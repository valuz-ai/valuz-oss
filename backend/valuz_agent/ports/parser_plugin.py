"""Plugin descriptor + capability model layered on top of ``ParserBackend``.

A ``ParserPlugin`` is the user-facing unit a settings UI can list, configure
and select. Each plugin produces a concrete ``ParserBackend`` instance via
``build()``.

Why this layer exists on top of ``ParserBackend``:

- ``ParserBackend`` is the runtime contract — "given a file, produce
  markdown". It is intentionally minimal so ``DocumentLibraryService`` does
  not care which engine ran.
- ``ParserPlugin`` adds the *static* metadata the host needs to render a
  configuration page, gate by capability, and orchestrate one-time setup
  (API-key entry, local model download) before the backend is usable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from valuz_agent.ports.parser_backend import ParserBackend


class ParserPluginMode(StrEnum):
    """How a plugin produces results."""

    SYNC = "sync"
    """In-process, blocking. ``ParserBackend.parse`` returns the result."""

    ASYNC_POLL = "async_poll"
    """Plugin submits a remote task and polls for completion via
    ``PollingScheduler``. ``ParserBackend.parse`` awaits the future."""


class CapabilityStatus(StrEnum):
    READY = "ready"
    NEEDS_SETUP = "needs_setup"
    UNAVAILABLE = "unavailable"


SetupKind = Literal["credential", "model_download"]
"""Distinguishes "needs user input" (API key) from "needs network +
authorization" (model file download). Drives UI widget selection."""


@dataclass(frozen=True)
class SetupRequirement:
    """Describes the one-time work a user must approve before a capability
    becomes usable. Rendered as a card in the settings UI.

    ``id`` is the stable key used by the setup-job endpoints
    (``/v1/system/parser/setup/{id}``).
    """

    id: str
    label_zh: str
    kind: SetupKind
    network_required: bool = True
    size_bytes: int | None = None
    source: str | None = None
    license_name: str | None = None
    license_url: str | None = None
    # Optional i18n key for ``label_zh``. Frontend prefers this over the
    # inline ``label_zh`` string; default fallback when missing.
    label_key: str | None = None


@dataclass(frozen=True)
class PluginCapability:
    """A single (file-kind, status) pair the plugin advertises.

    Kinds are coarse buckets the router uses to dispatch — they map roughly
    to file-extension families. The canonical kinds in v1 are:

    - ``"pdf"``      — .pdf
    - ``"image"``    — .png .jpg .jpeg .bmp .webp .gif .tiff .jp2
    - ``"office"``   — .doc .docx .ppt .pptx
    - ``"spreadsheet"`` — .xls .xlsx
    - ``"web"``      — .html .htm
    - ``"text"``     — .md .txt .csv .json .xml
    """

    kind: str
    status: CapabilityStatus
    setup: SetupRequirement | None = None
    reason_zh: str | None = None


ConfigFieldType = Literal["string", "secret", "bool", "select", "number"]


@dataclass(frozen=True)
class ConfigField:
    """A single settings-form field. Drives ``DynamicConfigForm`` on the
    frontend — adding a new plugin should require no UI code.

    ``*_key`` fields (``label_key``, ``help_key``, ``placeholder_key``)
    point at i18n keys that the plugin contributes via its own locale
    JSON (see ``frontend/packages/parser-plugins/<id>/locale.*.json``
    and ``registerLocaleNamespace``). When ``*_key`` is set, the
    frontend resolves it through ``t()`` and only falls back to the
    inline ``*_zh`` string if the lookup misses. Plugins shipping i18n
    should always set keys; ``*_zh`` is kept as a back-compat path for
    legacy / third-party plugins that haven't migrated yet."""

    key: str
    label_zh: str
    type: ConfigFieldType
    required: bool = False
    default: str | bool | int | float | None = None
    placeholder: str | None = None
    help_zh: str | None = None
    label_key: str | None = None
    help_key: str | None = None
    placeholder_key: str | None = None
    # Optional doc URL — frontend renders this as a clickable link
    # next to ``help_zh``. Use for "where to get a token" pointers so
    # the user can jump directly to the issuer's dashboard.
    help_url: str | None = None
    options: tuple[tuple[str, str], ...] | None = None
    # Optional i18n keys for the ``options`` labels (parallel to
    # ``options``, same length). Each entry replaces the corresponding
    # ``options[i][1]`` label for rendering. Plugins shipping i18n
    # should set this; the wire format keeps ``options`` as-is so the
    # client can still render the inline label when the key is missing.
    option_keys: tuple[str | None, ...] | None = None


