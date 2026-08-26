"""Runtime-side safety helpers for the desktop egress rollout.

This module does not discover proxies or mutate ``os.environ``.  Electron owns
route resolution; runtimes only use these pure helpers to keep their loopback
adapter reachable and to enforce Phase-0 capability gates.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import stat
import sys
import time
from collections.abc import AsyncIterator, Mapping, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import IO, TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from src.core.types import Session

_LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")


def is_loopback_url(raw: str) -> bool:
    """Return whether an HTTP(S) base URL targets the local host."""
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or host is None:
            return False
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_strict_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_descriptor_expiry(value: Any) -> bool:
    now = int(time.time() * 1000)
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and now < value <= now + _MAX_LEASE_TTL_MS
    )


def merge_loopback_no_proxy(env: MutableMapping[str, str], base_url: str) -> None:
    """Merge loopback bypasses into both NO_PROXY spellings when required.

    Lowercase proxy variables win in several HTTP stacks, while GUI-launched
    applications commonly carry only uppercase variables.  Writing the same
    merged union to both names avoids either spelling accidentally routing the
    local model ingress back through the user's upstream proxy.  Existing user
    entries are preserved in first-seen order.
    """
    if not is_loopback_url(base_url):
        return

    entries: list[str] = []
    seen: set[str] = set()
    for value in (env.get("no_proxy", ""), env.get("NO_PROXY", "")):
        for raw in value.split(","):
            item = raw.strip()
            normalized = item.lower()
            if item and normalized not in seen:
                entries.append(item)
                seen.add(normalized)
    for item in _LOOPBACK_NO_PROXY:
        if item.lower() not in seen:
            entries.append(item)
            seen.add(item.lower())

    merged = ",".join(entries)
    env["no_proxy"] = merged
    env["NO_PROXY"] = merged


@dataclass(frozen=True)
class CredentialIsolationGate:
    eligible: bool
    reason: str | None = None


def claude_api_key_credential_gate(
    *,
    permission_mode: Literal["default", "auto_review", "full_access"],
    session_mode: Literal["default", "plan", "goal"],
) -> CredentialIsolationGate:
    """Phase-0 gate confirmed against bundled Claude Code 2.1.220.

    ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`` strips Anthropic credentials but
    forces the CLI's initial permission mode to ``default``.  Valuz's existing
    ``can_use_tool`` callback preserves ``default`` and ``full_access``
    semantics, but cannot reproduce Claude's proprietary ``auto`` classifier
    or an initial ``plan`` mode.  Those combinations must stay off the egress
    allowlist until a compatible isolation boundary is available.
    """
    if session_mode == "plan":
        return CredentialIsolationGate(False, "claude_api_key_plan_scrub_incompatible")
    if permission_mode == "auto_review":
        return CredentialIsolationGate(False, "claude_api_key_auto_review_scrub_incompatible")
    return CredentialIsolationGate(True)


@dataclass(frozen=True)
class EgressBootstrap:
    mode: Literal["auto", "direct"]
    control_endpoint: str
    bootstrap_token: str
    expires_at: int


@dataclass(frozen=True)
class ModelIngressDescriptor:
    kind: Literal["model_ingress"]
    base_url: str
    client_id: str
    expires_at: int
    supports_websocket: bool


@dataclass(frozen=True)
class ForwardProxyDescriptor:
    kind: Literal["forward_proxy"]
    proxy_url: str
    client_id: str
    expires_at: int


EgressDescriptor = ModelIngressDescriptor | ForwardProxyDescriptor
RuntimePhase = Literal[
    "runtime_init_started",
    "runtime_init",
    "thread_init_started",
    "thread_init",
    "dispatch_started",
    "dispatch",
    "model_first_event",
    "runtime_ready",
    "runtime_prepare_failed",
    "turn_complete",
    "interrupted",
]
_RUNTIME_PHASES = frozenset(
    {
        "runtime_init_started",
        "runtime_init",
        "thread_init_started",
        "thread_init",
        "dispatch_started",
        "dispatch",
        "model_first_event",
        "runtime_ready",
        "runtime_prepare_failed",
        "turn_complete",
        "interrupted",
    }
)

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_CODEX_SUBSCRIPTION_BASE_URL = "https://chatgpt.com/backend-api/codex"
_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_MAX_LEASE_TTL_MS = 24 * 60 * 60 * 1000
_MAX_BOOTSTRAP_BYTES = 16 * 1024


class EgressRegistrationError(RuntimeError):
    """Stable error without control response bodies or capability values."""


def _parse_bootstrap(payload: Mapping[str, Any]) -> EgressBootstrap:
    mode = payload.get("mode")
    endpoint = payload.get("controlEndpoint")
    token = payload.get("bootstrapToken")
    expires_at = payload.get("expiresAt")
    if mode not in {"auto", "direct"}:
        raise ValueError("invalid_egress_bootstrap_mode")
    if not isinstance(endpoint, str):
        raise ValueError("invalid_egress_control_endpoint")
    parsed = urlsplit(endpoint)
    try:
        loopback = parsed.hostname is not None and (
            parsed.hostname.lower() == "localhost"
            or ipaddress.ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        loopback = False
    if (
        parsed.scheme != "http"
        or not loopback
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("invalid_egress_control_endpoint")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("invalid_egress_bootstrap_token")
    if (
        not isinstance(expires_at, int)
        or expires_at <= int(time.time() * 1000)
        or expires_at > int(time.time() * 1000) + 24 * 60 * 60 * 1000
    ):
        raise ValueError("invalid_egress_bootstrap_expiry")
    return EgressBootstrap(
        mode=mode,
        control_endpoint=endpoint.rstrip("/"),
        bootstrap_token=token,
        expires_at=expires_at,
    )


class NetworkEgressRegistry:
    """In-memory runtime descriptors backed by Electron's control plane."""

    def __init__(self, bootstrap: EgressBootstrap) -> None:
        self.bootstrap = bootstrap
        self._descriptors: dict[str, EgressDescriptor] = {}
        self._client: httpx.AsyncClient | None = None
        self._lease_task: asyncio.Task[None] | None = None

    def start_keepalive(self) -> None:
        """Renew the in-memory control/client lease while the sidecar is alive."""
        if self._lease_task is None or self._lease_task.done():
            self._lease_task = asyncio.create_task(
                self._keepalive_loop(),
                name="valuz-egress-lease-keepalive",
            )

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.bootstrap.control_endpoint,
                headers={"authorization": f"Bearer {self.bootstrap.bootstrap_token}"},
                timeout=httpx.Timeout(5.0),
                trust_env=False,
            )
        return self._client

    def _active(self) -> None:
        if self.bootstrap.expires_at <= int(time.time() * 1000):
            raise EgressRegistrationError("egress_bootstrap_expired")

    async def renew_lease(self) -> None:
        """Extend the bootstrap and all active runtime capabilities in place."""
        self._active()
        client_ids = [descriptor.client_id for descriptor in self._descriptors.values()]
        try:
            response = await self._http_client().post(
                "/v1/lease/renew",
                json={"clientIds": client_ids},
            )
            if response.status_code != 200:
                raise EgressRegistrationError("egress_lease_renewal_rejected")
            expires_at = int(response.json()["expiresAt"])
        except EgressRegistrationError:
            raise
        except Exception as exc:
            raise EgressRegistrationError("egress_lease_renewal_failed") from exc
        now = int(time.time() * 1000)
        if expires_at <= now or expires_at > now + _MAX_LEASE_TTL_MS:
            raise EgressRegistrationError("invalid_egress_lease_expiry")
        self.bootstrap = replace(self.bootstrap, expires_at=expires_at)
        self._descriptors = {
            runtime_key: replace(descriptor, expires_at=expires_at)
            for runtime_key, descriptor in self._descriptors.items()
        }

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                remaining_ms = self.bootstrap.expires_at - int(time.time() * 1000)
                # Renew at roughly one-third of the remaining lease, capped so
                # clock changes and long app sessions cannot strand the sidecar.
                delay_seconds = max(1.0, min(60 * 60.0, remaining_ms / 3_000))
                await asyncio.sleep(delay_seconds)
                try:
                    await self.renew_lease()
                except EgressRegistrationError:
                    # A transient loopback/control restart gets another chance;
                    # callers still fail loud once the current lease expires.
                    await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            return

    async def _revoke_client_id(self, client_id: str) -> None:
        try:
            await self._http_client().delete(f"/v1/clients/{client_id}")
        except Exception:
            # Capability expiry remains the hard boundary if the control plane
            # disappeared during cleanup.
            return

    async def register_model_ingress(
        self,
        runtime_key: str,
        *,
        runtime: Literal["codex", "claude"],
        upstream_base_url: str,
        supports_websocket: bool,
    ) -> ModelIngressDescriptor:
        existing = self._descriptors.get(runtime_key)
        if isinstance(existing, ModelIngressDescriptor):
            return existing
        self._active()
        client_id = secrets.token_urlsafe(18)
        try:
            response = await self._http_client().post(
                "/v1/clients/model-ingress",
                json={
                    "clientId": client_id,
                    "runtime": runtime,
                    "upstreamBaseUrl": upstream_base_url,
                    "supportsWebSocket": supports_websocket,
                },
            )
            if response.status_code != 201:
                raise EgressRegistrationError("model_ingress_registration_rejected")
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("kind") != "model_ingress"
                or not isinstance(payload.get("baseUrl"), str)
                or not isinstance(payload.get("clientId"), str)
                or not _valid_descriptor_expiry(payload.get("expiresAt"))
                or not isinstance(payload.get("supportsWebSocket"), bool)
                or payload["supportsWebSocket"] is not supports_websocket
            ):
                await self._revoke_client_id(client_id)
                raise EgressRegistrationError("invalid_model_ingress_descriptor")
            descriptor = ModelIngressDescriptor(
                kind="model_ingress",
                base_url=payload["baseUrl"],
                client_id=payload["clientId"],
                expires_at=payload["expiresAt"],
                supports_websocket=payload["supportsWebSocket"],
            )
        except EgressRegistrationError:
            raise
        except Exception as exc:
            raise EgressRegistrationError("model_ingress_registration_failed") from exc
        ingress = urlsplit(descriptor.base_url)
        if (
            descriptor.client_id != client_id
            or ingress.scheme != "http"
            or not _is_strict_loopback_host(ingress.hostname)
            or ingress.port is None
            or ingress.username
            or ingress.password
            or ingress.path in {"", "/"}
            or ingress.query
            or ingress.fragment
        ):
            await self._revoke_client_id(client_id)
            raise EgressRegistrationError("invalid_model_ingress_descriptor")
        self._descriptors[runtime_key] = descriptor
        return descriptor

    async def register_forward_proxy(
        self,
        runtime_key: str,
        *,
        runtime: Literal["deepagents", "provider_test"],
    ) -> ForwardProxyDescriptor:
        existing = self._descriptors.get(runtime_key)
        if isinstance(existing, ForwardProxyDescriptor):
            return existing
        self._active()
        client_id = secrets.token_urlsafe(18)
        try:
            response = await self._http_client().post(
                "/v1/clients/forward-proxy",
                json={"clientId": client_id, "runtime": runtime},
            )
            if response.status_code != 201:
                raise EgressRegistrationError("forward_proxy_registration_rejected")
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("kind") != "forward_proxy"
                or not isinstance(payload.get("proxyUrl"), str)
                or not isinstance(payload.get("clientId"), str)
                or not _valid_descriptor_expiry(payload.get("expiresAt"))
            ):
                await self._revoke_client_id(client_id)
                raise EgressRegistrationError("invalid_forward_proxy_descriptor")
            descriptor = ForwardProxyDescriptor(
                kind="forward_proxy",
                proxy_url=payload["proxyUrl"],
                client_id=payload["clientId"],
                expires_at=payload["expiresAt"],
            )
        except EgressRegistrationError:
            raise
        except Exception as exc:
            raise EgressRegistrationError("forward_proxy_registration_failed") from exc
        proxy = urlsplit(descriptor.proxy_url)
        if (
            descriptor.client_id != client_id
            or proxy.scheme != "http"
            or not _is_strict_loopback_host(proxy.hostname)
            or proxy.port is None
            or not proxy.username
            or not proxy.password
            or proxy.path not in {"", "/"}
            or proxy.query
            or proxy.fragment
        ):
            await self._revoke_client_id(client_id)
            raise EgressRegistrationError("invalid_forward_proxy_descriptor")
        self._descriptors[runtime_key] = descriptor
        return descriptor

    async def revoke(self, runtime_key: str) -> None:
        descriptor = self._descriptors.pop(runtime_key, None)
        if descriptor is None:
            return
        await self._revoke_client_id(descriptor.client_id)

    async def record_runtime_phase(
        self,
        *,
        runtime_key: str,
        turn_attempt_id: str,
        phase: RuntimePhase,
        monotonic_ms: float,
    ) -> None:
        descriptor = self._descriptors.get(runtime_key)
        if descriptor is None:
            return
        try:
            await self._http_client().post(
                "/v1/runtime-phase",
                json={
                    "turnAttemptId": turn_attempt_id,
                    "clientId": descriptor.client_id,
                    "phase": phase,
                    "monotonicMs": monotonic_ms,
                },
            )
        except Exception:
            return

    async def close(self) -> None:
        self._descriptors.clear()
        lease_task = self._lease_task
        self._lease_task = None
        if lease_task is not None:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_registry: NetworkEgressRegistry | None = None
