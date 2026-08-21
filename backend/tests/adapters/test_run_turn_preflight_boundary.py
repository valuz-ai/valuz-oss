"""``run_turn`` marks the boundary between "the kernel never saw this turn" and
"the kernel owns this turn".

Everything ``run_turn`` does before handing off — allocating the turn's kernel,
building the overlay's runtime context (where a commercial build swaps in a
cloud execution capability) — happens with the kernel not yet entered, so a
failure there leaves the turn with no event bracket at all. Those failures are
re-raised as ``TurnNotStartedError`` so the turn driver knows it has to write
the opening ``user_message`` itself.

Anything the kernel raises after accepting the turn must keep its own type:
by then the kernel has already opened the bracket, and a second user message
would duplicate the user's bubble.
"""

from __future__ import annotations

from typing import Any

import pytest

from valuz_agent.adapters import kernel_client
from valuz_agent.adapters.kernel_client import TurnNotStartedError


class _Kernel:
    def __init__(self, raise_exc: BaseException | None = None) -> None:
        self._raise_exc = raise_exc

    async def run_turn(self, *a: Any, **k: Any) -> str:
        if self._raise_exc is not None:
            raise self._raise_exc
        return "message"


def _patch(monkeypatch: pytest.MonkeyPatch, kernel: _Kernel) -> None:
    async def _scope_for(*a: Any, **k: Any) -> str:
        return "scope"

    async def _kernel_for(*a: Any, **k: Any) -> _Kernel:
        return kernel

    monkeypatch.setattr(kernel_client, "_scope_for", _scope_for)
    monkeypatch.setattr(kernel_client, "_kernel_for", _kernel_for)


async def _run(monkeypatch: pytest.MonkeyPatch) -> Any:
    return await kernel_client.run_turn("user-1", "sess-1", "hi")


@pytest.mark.asyncio
async def test_preflight_context_failure_becomes_turn_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _Kernel())

    async def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("502: Valuz execution capability returned 403")

    monkeypatch.setattr(kernel_client, "_build_runtime_turn_context", _boom)

    with pytest.raises(TurnNotStartedError) as caught:
        await _run(monkeypatch)
    # The cause's text is preserved verbatim — it is what surfaces to the user
    # as the turn's stop reason.
    assert "execution capability returned 403" in str(caught.value)


@pytest.mark.asyncio
async def test_preflight_allocation_failure_becomes_turn_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _scope_for(*a: Any, **k: Any) -> str:
        return "scope"

    async def _no_kernel(*a: Any, **k: Any) -> None:
        raise RuntimeError("no sandbox available")

    monkeypatch.setattr(kernel_client, "_scope_for", _scope_for)
    monkeypatch.setattr(kernel_client, "_kernel_for", _no_kernel)

    with pytest.raises(TurnNotStartedError):
        await _run(monkeypatch)


@pytest.mark.asyncio
async def test_failure_inside_the_kernel_keeps_its_own_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _Kernel(RuntimeError("model call blew up")))

    async def _ctx(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(kernel_client, "_build_runtime_turn_context", _ctx)

    with pytest.raises(RuntimeError) as caught:
        await _run(monkeypatch)
    assert not isinstance(caught.value, TurnNotStartedError)
