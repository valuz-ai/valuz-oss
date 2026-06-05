"""Top-level ``ParserBackend`` implementation that dispatches to plugins.

The router classifies an input file into a coarse ``kind``, then looks up
the *effective* plugin via:

1. ``parser.by_kind`` override (if present and registered)
2. ``parser.primary_plugin_id`` (capability-gated to ``supported_kinds``)
3. ``light_local`` as the safe fallback

A capability gate sits on top: if the plugin claims ``kind`` but its
``PluginCapability.status`` is still ``needs_setup`` (no API key, model
not downloaded, etc.), the router demotes to LightLocal with
``route_reason="capability_gate"``.

Runtime fallback (cloud plugin throws) lands in PR-3 alongside the cloud
plugin implementations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from valuz_agent.modules.parser.registry import (
    LIGHT_LOCAL_PLUGIN_ID,
    ParserPluginRegistry,
)
from valuz_agent.ports.parser_backend import ParseOptions, ParserBackend, ParseResult
from valuz_agent.ports.parser_plugin import (
    CapabilityStatus,
    ParserPlugin,
    ParserPluginConfig,
    SecretResolver,
)

if TYPE_CHECKING:
    from valuz_agent.modules.settings.parser_routing import ParserRoutingConfig

logger = logging.getLogger(__name__)


def _drive_async_parse_sync(
    backend: ParserBackend,
    file_path: str,
    options: ParseOptions | None,
) -> ParseResult:
    """Run an async-only backend's ``parse`` from a sync caller.

    Two cases:

    - **No running loop** (true sync context / tests): ``asyncio.run`` it.
    - **Already inside a running loop** (the docs reindex / rescan worker hosts
      its own loop in a daemon thread): we CANNOT nest ``asyncio.run`` — it
      raises "asyncio.run() cannot be called from a running event loop". And
      for ASYNC_POLL backends (PaddleOCR / MinerU) the parse depends on the
      ``PollingScheduler`` whose tick + awaiter futures live on the **main**
      app loop, so the coroutine must run *there*. Dispatch it onto the
      scheduler's loop via ``run_coroutine_threadsafe`` and block this worker
      thread on the result.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(backend.parse(file_path, options))

    # Inside a worker loop. Async-poll backends expose ``_scheduler`` whose
    # ``loop`` is the main app loop the tick runs on.
    scheduler = getattr(backend, "_scheduler", None)
    main_loop = getattr(scheduler, "loop", None)
    if main_loop is not None and not main_loop.is_closed():
        fut = asyncio.run_coroutine_threadsafe(backend.parse(file_path, options), main_loop)
        return fut.result()

    raise RuntimeError(
        "async parser backend requires a running PollingScheduler loop "
        "to run from a sync worker thread"
    )


_PDF: Final = frozenset({".pdf"})
_IMAGE: Final = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp", ".gif", ".jp2"})
_OFFICE: Final = frozenset({".doc", ".docx", ".ppt", ".pptx"})
_SPREADSHEET: Final = frozenset({".xls", ".xlsx"})
_WEB: Final = frozenset({".html", ".htm"})
_TEXT: Final = frozenset({".md", ".txt", ".csv", ".json", ".xml"})


def classify(file_path: str | Path) -> str:
    """Map a file path to a router ``kind``.

    Unknown extensions fall through to ``"text"`` (the cheapest, safest
    bucket) — the underlying parser will still produce an "unsupported
    extension" markdown response. Using ``"text"`` here keeps the router
    from blowing up on edge-case uploads.
    """
    ext = Path(file_path).suffix.lower()
    if ext in _PDF:
        return "pdf"
    if ext in _IMAGE:
        return "image"
    if ext in _OFFICE:
        return "office"
    if ext in _SPREADSHEET:
        return "spreadsheet"
    if ext in _WEB:
        return "web"
    return "text"


class _NullSecretResolver(SecretResolver):
    """PR-1 stub. PR-2 swaps this for a real ``FileSecretStore`` adapter."""

    def resolve(self, secret_ref: str | None) -> str | None:
        return None