_required_unavailable = False
_desktop_control_token: str | None = None


def get_network_egress_registry() -> NetworkEgressRegistry | None:
    return _registry


def desktop_control_authorized(token: str | None) -> bool:
    """Validate the memory-only capability delivered by the desktop shell."""
    return (
        isinstance(token, str)
        and _desktop_control_token is not None
        and secrets.compare_digest(token, _desktop_control_token)
    )


async def prepare_runtime_egress(
    runtime_key: str,
    session: Session,
) -> EgressDescriptor | None:
    """Register the narrowest verified frontend for a runtime instance.

    A missing registry is the compatibility/headless path.  Runtime and
    authentication combinations that have not passed the Phase-0 protocol
    and credential-isolation spikes deliberately stay on that legacy path;
    they are never silently sent through a broader process proxy.
    """
    provider = session.model_provider
    runtime = session.runtime_provider
    ingress_runtime: Literal["codex", "claude"] | None = None
    upstream_base_url: str | None = None
    supports_websocket = False
    forward_runtime: Literal["deepagents"] | None = None

    if runtime == "codex":
        ingress_runtime = "codex"
        upstream_base_url = (
            _DEFAULT_CODEX_SUBSCRIPTION_BASE_URL
            if provider is None
            else provider.base_url or _DEFAULT_OPENAI_BASE_URL
        )
        # Keep the proxy-oriented canary on HTTPS streaming.  Codex retries a
        # WebSocket that closes before ``response.completed`` up to five times
        # before falling back, which turns a marginal proxy path into a
        # minute-long first response.  The model ingress remains capable of
        # WebSocket relay, but a runtime must pass the long-stream/reconnect
        # matrix before its registration is allowed to upgrade.
        supports_websocket = False
    elif runtime == "claude_agent":
        # Subscription OAuth stays inside Claude Code's native credential
        # store, so it needs no credential env scrub and is safe in every
        # permission/session mode. Explicit API-key providers still need the
        # locked CLI's scrub gate, re-checked on every client spawn below.
        if provider is not None:
            gate = claude_api_key_credential_gate(
                permission_mode=session.permission_mode,
                session_mode=session.mode,
            )
            if not gate.eligible:
                return None
        ingress_runtime = "claude"
        upstream_base_url = (
            _DEFAULT_ANTHROPIC_BASE_URL
            if provider is None
            else provider.base_url or _DEFAULT_ANTHROPIC_BASE_URL
        )
    elif runtime == "deepagents":
        # OpenAI and Anthropic both expose an explicit proxy hook in the
        # locked SDK versions.  Gemini's transport surface still needs a
        # verified no-global-env spike before it can join the allowlist.
        if provider is None or provider.api_protocol not in {
            "openai_completion",
            "anthropic",
        }:
            return None
        forward_runtime = "deepagents"
    else:
        return None

    registry = get_network_egress_registry()
    if registry is None:
        if _required_unavailable:
            raise EgressRegistrationError("egress_manager_unavailable")
        return None
    if ingress_runtime is not None and upstream_base_url is not None:
        return await registry.register_model_ingress(
            runtime_key,
            runtime=ingress_runtime,
            upstream_base_url=upstream_base_url,
            supports_websocket=supports_websocket,
        )
    assert forward_runtime is not None
    return await registry.register_forward_proxy(
        runtime_key,
        runtime=forward_runtime,
    )


