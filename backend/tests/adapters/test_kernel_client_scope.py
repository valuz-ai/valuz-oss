"""Sandbox-scope routing through the kernel_client facade (scoped allocation).

Pins the on-demand start/stop seam:

- EXEC ops derive ``session:{session_id}`` scope by default and hand it to a
  scope-aware allocator;
- an explicit creation scope (tasks) seeds the session→scope cache so later
  ops on the same session route to the SAME scope;
- the bound resolver maps task sessions to ``task:{task_id}``;
- pre-scope allocators (no ``scope`` kwarg) keep working untouched;
- ``subscribe_session_events_existing`` / ``emit_live_event`` NEVER provision;
- ``run_turn``'s ``pre_turn`` hook runs AFTER allocation, with control writes
  pinned to the instance that is about to serve the turn.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sys.path bootstrap)
from valuz_agent.adapters import kernel_client as kc
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.sandbox_allocator import SandboxLease, SandboxScope
from valuz_agent.ports.sandbox_provider import SandboxEndpoint


@pytest.fixture(autouse=True)
def _clean_scope_state(monkeypatch):
    monkeypatch.setattr(kc, "_scope_cache", {})
    monkeypatch.setattr(kc, "_scope_resolver", None)
    monkeypatch.setattr(kc, "_endpoint_clients", {})


class _ScopedAllocator:
    """Records every (owner, scope) it is asked for; one endpoint per scope key."""

    def __init__(self) -> None:
        self.ensured: list[tuple[str, SandboxScope | None]] = []
        self.new_turns: list[bool] = []
        self.peeked: list[tuple[str, SandboxScope | None]] = []
        self.live: bool = True

    async def ensure(
        self, *, owner_user_id: str, scope: SandboxScope | None = None, new_turn: bool = False
    ) -> SandboxLease:
        self.ensured.append((owner_user_id, scope))
        self.new_turns.append(new_turn)
        key = scope.key if scope else "owner"
        return SandboxLease(
            endpoint=SandboxEndpoint(sandbox_id=key, base_url=f"https://{key}.pool", token="t")
        )

    async def peek(
        self, *, owner_user_id: str, scope: SandboxScope | None = None
    ) -> SandboxLease | None:
        self.peeked.append((owner_user_id, scope))
        if not self.live:
            return None
        key = scope.key if scope else "owner"
        return SandboxLease(
            endpoint=SandboxEndpoint(sandbox_id=key, base_url=f"https://{key}.pool", token="t")
        )

    async def release(self, *, owner_user_id: str, scope: SandboxScope | None = None) -> None:
        return None


async def test_exec_ops_default_to_session_scope(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    class _FakeClient:
        async def run_turn(self, *a, **k):  # noqa: ANN002, ANN003
            return "MSG"

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": _FakeClient()})
    await kc.run_turn("u1", "s1", "hi")
    assert alloc.ensured == [("u1", SandboxScope(kind="session", id="s1"))]
    # run_turn signals a fresh conversation turn to the allocator.
    assert alloc.new_turns == [True]


async def test_turn_context_is_built_from_durable_session_metadata(monkeypatch) -> None:
    """An overlay receives durable metadata rather than request headers."""
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    seen: dict[str, object] = {}

    class _ContextContributor:
        async def build(self, **kwargs):  # noqa: ANN003
            seen.update(kwargs)
            return {"example.runtime": "opaque-value"}

    class _Kernel:
        async def run_turn(self, *args, **kwargs):  # noqa: ANN002, ANN003
            seen["kernel_context"] = kwargs.get("runtime_context")
            return "MSG"

    class _DataPlane:
        async def get_session(self, user_id, session_id):  # noqa: ANN001
            assert (user_id, session_id) == ("u1", "member-1")
            return SimpleNamespace(metadata={"valuz": {"task_id": "task-7"}})

    monkeypatch.setattr(ext, "runtime_turn_context", _ContextContributor())
    monkeypatch.setattr(kc, "_host_data_client", _DataPlane())
    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:member-1.pool": _Kernel()})

    assert await kc.run_turn("u1", "member-1", "hi") == "MSG"
    assert seen == {
        "user_id": "u1",
        "session_id": "member-1",
        "metadata": {"valuz": {"task_id": "task-7"}},
        "kernel_context": {"example.runtime": "opaque-value"},
    }


async def test_non_turn_ops_do_not_set_new_turn(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    class _FakeClient:
        async def submit_action(self, *a, **k):  # noqa: ANN002, ANN003
            return {}

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": _FakeClient()})
    await kc.submit_action("u1", "s1", object())
    assert alloc.new_turns == [False]  # mid-turn op reuses the current instance


async def test_explicit_create_scope_seeds_cache_for_later_ops(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    task_scope = SandboxScope(kind="task", id="t42")

    class _FakeClient:
        async def create_session(self, user_id, req):  # noqa: ANN001
            return "SESSION"

        async def run_turn(self, *a, **k):  # noqa: ANN002, ANN003
            return "MSG"

    monkeypatch.setattr(kc, "_endpoint_clients", {"https://task:t42.pool": _FakeClient()})

    class _Req:
        id = "lead-1"

    await kc.create_session("u1", _Req(), scope=task_scope)
    await kc.run_turn("u1", "lead-1", "go")  # later op, no explicit scope
    assert [s for _, s in alloc.ensured] == [task_scope, task_scope]


async def test_resolver_maps_task_sessions(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    async def _resolver(user_id: str, session_id: str) -> SandboxScope | None:
        return SandboxScope(kind="task", id="t7") if session_id == "member-1" else None

    kc.bind_sandbox_scope_resolver(_resolver)

    class _FakeClient:
        async def interrupt(self, *a, **k):  # noqa: ANN002, ANN003
            return None

    monkeypatch.setattr(
        kc,
        "_endpoint_clients",
        {"https://task:t7.pool": _FakeClient(), "https://session:chat-1.pool": _FakeClient()},
    )
    await kc.interrupt("u1", "member-1")
    await kc.interrupt("u1", "chat-1")
    assert [s for _, s in alloc.ensured] == [
        SandboxScope(kind="task", id="t7"),
        SandboxScope(kind="session", id="chat-1"),
    ]


async def test_prescope_allocator_still_works(monkeypatch) -> None:
    """An allocator written against the pre-scope port signature is never
    handed a scope kwarg — additive contract (ADR-001 spirit)."""

    calls: list[str] = []

    class _Legacy:
        async def ensure(self, *, owner_user_id: str) -> SandboxLease:
            calls.append(owner_user_id)
            return SandboxLease(endpoint=None)

        async def release(self, *, owner_user_id: str) -> None:
            return None

    monkeypatch.setattr(ext, "sandbox_allocator", _Legacy())
    assert await kc._kernel_for("u1", SandboxScope(kind="session", id="s1")) is kc.client
    assert calls == ["u1"]


async def test_subscribe_existing_never_provisions(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False  # no live kernel for any scope
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    frames = [f async for f in kc.subscribe_session_events_existing("u1", "s1")]
    assert frames == []
    assert alloc.ensured == []  # peek-only: opening history never provisions
    assert alloc.peeked == [("u1", SandboxScope(kind="session", id="s1"))]


async def test_emit_live_event_noops_without_live_kernel(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    await kc.emit_live_event("u1", "s1", "todo_update", {"todos": []})
    assert alloc.ensured == []  # never provisions just to broadcast a live frame


# ── pre_turn: capability convergence reaches the turn's kernel ──────────────


class _RecordingKernel:
    """A turn's sandbox kernel: records the control writes it receives."""

    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self.updates: list[str] = []
        self._order = order

    async def update_session(self, user_id, session_id, req):  # noqa: ANN001
        self.updates.append(session_id)
        self._order.append(f"update:{self.name}")
        return "SESSION"

    async def run_turn(self, *a, **k):  # noqa: ANN002, ANN003
        self._order.append("run_turn")
        return "MSG"