@dataclass(frozen=True)
class SplitPolicy:
    """Per-plugin file-splitting limits, surfaced statically on the
    descriptor so the splitter (``modules.parser.splitter``) can
    pre-split oversized files before invoking the plugin. ``None`` for
    either field means no limit.

    Defined here (in ports) rather than in ``modules.parser.splitter``
    so the descriptor can reference it without a runtime import cycle.
    """

    max_pages: int | None = None
    """For PDF: split into parts of at most this many pages before
    handing to the plugin."""

    max_bytes: int | None = None
    """Hard reject above this size — splitting on byte boundaries
    doesn't make sense for structured formats. Today unused; reserved
    for future plugins that may want byte-level gates."""


@dataclass(frozen=True)
class ParserPluginDescriptor:
    """Static identity + advertised capabilities of a plugin.

    A descriptor is built once at module import time. Runtime state
    (whether the API key is configured, whether the model is downloaded)
    lives in ``SettingsService`` / ``SetupJob`` tables; the router queries
    those when it composes the *effective* status for a UI list.
    """

    id: str
    name_zh: str
    description_zh: str
    mode: ParserPluginMode
    capabilities: tuple[PluginCapability, ...]
    config_schema: tuple[ConfigField, ...] = field(default_factory=tuple)
    fallback_policy: Literal["allow_local_fallback", "strict"] = "allow_local_fallback"
    # i18n + ordering surface (Phase 1 plugin model). All four fields
    # are optional so existing plugins (third-party or in-tree before
    # migration) keep working — ``name_zh`` / ``description_zh`` are
    # the fallback the frontend uses when the keys miss or aren't set.
    # ``i18n_namespace`` defaults to ``parser_<id>`` on the wire; the
    # frontend uses it only for diagnostics today.
    name_key: str | None = None
    description_key: str | None = None
    i18n_namespace: str | None = None
    # Sort weight for the settings UI. Lower = earlier. ``light_local``
    # uses 10 (always-available fallback first); cloud plugins land in
    # the 20-40 range; ``valuz_ocr`` placeholder uses 90 (last). This
    # is on the descriptor (not the frontend registry) so there's one
    # source of truth.
    sort_weight: int = 50
    split_policy: SplitPolicy = field(default_factory=SplitPolicy)

    @property
    def supported_kinds(self) -> frozenset[str]:
        """The set of file-kinds this plugin claims to handle at all (any
        status). The router's capability gate uses this to decide whether
        to route to this plugin in the first place."""
        return frozenset(c.kind for c in self.capabilities)


@dataclass(frozen=True)
class ParserPluginConfig:
    """User-provided configuration for one plugin instance.

    Secrets are never stored here in plaintext — the ``secret_ref`` points
    into the ``SecretStorePort`` and is resolved by ``ParserPlugin.build``
    at request time.
    """

    plugin_id: str
    enabled: bool = False
    secret_ref: str | None = None
    options: dict[str, str | bool | int | float] = field(default_factory=dict)


class ParserPlugin(Protocol):
    """Factory for ``ParserBackend`` instances of a particular kind.

    Implementations are stateless and registered once at app startup. The
    router calls ``build(config, secret_store)`` each time it needs to
    execute a parse — implementations are expected to cache the resulting
    backend internally if construction is expensive."""

    @property
    def descriptor(self) -> ParserPluginDescriptor: ...

    def build(
        self,
        config: ParserPluginConfig,
        secret_resolver: SecretResolver,
    ) -> ParserBackend: ...


class SecretResolver(Protocol):
    """Indirection over ``SecretStorePort`` so plugins do not pull in
    ``infra.secret_store`` directly. Implemented in
    ``modules/parser/router.py``."""

    def resolve(self, secret_ref: str | None) -> str | None: ...


class ParserCapabilityNotReady(Exception):  # noqa: N818
    """Raised by the router when the selected plugin's capability for a
    given kind is still ``needs_setup``. The router translates this into a
    user-facing error message on the document row pointing them to the
    setup endpoint.

    Domain-style naming (no ``Error`` suffix) follows the existing
    convention used in ``modules/*/errors.py`` files."""

    def __init__(
        self,
        *,
        plugin_id: str,
        kind: str,
        setup_id: str | None,
        hint_zh: str,
    ) -> None:
        super().__init__(f"plugin={plugin_id} kind={kind} setup_id={setup_id} — {hint_zh}")
        self.plugin_id = plugin_id
        self.kind = kind
        self.setup_id = setup_id
        self.hint_zh = hint_zh