async def record_runtime_egress_phase(
    runtime_key: str | None,
    turn_attempt_id: str | None,
    phase: str,
    *,
    enabled: bool = True,
) -> None:
    """Forward one allowlisted timing marker without leaking session data."""
    registry = get_network_egress_registry()
    if (
        not enabled
        or registry is None
        or runtime_key is None
        or turn_attempt_id is None
        or phase not in _RUNTIME_PHASES
    ):
        return
    await registry.record_runtime_phase(
        runtime_key=runtime_key,
        turn_attempt_id=turn_attempt_id,
        phase=cast(RuntimePhase, phase),
        monotonic_ms=time.monotonic() * 1000,
    )


@asynccontextmanager
async def provider_test_egress() -> AsyncIterator[ForwardProxyDescriptor | None]:
    """Lease a short-lived explicit proxy for one provider probe operation."""
    registry = get_network_egress_registry()
    if registry is None:
        if _required_unavailable:
            raise EgressRegistrationError("egress_manager_unavailable")
        yield None
        return
    runtime_key = f"provider-test-{secrets.token_urlsafe(18)}"
    descriptor = await registry.register_forward_proxy(
        runtime_key,
        runtime="provider_test",
    )
    try:
        yield descriptor
    finally:
        await registry.revoke(runtime_key)