class ParserRouter(ParserBackend):
    """``ParserBackend`` that fans out to plugins via the registry.

    Resolution rules (in order):

    1. ``parser.by_kind[kind]`` → use that plugin if registered.
    2. ``parser.primary_plugin_id`` → use it if the plugin supports
       ``kind`` AND its capability for ``kind`` is ``ready``.
    3. ``light_local`` fallback (always supports every kind via
       MarkItDown / pymupdf4llm / RapidOCR / markdownify).

    Plus a setup gate: if the resolved plugin's capability is
    ``needs_setup`` (e.g. RapidOCR model not downloaded yet), raise
    ``ParserCapabilityNotReady`` — the docs service translates that into
    a friendly error pointing the user to the setup endpoint.
    """

    def __init__(
        self,
        registry: ParserPluginRegistry,
        secret_resolver: SecretResolver | None = None,
        routing_config: ParserRoutingConfig | None = None,
        setup_complete_probe: Callable[[str], bool] | None = None,
    ) -> None:
        from valuz_agent.modules.settings.parser_routing import DEFAULT_ROUTING_CONFIG

        self._registry = registry
        self._secrets = secret_resolver or _NullSecretResolver()
        # Immutable routing snapshot resolved once per request by the caller
        # (``deps.get_document_service``). The hot per-parse path reads it
        # in-memory — no DB, callable from both ``parse`` and ``parse_sync``.
        # Defaults (light_local primary, fallback on) when not wired.
        self._routing = routing_config or DEFAULT_ROUTING_CONFIG
        # Optional probe ``setup_id -> bool`` for the capability gate.
        # When None the gate uses only the static descriptor status
        # (i.e. PR-1 behavior — no runtime upgrade).
        self._setup_complete_probe = setup_complete_probe

    # --- ParserBackend ----------------------------------------------------

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        kind = classify(file_path)
        plugin, route_reason = self._resolve_plugin(kind)
        try:
            backend = plugin.build(self._config_for(plugin.descriptor.id), self._secrets)
            result = await backend.parse(file_path, options)
        except Exception as exc:  # noqa: BLE001
            return await self._runtime_fallback_async(
                file_path=file_path,
                options=options,
                failing_plugin_id=plugin.descriptor.id,
                exc=exc,
            )
        return self._annotate(result, plugin_id=plugin.descriptor.id, route_reason=route_reason)

    def parse_sync(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        """Fast-path mirror of ``parse`` for sync callers.

        ``DocumentLibraryService`` duck-types ``parse_sync`` to avoid the
        per-call event-loop creation cost. Backends exposing a ``parse_sync``
        (LightLocal) forward directly; async-only backends are driven via
        :func:`_drive_async_parse_sync`, which handles the case where the
        caller is *already* inside a running loop (the docs reindex/rescan
        worker hosts its own loop) — there, ASYNC_POLL backends
        (PaddleOCR / MinerU) must run on the main PollingScheduler loop, not
        a nested ``asyncio.run`` (which raises "cannot be called from a
        running event loop").
        """
        kind = classify(file_path)
        plugin, route_reason = self._resolve_plugin(kind)
        try:
            backend = plugin.build(self._config_for(plugin.descriptor.id), self._secrets)
            sync_fn = getattr(backend, "parse_sync", None)
            if callable(sync_fn):
                result = sync_fn(file_path, options)
            else:
                result = _drive_async_parse_sync(backend, file_path, options)
        except Exception as exc:  # noqa: BLE001
            return self._runtime_fallback_sync(
                file_path=file_path,
                options=options,
                failing_plugin_id=plugin.descriptor.id,
                exc=exc,
            )
        return self._annotate(result, plugin_id=plugin.descriptor.id, route_reason=route_reason)

    async def health_check(self) -> bool:
        # PR-1: as long as the local plugin's backend is healthy, we are.
        plugin = self._registry.try_get(LIGHT_LOCAL_PLUGIN_ID)
        if plugin is None:
            return False
        backend = plugin.build(ParserPluginConfig(plugin_id=LIGHT_LOCAL_PLUGIN_ID), self._secrets)
        return await backend.health_check()

    @property
    def capabilities(self) -> set[str]:
        # Union of all registered plugins' supported kinds (a strict
        # superset of "what the router can route today"). UIs that ask
        # "do we support image at all?" want this union, not the active
        # plugin's slice.
        out: set[str] = set()
        for plugin in self._registry:
            out.update(plugin.descriptor.supported_kinds)
        return out

    @property
    def strategy_name(self) -> str:
        return "parser_router"

    def expected_plugin_id_for_kind(self, kind: str) -> str:
        """Return the plugin id this router *would* pick for ``kind``
        right now — same decision ``_resolve_plugin`` makes at parse
        time, including the capability-gate demotion to light_local
        when the user's choice isn't ready.

        Used by the docs rescan loop to detect "the configured engine
        for this doc's kind differs from the engine that last parsed
        it" and requeue affected docs. Living here keeps the routing
        decision in one place — duplicating the by_kind / primary_id /
        ready-check logic in docs service would diverge as the router
        evolves.
        """
        plugin, _reason = self._resolve_plugin(kind)
        return plugin.descriptor.id

    # --- helpers ---------------------------------------------------------

    def _resolve_plugin(self, kind: str) -> tuple[ParserPlugin, str]:
        """Resolve the effective plugin for ``kind``.

        Returns ``(plugin, route_reason)``. The reason captures *why*
        this plugin was chosen so it can be stamped on ``ParseResult.metadata``
        for observability:

        - ``"primary"`` — the user's chosen plugin handles this kind directly.
        - ``"capability_gate"`` — the user's plugin doesn't support this
          kind (or the capability is still ``needs_setup``); demoted to
          LightLocal.
        - ``"runtime_fallback"`` — applied later (after the plugin actually
          raises) inside ``_runtime_fallback_*``; never returned here.
        """
        # Locked kinds (text/plain) always route to LightLocal. The
        # settings layer already strips these from ``by_kind`` but be
        # defensive at the gate too.
        from valuz_agent.modules.settings.parser_routing import LOCKED_LOCAL_KINDS

        local = self._registry.get(LIGHT_LOCAL_PLUGIN_ID)

        if kind in LOCKED_LOCAL_KINDS:
            return local, "primary"

        explicit_id = self._routing.by_kind.get(kind)
        candidate_id = explicit_id or self._routing.primary_plugin_id

        # LightLocal as the effective choice is always "primary" — it's the
        # universal local parser, never gated against itself (preserves the
        # no-settings-wired default behavior).
        if candidate_id == LIGHT_LOCAL_PLUGIN_ID:
            return local, "primary"

        candidate = self._registry.try_get(candidate_id)
        if candidate is None:
            return local, "capability_gate"

        # Capability gate: does this plugin claim ``kind`` AND is its
        # capability effectively ready?
        if not self._kind_is_ready(candidate, kind):
            return local, "capability_gate"

        return candidate, "primary"

    def _kind_is_ready(self, plugin: ParserPlugin, kind: str) -> bool:
        for cap in plugin.descriptor.capabilities:
            if cap.kind != kind:
                continue
            if cap.status == CapabilityStatus.READY:
                return True
            if cap.status == CapabilityStatus.UNAVAILABLE:
                return False
            # NEEDS_SETUP — resolution depends on the *kind* of setup:
            if cap.setup is None:
                return False
            if cap.setup.kind == "credential":
                # Credential gate: ready iff the user has entered the
                # API key (i.e. ``plugin_configs[id].secret_ref`` is
                # set and ``enabled=True``). The setup-job probe is
                # irrelevant — credential-type setups have no marker.
                cfg = self._config_for(plugin.descriptor.id)
                if not cfg.enabled:
                    return False
                requires_secret = any(f.type == "secret" for f in plugin.descriptor.config_schema)
                if requires_secret and not cfg.secret_ref:
                    return False
                return True
            if cap.setup.kind == "model_download":
                # Marker-on-disk gate via the SetupJob controller probe.
                # Probe failures treated as "still needs setup" — safer side.
                return self._setup_complete_probe is not None and self._setup_complete_probe(
                    cap.setup.id
                )
            return False
        return False

    def _config_for(self, plugin_id: str) -> ParserPluginConfig:
        """Pull this plugin's stored user config (enabled / secret_ref /
        options) from the in-memory routing snapshot so ``plugin.build`` has
        the data it needs. Returns an empty config when the plugin has no
        stored config yet."""
        data = self._routing.plugin_config(plugin_id)
        return ParserPluginConfig(
            plugin_id=plugin_id,
            enabled=bool(data.get("enabled", False)),
            secret_ref=data.get("secret_ref"),
            options=dict(data.get("options", {})),
        )

    def _fallback_enabled(self) -> bool:
        """Whether ``parser.fallback_to_local_on_error`` is set. Defaults to
        True (``DEFAULT_FALLBACK_ON_ERROR``) — a single cloud failure should
        not nuke a user's document."""
        return self._routing.fallback_to_local_on_error

    def _failure_result(self, *, plugin_id: str, exc: Exception) -> ParseResult:
        """Convert a parse exception into a ``ParseResult`` that signals
        failure to ``DocumentLibraryService`` via the standard
        ``metadata['error']`` channel.

        Why we don't just re-raise: the docs service expects parsers to
        ALWAYS return a ``ParseResult``; an exception would leave the
        doc stuck in ``processing`` (no surrounding try/except at the
        call site). All bundled LightLocal branches follow this
        convention — the router must too.
        """
        msg = (str(exc) or exc.__class__.__name__)[:400]
        return ParseResult(
            markdown=f"*{plugin_id} parse failed: {msg}*",
            metadata={
                "engine": plugin_id,
                "error": msg,
                "route_reason": "runtime_fallback",
            },
        )

    async def _runtime_fallback_async(
        self,
        *,
        file_path: str,
        options: ParseOptions | None,
        failing_plugin_id: str,
        exc: Exception,
    ) -> ParseResult:
        """Async path runtime fallback. Returns a failure ParseResult if
        fallback is disabled OR the failing plugin already IS LightLocal
        — we can't fall back any lower than the local engine."""
        if failing_plugin_id == LIGHT_LOCAL_PLUGIN_ID or not self._fallback_enabled():
            logger.warning(
                "parser plugin %s failed (%s: %s); fallback disabled — surfacing failure",
                failing_plugin_id,
                type(exc).__name__,
                (str(exc) or "<no message>")[:240],
            )
            return self._failure_result(plugin_id=failing_plugin_id, exc=exc)
        logger.warning(
            "parser plugin %s failed (%s: %s); falling back to %s",
            failing_plugin_id,
            type(exc).__name__,
            (str(exc) or "<no message>")[:240],
            LIGHT_LOCAL_PLUGIN_ID,
        )
        local = self._registry.get(LIGHT_LOCAL_PLUGIN_ID)
        backend = local.build(self._config_for(LIGHT_LOCAL_PLUGIN_ID), self._secrets)
        result = await backend.parse(file_path, options)
        return self._annotate(
            result,
            plugin_id=LIGHT_LOCAL_PLUGIN_ID,
            route_reason="runtime_fallback",
            fallback_from=failing_plugin_id,
            fallback_error=str(exc) or exc.__class__.__name__,
        )

    def _runtime_fallback_sync(
        self,
        *,
        file_path: str,
        options: ParseOptions | None,
        failing_plugin_id: str,
        exc: Exception,
    ) -> ParseResult:
        """Sync path runtime fallback — same contract as the async
        version but routes through ``parse_sync`` when available."""
        if failing_plugin_id == LIGHT_LOCAL_PLUGIN_ID or not self._fallback_enabled():
            logger.warning(
                "parser plugin %s failed (%s: %s); fallback disabled — surfacing failure",
                failing_plugin_id,
                type(exc).__name__,
                (str(exc) or "<no message>")[:240],
            )
            return self._failure_result(plugin_id=failing_plugin_id, exc=exc)
        logger.warning(
            "parser plugin %s failed (%s: %s); falling back to %s",
            failing_plugin_id,
            type(exc).__name__,
            (str(exc) or "<no message>")[:240],
            LIGHT_LOCAL_PLUGIN_ID,
        )
        local = self._registry.get(LIGHT_LOCAL_PLUGIN_ID)
        backend = local.build(self._config_for(LIGHT_LOCAL_PLUGIN_ID), self._secrets)
        sync_fn = getattr(backend, "parse_sync", None)
        if callable(sync_fn):
            result = sync_fn(file_path, options)
        else:
            result = _drive_async_parse_sync(backend, file_path, options)
        return self._annotate(
            result,
            plugin_id=LIGHT_LOCAL_PLUGIN_ID,
            route_reason="runtime_fallback",
            fallback_from=failing_plugin_id,
            fallback_error=str(exc) or exc.__class__.__name__,
        )

    @staticmethod
    def _annotate(
        result: ParseResult,
        *,
        plugin_id: str,
        route_reason: str,
        fallback_from: str | None = None,
        fallback_error: str | None = None,
    ) -> ParseResult:
        """Stamp routing provenance into ``result.metadata`` without losing
        the underlying engine label (e.g. ``pymupdf4llm`` / ``rapidocr``).

        Contract for downstream consumers:

        - ``metadata["engine"]`` keeps the per-format engine name (the
          existing convention — preserved for back-compat with
          ``DocumentRecordRow.parser_mode``).
        - ``metadata["plugin_id"]`` records which plugin owned the call.
        - ``metadata["route_reason"]`` ∈ {primary, capability_gate,
          runtime_fallback}.
        - ``metadata["fallback_from"]`` set only when the router demoted
          the call away from the user's chosen plugin.
        - ``metadata["fallback_error"]`` is the original error from the
          plugin that triggered the demotion — surfaces "why did MinerU
          fail" in the document view without requiring a log dive.
        """
        annotated = dict(result.metadata)
        annotated.setdefault("plugin_id", plugin_id)
        annotated.setdefault("route_reason", route_reason)
        if fallback_from is not None:
            annotated.setdefault("fallback_from", fallback_from)
        if fallback_error is not None:
            annotated.setdefault("fallback_error", fallback_error[:240])
        return ParseResult(
            markdown=result.markdown,
            page_count=result.page_count,
            metadata=annotated,
        )


__all__ = ["ParserRouter", "classify"]
