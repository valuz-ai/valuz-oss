"""Port: sandbox provisioning — give me a running kernel endpoint.

This is the ① supply face of the kernel-sandbox deployment design (see
``docs/design/kernel-sandbox-deployment.md`` §B.4). The abstraction sits
at the **endpoint-provisioning** layer, deliberately NOT at the
sandbox-primitive layer: ``exec`` / file-read / file-write / port-forward
are NOT on this protocol — those go through the kernel's own HTTP API
once it is running. A provider answers exactly one question: *"give me a
running ``valuz-server`` reachable at this URL with this token."*

Keeping the surface this thin means a vendor SDK's primitives (E2B's
filesystem API, ``docker exec``, …) never leak into the business layer —
swapping providers is swapping one ``integrations/sandbox_*.py`` file.

``provision`` is responsible for **bootstrap**: it migrates the kernel's
private database, spawns/starts the server, waits for ``/health``, and
only then returns. The returned ``(base_url, token)`` is fed straight
into ``HttpKernelClient`` — the supply face and the control face
(``KernelClient``) hand off here and meet nowhere else.

The default OSS implementation is ``integrations/sandbox_seatbelt`` (a
``sandbox-exec``-wrapped subprocess on macOS); other drivers
(``docker``, ``opensandbox``, ``e2b``) implement the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class MountSpec:
    """One entry of the provision **manifest** — a host path made visible
    inside the sandbox.

    In the local form host and sandbox share a filesystem, so ``target``
    equals ``source`` and the spec is realised as a profile allow-rule
    (Seatbelt) or a bind mount (docker). A remote driver would realise it
    as a stage-in / volume attach instead.
    """

    target: str
    source: str
    mode: Literal["rw", "ro"] = "ro"


@dataclass(frozen=True)
class SandboxSpec:
    """Everything a provider needs to bring up one kernel endpoint.

    The manifest (``mounts``) plus ``env`` and ``allowed_domains`` are the
    three knobs the design's six faces collapse onto for the minimal form:
    ⑤ materials (mounts), ⑥ credentials (env, L1 injection), and the
    network policy (allowed_domains, deny-by-default).

    RED LINE: the host's business database directory and secret store are
    NEVER part of ``mounts`` — that is the whole point of sandboxing. The
    Seatbelt driver additionally emits explicit ``deny`` rules for them
    (see ``integrations/sandbox_seatbelt``).
    """

    sandbox_id: str
    kernel_db_path: str
    """Absolute path to the sandbox's private kernel SQLite file."""
    mounts: tuple[MountSpec, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    """Environment injected into the kernel process — the ⑥ L1 落点
    (provider keys resolved from references, never persisted)."""
    allowed_domains: tuple[str, ...] = ()
    """Outbound network allowlist (deny-by-default). LLM provider hosts +
    the host callback endpoint."""
    host_callback_url: str = ""
    """Where the sandboxed kernel reaches the host's toolkit MCP server
    (④ harness callback). Loopback in the local form."""
    deny_paths: tuple[str, ...] = ()
    """Paths the sandbox must NOT read — the host business DB dir and
    secret store. Drivers translate to explicit deny rules."""


@dataclass(frozen=True)
class SandboxEndpoint:
    """The handoff value: a running kernel the host can drive over HTTP."""

    sandbox_id: str
    base_url: str
    """Directly usable as ``HttpKernelClient(base_url, token=...)``."""
    token: str
    """The ``KERNEL_AUTH_TOKEN`` the kernel was started with."""


@dataclass(frozen=True)
class MountGrant:
    """The runtime counterpart of ``MountSpec``: the receipt for a host path
    made reachable inside an *already-running* sandbox.

    ``MountSpec`` is the *provision-time* request (a static manifest entry);
    a ``MountGrant`` is what ``bind_workspace`` returns AFTER the sandbox is
    up — the ② dynamic-mount face (see
    ``docs/design/kernel-sandbox-deployment.md`` §C). It lets a project
    created/bound while the kernel is already serving become reachable
    without a restart, and carries the handle to revoke that access.

    ``kernel_cwd`` is the cloud seam — the path the kernel must use as the
    session cwd:

    - **Local (Seatbelt):** host and sandbox share a filesystem, so
      ``kernel_cwd == host_path`` (unchanged); the binding is realised as a
      macOS *sandbox extension* the kernel consumes live. Nothing is copied.
    - **Cloud:** files are staged into a unified workspace root, so
      ``kernel_cwd`` is the in-sandbox staged path (e.g.
      ``/workspace/{project_id}``), which differs from ``host_path``.

    The caller (``HttpKernelClient.create_session``) always uses
    ``kernel_cwd`` for the session, so the same call site is correct for
    both forms.
    """

    grant_id: str
    """Opaque handle for ``unbind_workspace`` — the kernel-side extension
    handle (local) or the staging-session id (cloud)."""
    kernel_cwd: str
    """The cwd the kernel must use for sessions under this path."""
    host_path: str
    """The original host path that was bound (canonical/realpath)."""
    mode: Literal["rw", "ro"] = "rw"


class SandboxProvider(Protocol):
    """Port: provision / observe / tear down a kernel endpoint.

    Every operation is keyed by ``sandbox_id`` so a host can manage a fleet
    (one sandbox per project in the design). The protocol is intentionally
    minimal — no exec, no file IO, no port management; those belong to the
    kernel's own API surface, reached via ``HttpKernelClient`` once
    ``provision`` returns.
    """

    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint:
        """Bring up a kernel for ``spec`` and return its reachable endpoint.

        Must self-bootstrap (migrate the private DB, start the server,
        wait for health) before returning. Raises ``SandboxProvisionError``
        on failure.
        """
        ...

    async def health(self, sandbox_id: str) -> bool:
        """True iff the sandbox's kernel answers ``/health``."""
        ...

    async def destroy(self, sandbox_id: str) -> None:
        """Tear the sandbox down. Idempotent — a no-op if already gone."""
        ...

    async def bind_workspace(
        self, sandbox_id: str, host_path: str, mode: Literal["rw", "ro"] = "rw"
    ) -> MountGrant:
        """Make ``host_path`` reachable inside an ALREADY-RUNNING sandbox.

        The dynamic counterpart to the provision-time manifest: called when
        a project whose cwd is outside the static mounts is bound while the
        kernel is already serving. Local drivers issue+consume a sandbox
        extension (no restart, no copy); a cloud driver stages the files.
        Idempotent per ``(sandbox_id, host_path)``. Raises
        ``SandboxProvisionError`` if the grant cannot be delivered.
        """
        ...

    async def unbind_workspace(self, sandbox_id: str, grant_id: str) -> None:
        """Revoke a prior ``bind_workspace`` grant. Idempotent."""
        ...


class SandboxProvisionError(RuntimeError):
    """A provider could not bring up a healthy kernel endpoint."""