async def test_pre_turn_hook_runs_after_allocation_and_before_the_turn(monkeypatch) -> None:
    order: list[str] = []
    alloc = _ScopedAllocator()

    async def _ensure(*, owner_user_id, scope=None, new_turn=False):  # noqa: ANN001
        order.append("ensure")
        return await _ScopedAllocator.ensure(
            alloc, owner_user_id=owner_user_id, scope=scope, new_turn=new_turn
        )

    monkeypatch.setattr(alloc, "ensure", _ensure)
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    monkeypatch.setattr(
        kc, "_endpoint_clients", {"https://session:s1.pool": _RecordingKernel("live", order)}
    )

    async def _hook() -> None:
        order.append("hook")

    await kc.run_turn("u1", "s1", "hi", pre_turn=_hook)
    assert order == ["ensure", "hook", "run_turn"]


async def test_pre_turn_control_writes_land_on_the_turns_kernel(monkeypatch) -> None:
    """The regression this seam exists for.

    The scope has NO live instance when the turn starts (``peek`` → None: the
    previous per-turn sandbox was reclaimed during a long idle). Without the
    pin, the hook's ``update_session`` would resolve through ``peek`` and fall
    back to the durable — and the freshly-seeded sandbox, which has no remote
    read path, would run the turn on its stale snapshot. That is how a resumed
    conversation 401'd every external MCP call: the refreshed OAuth bearer only
    ever reached the durable.
    """
    order: list[str] = []
    alloc = _ScopedAllocator()
    alloc.live = False  # nothing live for this scope — cold boot
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    live = _RecordingKernel("live", order)
    durable = _RecordingKernel("durable", order)
    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": live})
    monkeypatch.setattr(kc, "_host_data_client", durable)

    async def _hook() -> None:
        await kc.update_session("u1", "s1", object())

    await kc.run_turn("u1", "s1", "hi", pre_turn=_hook)

    assert live.updates == ["s1"], "the refresh must reach the instance serving the turn"
    assert durable.updates == [], "and must not be diverted to the durable"
    assert order == ["update:live", "run_turn"]


