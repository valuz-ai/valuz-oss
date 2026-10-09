"""Client for a dsh web host's typert Remote API.

The dsh host authenticates browsers with a per-process launch token: ``GET
/?token=…`` answers with an HttpOnly cookie, and every ``POST /api/<ns>/<m>``
must carry that cookie plus a same-origin ``Origin`` header. The request is a
``client-request`` envelope whose payload is ``{args: {<wire name>: value}}``;
the response is ``{type: "server-response", result: {ok, value | error}}``
(dsh ``client/connection`` + ``api/gateway``).
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx


class DshRemoteError(RuntimeError):
    """A Remote call the dsh host answered with ``ok: false``."""

    def __init__(self, method: str, error: dict[str, Any]) -> None:
        self.method = method
        self.code = str(error.get("code") or "unknown")
        self.details = error
        super().__init__(f"{method}: {self.code}: {error.get('message') or ''}".rstrip(": "))


class DshRemoteClient:
    def __init__(self, authenticated_url: str, *, timeout: float = 600.0) -> None:
        parts = urlsplit(authenticated_url)
        self.base_url = f"{parts.scheme}://{parts.netloc}"
        self._authenticated_url = authenticated_url
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._authenticated = False

    async def _authenticate(self) -> None:
        if self._authenticated:
            return
        # 303 → "/" with Set-Cookie; the jar keeps the HttpOnly session cookie.
        await self._client.get(self._authenticated_url)
        self._authenticated = True

    async def call(self, method: str, **args: Any) -> Any:
        await self._authenticate()
        response = await self._client.post(
            f"{self.base_url}/api/{method}",
            json={
                "type": "client-request",
                "rpcId": str(uuid.uuid4()),
                "method": method,
                "payload": {"args": args},
            },
            headers={"origin": self.base_url},
        )
        response.raise_for_status()
        result = response.json().get("result") or {}
        if not result.get("ok"):
            raise DshRemoteError(method, result.get("error") or {})
        return result.get("value")

    async def aclose(self) -> None:
        await self._client.aclose()
