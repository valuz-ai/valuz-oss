"""Merged supervision keeps typed fail-closed hooks and shared-host liveness."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from valuz_agent.adapters import kernel_client


@pytest.mark.asyncio
@pytest.mark.parametrize("with_liveness", [False, True])
async def test_pending_scan_facade_forwards_optional_shared_host_liveness(
    monkeypatch, with_liveness
):
    client = kernel_client.InProcessKernelClient()
    observed = []

    async def session_alive(owner, sid):
        observed.append((owner, sid))
        return owner == "owner-a" and sid == "live"

    async def scan(*, session_alive=None):
        if with_liveness:
            assert session_alive is not None
            assert await session_alive("owner-a", "live")
            assert not await session_alive("owner-b", "dead")
        else:
            assert session_alive is None
        return 1

    monkeypatch.setattr(client, "scan_orphan_pendings", scan)
    monkeypatch.setattr(kernel_client, "client", client)
    if with_liveness:
        assert await kernel_client.scan_orphan_pendings(session_alive=session_alive) == 1
        assert observed == [("owner-a", "live"), ("owner-b", "dead")]
    else:
        assert await kernel_client.scan_orphan_pendings() == 1
        assert not observed


@pytest.mark.asyncio
async def test_pending_scan_without_supervision_fails_with_typed_unavailable(monkeypatch):
    monkeypatch.setattr(kernel_client, "client", SimpleNamespace())
    with pytest.raises(kernel_client.KernelUnavailableError) as error:
        await kernel_client.scan_orphan_pendings()
    assert error.value.status == 503