async def test_pre_turn_pin_is_scoped_to_its_own_session(monkeypatch) -> None:
    """A hook that touches a DIFFERENT session must not inherit the pin."""
    order: list[str] = []
    alloc = _ScopedAllocator()
    alloc.live = False
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    live = _RecordingKernel("live", order)
    durable = _RecordingKernel("durable", order)
    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": live})
    monkeypatch.setattr(kc, "_host_data_client", durable)

    async def _hook() -> None:
        await kc.update_session("u1", "other-session", object())

    await kc.run_turn("u1", "s1", "hi", pre_turn=_hook)

    assert live.updates == []
    assert durable.updates == ["other-session"]  # routed normally, not pinned


async def test_pre_turn_pin_is_released_after_the_hook(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    order: list[str] = []
    durable = _RecordingKernel("durable", order)
    monkeypatch.setattr(
        kc, "_endpoint_clients", {"https://session:s1.pool": _RecordingKernel("live", order)}
    )
    monkeypatch.setattr(kc, "_host_data_client", durable)

    await kc.run_turn("u1", "s1", "hi", pre_turn=lambda: _noop())
    # Post-turn control writes route normally again (at-rest → durable).
    await kc.update_session("u1", "s1", object())
    assert durable.updates == ["s1"]


async def _noop() -> None:
    return None


async def test_a_failing_pre_turn_hook_never_sinks_the_turn(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    order: list[str] = []
    monkeypatch.setattr(
        kc, "_endpoint_clients", {"https://session:s1.pool": _RecordingKernel("live", order)}
    )

    async def _boom() -> None:
        raise RuntimeError("connector resolver down")

    assert await kc.run_turn("u1", "s1", "hi", pre_turn=_boom) == "MSG"
    assert order == ["run_turn"]


async def test_required_policy_failure_blocks_dispatch_and_releases_pin(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    order: list[str] = []
    live = _RecordingKernel("live", order)
    durable = _RecordingKernel("durable", order)
    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": live})
    monkeypatch.setattr(kc, "_host_data_client", durable)

    async def required_policy() -> None:
        raise kc.RequiredPreTurnError("Current task check policy unavailable")

    with pytest.raises(kc.TurnNotStartedError, match="Current task check policy unavailable"):
        await kc.run_turn("u1", "s1", "new research", pre_turn=required_policy)
    assert order == []
    await kc.update_session("u1", "s1", object())
    assert durable.updates == ["s1"]
    assert live.updates == []


# ── ephemeral review reuse (memory review inside the source's warm sandbox) ──


class _ReviewKernel:
    def __init__(self) -> None:
        self.created: list[tuple[str, str | None]] = []
        self.ran: list[tuple[str, str, str]] = []

    async def create_session(self, user_id, req):  # noqa: ANN001
        self.created.append((user_id, getattr(req, "id", None)))
        return "SESSION"

    async def run_turn(self, user_id, session_id, prompt, *a, **k):  # noqa: ANN001, ANN002, ANN003
        self.ran.append((user_id, session_id, prompt))
        return SimpleNamespace(assistant_message="REVIEW-OUT")


class _Req:
    id = "ephem-1"


async def test_ephemeral_review_reuses_live_sandbox_without_renewing(monkeypatch) -> None:
    # A live source sandbox is REUSED via peek — never ``ensure`` (which would
    # renew the AGS TTL and defeat the post-turn idle clamp keeping it warm).
    alloc = _ScopedAllocator()  # live=True
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)
    review = _ReviewKernel()
    monkeypatch.setattr(kc, "_endpoint_clients", {"https://session:s1.pool": review})
    deletes: list[tuple[str, str]] = []

    class _Durable:
        async def delete_session(self, user_id, session_id):  # noqa: ANN001
            deletes.append((user_id, session_id))
            return True

    monkeypatch.setattr(kc, "client", _Durable())

    out = await kc.run_ephemeral_review_in_scope(
        "u1", _Req(), "review this", reuse_scope=SandboxScope(kind="session", id="s1")
    )
    assert out == "REVIEW-OUT"
    assert alloc.ensured == []  # reuse must NOT provision / renew
    assert alloc.peeked == [("u1", SandboxScope(kind="session", id="s1"))]
    assert review.created == [("u1", "ephem-1")]  # ran inside the reused kernel
    assert review.ran == [("u1", "ephem-1", "review this")]
    assert deletes == [("u1", "ephem-1")]  # throwaway durable record cleaned up


async def test_ephemeral_review_returns_none_when_source_sandbox_gone(monkeypatch) -> None:
    alloc = _ScopedAllocator()
    alloc.live = False  # source sandbox already reclaimed
    monkeypatch.setattr(ext, "sandbox_allocator", alloc)

    out = await kc.run_ephemeral_review_in_scope(
        "u1", _Req(), "x", reuse_scope=SandboxScope(kind="session", id="s1")
    )
    assert out is None  # caller falls back to its own throwaway sandbox
    assert alloc.ensured == []  # the gone path never provisions here


async def test_ephemeral_review_inert_without_scoped_allocator(monkeypatch) -> None:
    # No scoped allocator → the local single-kernel case has no per-scope sandbox
    # to reuse; the helper is inert so the caller's normal path runs.
    monkeypatch.setattr(ext, "sandbox_allocator", None, raising=False)

    out = await kc.run_ephemeral_review_in_scope(
        "u1", _Req(), "x", reuse_scope=SandboxScope(kind="session", id="s1")
    )
    assert out is None
