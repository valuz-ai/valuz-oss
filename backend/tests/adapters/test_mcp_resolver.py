"""Regression tests for mcp_resolver header injection.

Guards the "passes the test, fails at runtime" divergence: the connector
probe and the runtime resolver must inject identical headers. Both now go
through ``service.build_overrides`` (manifest-driven, no ``auth_type`` /
``auth_header_name`` branch). The Authorization header keeps a single
transitional Bearer-prefix compat (legacy / Slice-2-migrated secrets store
the raw token); custom header names carry the raw secret verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Side-effect import — surfaces ``src.core...`` on sys.path before the
# resolver imports ``McpServerConfig`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.mcp_resolver import _build_http_config


@dataclass
class _FakeRow:
    id: str = "c1"
    slug: str = "acme"
    url: str = "https://mcp.acme.test/mcp"
    transport: str = "http"
    auth_type: str = "bearer"
    headers_json: str | None = None
    params_json: str | None = None
    cred_manifest_json: str | None = None
    args_json: str | None = None


class _FakeSecrets:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def get(self, ref: str) -> str | None:
        return self._m.get(ref)


def _manifest(name: str) -> str:
    return json.dumps(
        [
            {
                "key": "api_key",
                "target": "header",
                "name": name,
                "secret_ref": "connector/c1/api_key",
            }
        ]
    )


async def _headers(row: _FakeRow) -> dict[str, str]:
    secrets = _FakeSecrets({"connector/c1/api_key": "sk-123"})
    cfgs = await _build_http_config(row, secrets=secrets)
    assert cfgs is not None and len(cfgs) == 1
    return dict(cfgs[0].headers)


async def test_should_prefix_bearer_when_manifest_header_is_authorization() -> None:
    headers = await _headers(_FakeRow(cred_manifest_json=_manifest("Authorization")))
    assert headers == {"Authorization": "Bearer sk-123"}


async def test_should_send_raw_secret_when_manifest_header_is_custom() -> None:
    headers = await _headers(_FakeRow(cred_manifest_json=_manifest("X-API-Key")))
    assert headers == {"X-API-Key": "sk-123"}


async def test_should_treat_authorization_case_insensitively() -> None:
    headers = await _headers(_FakeRow(cred_manifest_json=_manifest("authorization")))
    assert headers == {"authorization": "Bearer sk-123"}
