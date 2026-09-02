from __future__ import annotations

import pytest
from src.core.mcp_server_instructions import append_trusted_mcp_server_instructions
from src.core.types import McpHttpServerConfig


@pytest.mark.asyncio
async def test_only_trusted_server_instructions_are_injected(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(cfg: McpHttpServerConfig) -> str:
        calls.append(cfg.name)
        return f"Use {cfg.name} capability routing."

    monkeypatch.setattr(
        "src.core.mcp_server_instructions._fetch_http_server_instructions",
        fake_fetch,
    )
    result = await append_trusted_mcp_server_instructions(
        "Base instructions.",
        (
            McpHttpServerConfig(
                name="official",
                url="https://data.test/mcp",
                server_instructions_trusted=True,
            ),
            McpHttpServerConfig(name="custom", url="https://evil.test/mcp"),
        ),
    )

    assert calls == ["official"]
    assert result.startswith("Base instructions.")
    assert 'name="official"' in result
    assert "Use official capability routing." in result
    assert "custom" not in result


@pytest.mark.asyncio
async def test_empty_or_failed_trusted_instructions_do_not_change_prompt(monkeypatch) -> None:
    async def fake_fetch(cfg: McpHttpServerConfig) -> str:
        if cfg.name == "failed":
            raise TimeoutError
        return ""

    monkeypatch.setattr(
        "src.core.mcp_server_instructions._fetch_http_server_instructions",
        fake_fetch,
    )
    result = await append_trusted_mcp_server_instructions(
        "Base instructions.",
        (
            McpHttpServerConfig(
                name="empty",
                url="https://data.test/empty",
                server_instructions_trusted=True,
            ),
            McpHttpServerConfig(
                name="failed",
                url="https://data.test/failed",
                server_instructions_trusted=True,
            ),
        ),
    )

    assert result == "Base instructions."


@pytest.mark.asyncio
async def test_combined_server_instructions_are_bounded(monkeypatch) -> None:
    async def fake_fetch(_cfg: McpHttpServerConfig) -> str:
        return "x" * 20_000

    monkeypatch.setattr(
        "src.core.mcp_server_instructions._fetch_http_server_instructions",
        fake_fetch,
    )
    result = await append_trusted_mcp_server_instructions(
        "",
        tuple(
            McpHttpServerConfig(
                name=f"official-{index}",
                url=f"https://data.test/{index}",
                server_instructions_trusted=True,
            )
            for index in range(3)
        ),
    )

    assert len(result) < 17_000
    assert 'name="official-0"' in result
    assert 'name="official-2"' not in result
