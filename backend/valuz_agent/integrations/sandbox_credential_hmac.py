"""Default OSS sandbox-credential verifier (per-owner HS256)."""

from __future__ import annotations

import asyncio

from valuz_agent.ports.sandbox_credential import SandboxCredentialClaims


class PerOwnerHmacSandboxCredentialVerifier:
    """Adapt the existing synchronous per-owner HMAC verifier to the async port."""

    async def credential_for(self, owner_user_id: str) -> str:
        from valuz_agent.adapters.capability_resolver import _mint_internal_mcp_token

        return await asyncio.to_thread(_mint_internal_mcp_token, owner_user_id)

    async def verify(self, credential: str | None) -> SandboxCredentialClaims | None:
        if not credential:
            return None
        return await asyncio.to_thread(self._verify_sync, credential)

    @staticmethod
    def _verify_sync(credential: str) -> SandboxCredentialClaims | None:
        from src.core.token_signer import InvalidTokenError

        from valuz_agent.boot.kernel import make_host_data_service_verifier_per_owner

        try:
            claims = make_host_data_service_verifier_per_owner().verify(credential)
        except InvalidTokenError:
            return None
        if claims is None:
            return None
        return SandboxCredentialClaims(user_id=claims.user_id, session_id=claims.session_id)


__all__ = ["PerOwnerHmacSandboxCredentialVerifier"]
