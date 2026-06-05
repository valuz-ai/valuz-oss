"""Pydantic wire models for parser settings + setup endpoints.

Kept in one file because routes/system.py and routes/settings.py both
need to import them and we want to avoid a circular dep via the parser
module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Setup jobs ────────────────────────────────────────────────────


class SetupRequirementSchema(BaseModel):
    """Static metadata about a one-time setup operation. Mirrors
    ``ports.parser_plugin.SetupRequirement`` so the frontend can render
    the license / source dialog without round-tripping."""

    id: str
    label_zh: str
    kind: Literal["credential", "model_download"]
    network_required: bool = True
    size_bytes: int | None = None
    source: str | None = None
    license_name: str | None = None
    license_url: str | None = None
    # i18n key for ``label_zh`` (Phase 1 plugin model).
    label_key: str | None = None


class SetupJobStatusSchema(BaseModel):
    """Live status for one setup_id. Polled by the UI ~every 2s while a
    download is running."""

    setup_id: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    downloaded_bytes: int
    total_bytes: int | None
    error: str | None
    source: str | None
    started_at: int | None
    completed_at: int | None
    updated_at: int | None
    requirement: SetupRequirementSchema | None


class SetupJobListResponse(BaseModel):
    jobs: list[SetupJobStatusSchema]


class StartSetupJobRequest(BaseModel):
    """POST body for ``/v1/system/parser/setup/{id}/start``.

    The user must affirmatively check the license + source — the server
    refuses the request otherwise. This is the contractual boundary
    that prevents any "silent download" path."""

    accept_license: bool = Field(
        ...,
        description="User has read and accepts the listed license terms.",
    )
    confirmed_source: str = Field(
        ...,
        description="Source string from the surfaced requirement (e.g. 'modelscope_official').",
    )


# ── Plugin descriptor + capability status ────────────────────────


class ConfigFieldSchema(BaseModel):
    key: str
    label_zh: str
    type: Literal["string", "secret", "bool", "select", "number"]
    required: bool = False
    default: str | bool | int | float | None = None
    placeholder: str | None = None
    help_zh: str | None = None
    help_url: str | None = None
    # i18n keys (Phase 1 plugin model). Frontend prefers ``*_key`` over
    # ``*_zh``; the inline strings stay as fallback.
    label_key: str | None = None
    help_key: str | None = None
    placeholder_key: str | None = None
    options: list[tuple[str, str]] | None = None
    # Parallel to ``options`` when set (same length); each entry, if
    # non-null, is the i18n key for the corresponding option's label.
    option_keys: list[str | None] | None = None


class PluginCapabilityStatusSchema(BaseModel):
    """A single (kind, effective-status) pair the UI renders for one
    plugin. ``status`` is the *runtime* status (resolved from settings +
    setup job state), not the static descriptor."""

    kind: str
    status: Literal["ready", "needs_setup", "unavailable"]
    setup: SetupRequirementSchema | None = None
    reason_zh: str | None = None


class PluginDescriptorSchema(BaseModel):
    id: str
    name_zh: str
    description_zh: str
    mode: Literal["sync", "async_poll"]
    capabilities: list[PluginCapabilityStatusSchema]
    config_schema: list[ConfigFieldSchema]
    supported_kinds: list[str]
    is_configured: bool = Field(
        description="Whether the user's stored config is enough to run this plugin."
    )
    requires_secret: bool
    # i18n keys (Phase 1 plugin model). Frontend prefers ``*_key`` and
    # falls back to ``*_zh`` on miss.
    name_key: str | None = None
    description_key: str | None = None
    i18n_namespace: str | None = None
    # Sort weight for the parser settings UI. Lower = earlier.
    sort_weight: int = 50


class PluginsListResponse(BaseModel):
    plugins: list[PluginDescriptorSchema]


# ── Routing (primary plugin + by-kind overrides) ────────────────


class ParserRoutingResponse(BaseModel):
    primary_plugin_id: str
    by_kind: dict[str, str]
    fallback_to_local_on_error: bool
    # Server-rendered preview of what the router will actually do for
    # each known kind given the current settings — saves the UI from
    # duplicating the gate logic.
    effective_by_kind: dict[str, str]
    locked_kinds: list[str]


class ParserRoutingPatchRequest(BaseModel):
    primary_plugin_id: str | None = Field(default=None, min_length=1)
    by_kind: dict[str, str] | None = None
    fallback_to_local_on_error: bool | None = None


# ── Per-plugin user config ──────────────────────────────────────


class PluginConfigResponse(BaseModel):
    plugin_id: str
    enabled: bool
    has_secret: bool = Field(
        description=(
            "True if the secret_ref points at a stored value. The plaintext "
            "API key is never returned over HTTP."
        )
    )
    options: dict[str, Any]


class PluginConfigPatchRequest(BaseModel):
    enabled: bool | None = None
    # ``secret`` is the *plaintext* API key. The server hashes it into
    # the secret store and persists only the ref. Pass an empty string
    # to clear.
    secret: str | None = None
    options: dict[str, Any] | None = None


class PluginTestResponse(BaseModel):
    ok: bool
    plugin_id: str
    error: str | None = None
    latency_ms: int | None = None
