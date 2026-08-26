"""Port: verify the opaque credential carried by a sandbox.

Built-in MCP and the Data Service are trusted host surfaces called from an
untrusted sandbox. Both must derive the owner from one verified credential;
neither may trust an owner header or request body. The verifier is async so a
managed deployment may consult a database, cache, or identity service without
blocking the event loop.

OSS binds a per-owner HMAC implementation. Commercial overlays may replace it
at composition time while keeping the wire headers and endpoint contracts
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SandboxCredentialClaims:
    """Identity established by a successfully verified sandbox credential."""

    user_id: str
    session_id: str | None = None


class SandboxCredentialVerifierPort(Protocol):
    """Resolve and verify the opaque credential shared with a sandbox.

    Invalid, expired, absent, or unknown credentials return ``None``. An
    unexpected backend failure may raise; callers fail closed in that case.
    """

    async def credential_for(self, owner_user_id: str) -> str:
        """Return the credential to inject for one explicitly known owner."""

    async def verify(self, credential: str | None) -> SandboxCredentialClaims | None: ...


def get_sandbox_credential_verifier() -> SandboxCredentialVerifierPort:
    from valuz_agent.ports.extensions import ext

    return ext.sandbox_credential_verifier


def set_sandbox_credential_verifier(verifier: SandboxCredentialVerifierPort) -> None:
    """Replace the process-wide verifier at application composition time."""
    from valuz_agent.ports.extensions import ext

    ext.sandbox_credential_verifier = verifier


__all__ = [
    "SandboxCredentialClaims",
    "SandboxCredentialVerifierPort",
    "get_sandbox_credential_verifier",
    "set_sandbox_credential_verifier",
]
