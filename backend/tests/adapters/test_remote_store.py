"""Phase A — RemoteStore abstract base machinery + backend factory.

Transport-agnostic policy (retry / idempotency / fail-loud / token refresh)
is exercised through a tiny in-memory ``FakeRemoteStore`` subclass that only
implements the single-shot ``_*_once`` hooks. No network, no DB.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from src.adapters.remote_store import (
    RemoteFatalError,
    RemoteStore,
    RemoteTransientError,
    build_remote_store,
    register_remote_backend,
)
from src.core.events import Event
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import Message, Session


class FakeRemoteStore(RemoteStore):
    """In-memory backend for testing the base policy.

    ``fail_times`` transient failures precede the first success on
    ``append_event`` / ``load_session``; ``append_event`` dedups by the
    ``request_id`` the base supplies, so a retried write returns the original
    ``seq`` (the idempotency contract).
    """

    def __init__(self, *, fail_times: int = 0, fatal_on_load: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.fail_times = fail_times
        self.fatal_on_load = fatal_on_load
        self.append_attempts = 0
        self.load_attempts = 0
        self.append_request_ids: list[str] = []
        self.tokens_seen: list[str] = []
        self._by_uid: dict[str, int] = {}
        self._seq = 0

    async def _append_event_once(
        self, user_id, session_id, message_id, event, *, request_id, seq=None
    ):
        self.append_attempts += 1
        self.append_request_ids.append(request_id)
        self.tokens_seen.append(await self._bearer())
        if self.append_attempts <= self.fail_times:
            raise RemoteTransientError("transient append boom")
        if request_id in self._by_uid:  # idempotent replay → original seq
            return self._by_uid[request_id]
        self._seq += 1
        self._by_uid[request_id] = self._seq
        return self._seq

    async def _load_session_once(self, user_id, session_id):
        self.load_attempts += 1
        if self.fatal_on_load:
            raise RemoteFatalError("not found / 4xx — do not retry")
        return None

    # --- remaining abstract hooks: minimal concrete stubs ---------------
    async def _save_session_once(self, session: Session, *, request_id: str) -> None:
        return None

    async def _list_sessions_once(self, user_id, *, status, ids, limit, offset):
        return []

    async def _delete_session_once(self, user_id, session_id, *, request_id):
        return True

    async def _save_message_once(self, user_id, message: Message, *, request_id: str) -> None:
        return None

    async def _load_message_once(self, user_id, message_id):
        return None

    async def _list_messages_for_session_once(self, user_id, session_id, *, limit, offset):
        return []

    async def _get_events_once(self, user_id, session_id, *, limit, offset) -> list[Event]:
        return []

    async def _get_events_for_message_once(self, user_id, message_id, *, limit, offset):
        return []

    async def _get_events_after_once(
        self, user_id, session_id, *, after_seq, limit
    ) -> list[StoredEvent]:
        return []

    async def _get_events_after_for_user_once(
        self, user_id, *, after_seq, types, limit
    ) -> list[StoredEvent]:
        return []

    async def _get_events_window_once(self, user_id, session_id, *, before_seq, turn_limit):
        return ([], False)

    async def _usage_rollup_once(self, user_id, start_ms, end_ms) -> list[UsageRollupRow]:
        return []


def _const_token(value: str = "jwt-A"):
    async def _hook() -> str:
        return value

    return _hook


async def test_retry_succeeds_after_transient_failures():
    store = FakeRemoteStore(
        fail_times=2, access_token=_const_token(), max_attempts=5, base_backoff_s=0.0
    )
    seq = await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    assert seq == 1
    assert store.append_attempts == 3  # 2 transient + 1 success


async def test_retry_reuses_one_idempotency_key_per_write():
    store = FakeRemoteStore(
        fail_times=2, access_token=_const_token(), max_attempts=5, base_backoff_s=0.0
    )
    await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    # The same request_id is reused across all attempts of ONE logical write.
    assert len(set(store.append_request_ids)) == 1
    assert len(store.append_request_ids) == 3


async def test_distinct_writes_get_distinct_keys():
    store = FakeRemoteStore(access_token=_const_token(), base_backoff_s=0.0)
    s1 = await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    s2 = await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    assert s1 == 1 and s2 == 2
    assert len(set(store.append_request_ids)) == 2  # one per logical write


async def test_fail_loud_after_max_attempts():
    store = FakeRemoteStore(
        fail_times=99, access_token=_const_token(), max_attempts=3, base_backoff_s=0.0
    )
    with pytest.raises(RemoteFatalError):
        await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    assert store.append_attempts == 3  # bounded — not infinite


async def test_non_transient_propagates_without_retry():
    store = FakeRemoteStore(
        fatal_on_load=True, access_token=_const_token(), max_attempts=5, base_backoff_s=0.0
    )
    with pytest.raises(RemoteFatalError):
        await store.load_session("u", "s")
    assert store.load_attempts == 1  # fatal → no retry


async def test_access_token_hook_called_each_attempt():
    seen: list[str] = []

    async def _rotating() -> str:
        token = f"jwt-{len(seen)}"
        seen.append(token)
        return token

    store = FakeRemoteStore(
        fail_times=1, access_token=_rotating, max_attempts=5, base_backoff_s=0.0
    )
    await store.append_event("u", "s", "m", Event(type="user_message", data={}))
    # bearer refreshed on each transport attempt (2: one transient + success)
    assert store.tokens_seen == ["jwt-0", "jwt-1"]


def test_build_unregistered_backend_fails_loud():
    with pytest.raises(RemoteFatalError):
        build_remote_store(kind="does-not-exist", access_token=_const_token())


def test_register_and_build_backend():
    register_remote_backend("fake-test", lambda **kw: FakeRemoteStore(**kw))
    store = build_remote_store(kind="fake-test", access_token=_const_token())
    assert isinstance(store, FakeRemoteStore)


def test_remote_store_satisfies_storeport_surface():
    # Structural check: every StorePort method is implemented on the base, so a
    # RemoteStore can be passed wherever a StorePort is expected.
    storeport_methods = (
        "save_session",
        "load_session",
        "list_sessions",
        "delete_session",
        "save_message",
        "load_message",
        "list_messages_for_session",
        "append_event",
        "get_events",
        "get_events_for_message",
        "get_events_after",
        "get_events_window",
        "usage_rollup",
    )
    for name in storeport_methods:
        assert callable(getattr(FakeRemoteStore, name, None)), f"missing StorePort method {name!r}"
