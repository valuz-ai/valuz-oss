"""Port: overlay-contributed LLM providers (not user-configured).

OSS mode: the registry is empty, so ``providersApi.list()`` only returns
user-configured providers (api_key / oauth) from the database. The
commercial overlay registers a ``valuz-channel`` system provider via
``get_llm_registry().register(...)`` in its app factory.

A system provider is read-only from the user's perspective — it has no
edit/delete actions and no key-input UI. Credentials are owned by the
overlay and supplied per resolve via the ``headers`` callable.

Resolution: when ``provider_resolver.resolve_model_provider`` looks up
an id that's not in the user provider table, it consults this registry.
A hit constructs a kernel ``ModelProvider`` with descriptor-supplied
``api_base`` + ``api_protocol`` and the bearer token returned by
``headers()['Authorization']``.

UI surface: the providers list endpoint maps each descriptor to a
``ProviderListItem`` with ``source="system"``, ``deletable=False``,
``credential_source="system_managed"``. SettingsPage uses ``source``
to hide edit/delete/key-input controls and show an availability badge.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SystemLLMProvider:
    """An LLM provider contributed by an overlay (not stored in user table).

    The descriptor carries everything the runtime needs to talk to the
    upstream gateway and everything the UI needs to render a card.
    Credentials are dynamic — ``headers()`` is invoked at resolve time
    so the overlay can pull a per-request JWT from a ``ContextVar``.

    Attributes:
        id: Stable identifier, e.g. ``"valuz-channel"``. Must be unique
            across user providers and other system providers.
        name: Display name shown on the providers list card.
        provider_kind: Maps to the OSS ``provider_kind`` column so the
            list/detail layer can colocate system providers next to
            user providers without a separate render path. For Valuz
            channel use ``"system"`` — provider_resolver short-circuits
            on this value, bypassing the descriptor map.
        runtime_provider: One of ``"claude_agent" | "codex" | "deepagents"``.
            Drives ``derive_runtime_provider`` for this id.
        api_protocol: Kernel underscore form — one of ``"anthropic" |
            "openai_completion" | "openai_response" | "gemini"``. Threaded
            straight into ``ModelProvider.api_protocol``.
        api_base: OpenAI-compatible (or Anthropic-compatible) base URL.
            Used as ``ModelProvider.base_url``.
        model_options: Static model id list shown in the picker. Use
            ``list_models`` for dynamic catalogs (Phase 2).
        default_model: Default model id when the user selects this
            provider without picking a model.
        headers: Per-resolve callable returning HTTP headers. The bearer
            token from ``Authorization`` is extracted into ``api_key``.
            Any other headers are not threaded through (kernel runtime
            uses SDKs that don't expose per-call extra-headers); the
            gateway must accept missing optional headers.
        enabled: Per-request callable. Returns ``False`` when the user
            is not authenticated; UI shows ``unavailable_reason``.
        unavailable_reason: Optional message shown next to the disabled
            badge. ``None`` = no reason given.
        list_models: Optional dynamic model lister (Phase 2).
    """

    id: str
    name: str
    provider_kind: str
    runtime_provider: str
    api_protocol: str
    api_base: str
    model_options: tuple[str, ...] = ()
    default_model: str | None = None

    headers: Callable[[], dict[str, str]] = field(default_factory=lambda: lambda: {}, repr=False)
    enabled: Callable[[], bool] = field(default_factory=lambda: lambda: True, repr=False)
    unavailable_reason: Callable[[], str | None] = field(
        default_factory=lambda: lambda: None, repr=False
    )
    list_models: Callable[[], Awaitable[list[str]] | list[str]] | None = None


class SystemProviderImmutable(RuntimeError):  # noqa: N818 — domain error, not Error-suffixed
    """Raised when a write op targets a registry-backed system provider.

    Carries the offending ``provider_id`` so the route layer can surface
    it. Mapped to HTTP 409 by the providers router.
    """

    def __init__(self, provider_id: str) -> None:
        super().__init__(
            f"provider {provider_id!r} is system-managed and cannot be edited or deleted"
        )
        self.provider_id = provider_id


class LLMProviderRegistry(Protocol):
    """Process-wide registry for overlay-contributed system providers."""

    def register(self, p: SystemLLMProvider) -> None: ...
    def unregister(self, provider_id: str) -> None: ...
    def all(self) -> Iterable[SystemLLMProvider]: ...
    def get(self, provider_id: str) -> SystemLLMProvider | None: ...
    def clear(self) -> None: ...


class _InMemoryRegistry:
    """Default registry — overlay populates at app-factory time.

    Not thread-safe by design: registration happens once at startup
    before any worker threads exist. ``clear()`` is for tests only.
    """

    def __init__(self) -> None:
        self._items: dict[str, SystemLLMProvider] = {}

    def register(self, p: SystemLLMProvider) -> None:
        if p.id in self._items:
            raise ValueError(f"system llm provider {p.id!r} already registered")
        self._items[p.id] = p

    def unregister(self, provider_id: str) -> None:
        self._items.pop(provider_id, None)

    def all(self) -> Iterable[SystemLLMProvider]:
        return tuple(self._items.values())

    def get(self, provider_id: str) -> SystemLLMProvider | None:
        return self._items.get(provider_id)

    def clear(self) -> None:
        self._items.clear()


_registry: LLMProviderRegistry = _InMemoryRegistry()


def get_llm_registry() -> LLMProviderRegistry:
    return _registry


def set_llm_registry(reg: LLMProviderRegistry) -> None:
    """Replace the registry (tests + advanced overlays)."""
    global _registry
    _registry = reg


__all__ = [
    "LLMProviderRegistry",
    "SystemLLMProvider",
    "SystemProviderImmutable",
    "get_llm_registry",
    "set_llm_registry",
]