def configure_network_egress(
    payload: Mapping[str, Any] | None,
    *,
    required_unavailable: bool = False,
) -> None:
    """Install a validated in-memory bootstrap, or disable the registry."""
    global _registry, _required_unavailable
    _registry = NetworkEgressRegistry(_parse_bootstrap(payload)) if payload else None
    _required_unavailable = payload is None and required_unavailable


async def replace_network_egress(
    payload: Mapping[str, Any] | None,
    *,
    required_unavailable: bool = False,
) -> None:
    """Replace the live desktop capability without restarting the backend.

    Runtime processes using the old descriptor must be closed by the caller
    before this function runs. The old registry is closed first so its lease
    task and loopback client cannot survive a mode transition.
    """
    previous = get_network_egress_registry()
    if previous is not None:
        await previous.close()
    configure_network_egress(payload, required_unavailable=required_unavailable)
    if registry := get_network_egress_registry():
        registry.start_keepalive()


def _consume_bootstrap_file(raw_path: str) -> str:
    """Read and unlink one private, regular rendezvous file without following links."""
    if not os.path.isabs(raw_path):
        raise RuntimeError("invalid_egress_bootstrap_file")
    parent = os.path.dirname(raw_path)
    try:
        parent_info = os.lstat(parent)
        file_info = os.lstat(raw_path)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_ISLNK(parent_info.st_mode)
            or parent_info.st_mode & 0o077
            or not stat.S_ISREG(file_info.st_mode)
            or stat.S_ISLNK(file_info.st_mode)
            or file_info.st_mode & 0o077
            or (
                hasattr(os, "getuid")
                and (parent_info.st_uid != os.getuid() or file_info.st_uid != os.getuid())
            )
            or file_info.st_nlink != 1
        ):
            raise RuntimeError("insecure_egress_bootstrap_file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(raw_path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != file_info.st_dev
                or opened.st_ino != file_info.st_ino
            ):
                raise RuntimeError("egress_bootstrap_file_changed")
            chunks: list[bytes] = []
            remaining = _MAX_BOOTSTRAP_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        os.unlink(raw_path)
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError("invalid_egress_bootstrap_file") from exc
    if not raw or len(raw) > _MAX_BOOTSTRAP_BYTES:
        raise RuntimeError("invalid_egress_bootstrap_payload")
    try:
        return raw.decode("utf8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("invalid_egress_bootstrap_payload") from exc


def consume_network_egress_bootstrap(
    *,
    environ: MutableMapping[str, str] | None = None,
    stdin: IO[str] | None = None,
) -> bool:
    """Consume the one-shot desktop stdin payload before child processes exist."""
    target_env = os.environ if environ is None else environ
    bootstrap_file = target_env.pop("VALUZ_EGRESS_BOOTSTRAP_FILE", None)
    marker = target_env.pop("VALUZ_EGRESS_BOOTSTRAP_STDIN", None)
    desktop_marker = target_env.pop("VALUZ_DESKTOP_BOOTSTRAP_STDIN", None)
    required = target_env.pop("VALUZ_EGRESS_REQUIRED", None)
    if bootstrap_file:
        raw = _consume_bootstrap_file(bootstrap_file)
    elif marker == "1" or desktop_marker == "1":
        source = sys.stdin if stdin is None else stdin
        raw = source.readline(_MAX_BOOTSTRAP_BYTES + 1)
        if not raw or len(raw) > _MAX_BOOTSTRAP_BYTES:
            raise RuntimeError("invalid_egress_bootstrap_payload")
    else:
        if required == "1":
            configure_network_egress(None, required_unavailable=True)
            return True
        return False
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        if desktop_marker == "1":
            token = payload.get("desktopControlToken")
            if not isinstance(token, str) or len(token) < 32:
                raise ValueError("invalid_desktop_control_token")
            global _desktop_control_token
            _desktop_control_token = token
            egress_payload = payload.get("egressBootstrap")
            if egress_payload is not None and not isinstance(egress_payload, dict):
                raise ValueError("invalid_egress_bootstrap_payload")
            configure_network_egress(
                egress_payload,
                required_unavailable=(
                    egress_payload is None and payload.get("egressRequired") is True
                ),
            )
        else:
            configure_network_egress(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("invalid_egress_bootstrap_payload") from exc
    return True
