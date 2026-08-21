from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from valuz_agent.integrations import feishu_long_connection as feishu_runtime
from valuz_agent.integrations.feishu_long_connection import (
    FeishuLongConnectionConfig,
    FeishuLongConnectionRunner,
    inbound_from_sdk_event,
)
from valuz_agent.modules.channels import AgentChannelRouteDecision, ChannelRouteDecisionKind
from valuz_agent.modules.channels.adapters.base import InboundChannelMessage
from valuz_agent.modules.channels.service import ChannelIngressResult


def test_inbound_from_sdk_event_normalizes_message_for_bound_agent() -> None:
    event = P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt-1",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou-user"}},
                "message": {
                    "message_id": "om-msg",
                    "chat_id": "oc-chat",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": '<at user_id="ou-bot">Valuz 小助手</at> 你好'}
                    ),
                    "mentions": [{"id": {"open_id": "ou-bot"}, "name": "Valuz 小助手"}],
                },
            },
        }
    )

    inbound = inbound_from_sdk_event(
        event,
        FeishuLongConnectionConfig(
            channel_instance_id="feishu-main",
            owner_user_id="u1",
            agent_slug="valuz-helper",
            app_id="cli_app_1",
            app_secret="app-secret",
        ),
    )

    assert inbound.text == "你好"
    assert inbound.context.user_id == "u1"
    assert inbound.context.mentioned_agent_slug == "valuz-helper"
    assert inbound.context.external_chat_id == "oc-chat"
    # A plain chat message is NOT a thread. Keying it by its own message id
    # made the route key unique per message, so no session was ever continued.
    assert inbound.context.external_thread_id is None


def test_inbound_from_sdk_event_keeps_user_opened_topic_as_thread() -> None:
    """A topic the user opens branches off: its root id becomes the thread id,
    giving that branch its own route key (and therefore its own session)."""
    event = P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {"event_id": "evt-2", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou-user"}},
                "message": {
                    "message_id": "om-msg-2",
                    "root_id": "om-root",
                    "chat_id": "oc-chat",
                    "message_type": "text",
                    "content": json.dumps({"text": "继续说"}),
                },
            },
        }
    )

    inbound = inbound_from_sdk_event(
        event,
        FeishuLongConnectionConfig(
            channel_instance_id="feishu-main",
            owner_user_id="u1",
            agent_slug="valuz-helper",
            app_id="cli_app_1",
            app_secret="app-secret",
        ),
    )

    assert inbound.context.external_thread_id == "om-root"
    assert inbound.context.is_top_level_mention is False


@pytest.mark.asyncio
async def test_feishu_runner_streams_the_answer_into_a_card() -> None:
    """The turn is acknowledged with a reaction (no placeholder message), the
    answer streams into a Feishu streaming card, and the reaction is cleared
    once the card is closed."""
    config = FeishuLongConnectionConfig(
        channel_instance_id="feishu-main",
        owner_user_id="u1",
        agent_slug="valuz-helper",
        app_id="cli_app_1",
        app_secret="app-secret",
    )
    inbound = InboundChannelMessage(
        text="你好",
        context=inbound_from_sdk_event(_message_event(), config).context,
        params={"query": "你好", "content": "你好"},
        channel_context={"platform": "feishu"},
    )
    replies: list[str] = []
    reactions: list[tuple[str, str]] = []
    removed: list[tuple[str, str]] = []
    pushes: list[tuple[str, bool]] = []

    async def dispatch(message: InboundChannelMessage) -> ChannelIngressResult:
        assert message.text == "你好"
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="valuz-helper",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    async def reply_sender(
        _config: FeishuLongConnectionConfig,
        message: InboundChannelMessage,
        content: str,
    ) -> str | None:
        replies.append(content)
        return "om-reply"

    async def reaction_adder(
        _config: FeishuLongConnectionConfig,
        message_id: str,
        emoji: str,
    ) -> str | None:
        reactions.append((message_id, emoji))
        return "reaction-1"

    async def reaction_remover(
        _config: FeishuLongConnectionConfig,
        message_id: str,
        reaction_id: str,
    ) -> None:
        removed.append((message_id, reaction_id))

    class _FakeCard:
        async def push(self, content: str, *, final: bool) -> None:
            pushes.append((content, final))

    async def card_opener(
        _config: FeishuLongConnectionConfig,
        _message: InboundChannelMessage,
    ):
        return _FakeCard()

    async def stream_session_events(user_id: str, session_id: str):
        assert user_id == "u1"
        assert session_id == "session-1"
        yield SimpleNamespace(type="text_delta", data={"text": "Hel"})
        yield SimpleNamespace(type="text_delta", data={"text": "lo"})
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = FeishuLongConnectionRunner(
        config,
        dispatch=dispatch,
        reply_sender=reply_sender,
        reaction_adder=reaction_adder,
        reaction_remover=reaction_remover,
        card_stream_opener=card_opener,
        session_event_stream_factory=stream_session_events,
    )

    await runner._dispatch_event(inbound)

    assert reactions == [("om-msg", feishu_runtime.ACK_REACTION_EMOJI)]
    assert removed == [("om-msg", "reaction-1")]
    assert replies == []  # no placeholder, no duplicate final message
    assert pushes[-1] == ("Hello", True)  # closed on the complete answer


@pytest.mark.asyncio
async def test_feishu_runner_keeps_every_segment_of_a_multi_step_answer() -> None:
    """One ``assistant_message`` per segment: the card must close on the whole
    answer, not on whatever the model said after its last tool call."""
    config = FeishuLongConnectionConfig(
        channel_instance_id="feishu-main",
        owner_user_id="u1",
        agent_slug="valuz-helper",
        app_id="cli_app_1",
        app_secret="app-secret",
    )
    inbound = InboundChannelMessage(
        text="查一下这个账号",
        context=inbound_from_sdk_event(_message_event(), config).context,
        params={"query": "查一下这个账号", "content": "查一下这个账号"},
        channel_context={"platform": "feishu"},
    )
    pushes: list[tuple[str, bool]] = []

    async def dispatch(_message: InboundChannelMessage) -> ChannelIngressResult:
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="valuz-helper",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    class _FakeCard:
        async def push(self, content: str, *, final: bool) -> None:
            pushes.append((content, final))

    async def card_opener(
        _config: FeishuLongConnectionConfig,
        _message: InboundChannelMessage,
    ):
        return _FakeCard()

    async def noop_reaction(*_args, **_kwargs):
        return None

    async def stream_session_events(_user_id: str, _session_id: str):
        yield SimpleNamespace(type="text_delta", data={"text": "当前可用点数：30,187"})
        yield SimpleNamespace(type="assistant_message", data={"text": "当前可用点数：30,187"})
        yield SimpleNamespace(type="tool_use", data={"id": "toolu-1", "name": "clickhouse"})
        # A subagent streams onto the same tap — it belongs to the tool card,
        # not to the answer.
        yield SimpleNamespace(
            type="assistant_message",
            data={"text": "扫表中", "parent_tool_use_id": "toolu-1"},
        )
        yield SimpleNamespace(
            type="assistant_message", data={"text": "补一点：扣点是单条记录扣减。"}
        )
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = FeishuLongConnectionRunner(
        config,
        dispatch=dispatch,
        reply_sender=noop_reaction,
        reaction_adder=noop_reaction,
        reaction_remover=noop_reaction,
        card_stream_opener=card_opener,
        session_event_stream_factory=stream_session_events,
    )

    await runner._dispatch_event(inbound)

    assert pushes[-1] == ("当前可用点数：30,187\n\n补一点：扣点是单条记录扣减。", True)


@pytest.mark.asyncio
async def test_feishu_runner_falls_back_to_text_when_card_unavailable() -> None:
    """An app without the cardkit permission still answers — the sink degrades
    to editing a plain text reply for the rest of the turn."""
    config = FeishuLongConnectionConfig(
        channel_instance_id="feishu-main",
        owner_user_id="u1",
        agent_slug="valuz-helper",
        app_id="cli_app_1",
        app_secret="app-secret",
    )
    inbound = InboundChannelMessage(
        text="你好",
        context=inbound_from_sdk_event(_message_event(), config).context,
        params={"query": "你好", "content": "你好"},
        channel_context={"platform": "feishu"},
    )
    replies: list[str] = []
    patches: list[tuple[str, str]] = []

    async def dispatch(_message: InboundChannelMessage) -> ChannelIngressResult:
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="valuz-helper",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    async def reply_sender(
        _config: FeishuLongConnectionConfig,
        _message: InboundChannelMessage,
        content: str,
    ) -> str | None:
        replies.append(content)
        return "om-reply"

    async def reply_updater(
        _config: FeishuLongConnectionConfig,
        message_id: str,
        content: str,
    ) -> None:
        patches.append((message_id, content))

    async def card_opener(
        _config: FeishuLongConnectionConfig,
        _message: InboundChannelMessage,
    ):
        return None  # e.g. missing cardkit permission

    async def noop_reaction(*_args, **_kwargs):
        return None

    async def stream_session_events(_user_id: str, _session_id: str):
        yield SimpleNamespace(type="assistant_message", data={"text": "Hello"})
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = FeishuLongConnectionRunner(
        config,
        dispatch=dispatch,
        reply_sender=reply_sender,
        reply_updater=reply_updater,
        reaction_adder=noop_reaction,
        reaction_remover=noop_reaction,
        card_stream_opener=card_opener,
        session_event_stream_factory=stream_session_events,
    )

    await runner._dispatch_event(inbound)

    assert replies == ["Hello"]  # sent once, then edited in place
    assert patches == [("om-reply", "Hello")]


def test_feishu_event_handler_ignores_p2p_chat_entered_event() -> None:
    message_events: list[object] = []
    handler = feishu_runtime._build_event_handler(
        FeishuLongConnectionConfig(
            channel_instance_id="feishu-main",
            owner_user_id="u1",
            agent_slug="valuz-helper",
            app_id="cli_app_1",
            app_secret="app-secret",
        ),
        message_events.append,
    )

    handler._do_without_validation(
        json.dumps(
            {
                "schema": "2.0",
                "header": {
                    "event_id": "evt-p2p-1",
                    "event_type": "im.chat.access_event.bot_p2p_chat_entered_v1",
                },
                "event": {
                    "operator_id": {"open_id": "ou-user"},
                    "chat_id": "oc-chat",
                },
            }
        ).encode("utf-8")
    )

    assert message_events == []


def _message_event() -> P2ImMessageReceiveV1:
    return P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt-1",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou-user"}},
                "message": {
                    "message_id": "om-msg",
                    "chat_id": "oc-chat",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好"}),
                },
            },
        }
    )


async def test_load_enabled_configs_uses_row_owner_not_local_identity(
    tmp_path, monkeypatch
) -> None:
    """Bindings created under an edition user id (e.g. a logged-in commercial
    user) must load with that owner — resolving the device-fingerprint local
    id here would silently produce zero connections."""
    from contextlib import asynccontextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from valuz_agent.infra.database import Base
    from valuz_agent.modules.channels.datastore import AgentChannelBindingDatastore
    from valuz_agent.modules.channels.models import AgentChannelBindingRow

    db_file = tmp_path / "channels.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=[AgentChannelBindingRow.__table__])
    sessionmaker_ = async_sessionmaker(
        bind=create_async_engine(f"sqlite+aiosqlite:///{db_file}"),
        expire_on_commit=False,
    )

    async with sessionmaker_() as db:
        await AgentChannelBindingDatastore(db).upsert(
            user_id="commercial-user-1",
            platform="feishu",
            agent_slug="valuz-helper",
            channel_instance_id="feishu-main",
            bot_id="cli_app_1",
            secret_ref="channel/feishu/valuz-helper",
            enabled=True,
        )
        await db.commit()

    @asynccontextmanager
    async def fake_unit_of_work(**_kwargs):
        async with sessionmaker_() as session:
            yield session

    secret_reads: list[tuple[str, str]] = []

    def fake_secret_get(user_id: str, ref: str) -> str:
        secret_reads.append((user_id, ref))
        return json.dumps({"app_secret": "s3cret"})

    monkeypatch.setattr(feishu_runtime, "async_unit_of_work", fake_unit_of_work)
    monkeypatch.setattr(feishu_runtime.secret_store, "get", fake_secret_get)

    configs = await feishu_runtime._load_enabled_feishu_configs()

    assert [config.owner_user_id for config in configs] == ["commercial-user-1"]
    assert configs[0].app_id == "cli_app_1"
    assert configs[0].app_secret == "s3cret"
    assert secret_reads == [("commercial-user-1", "channel/feishu/valuz-helper")]


def test_inbound_marks_a_p2p_message_as_a_direct_chat() -> None:
    """The routing rule that keeps DMs out of projects needs the adapter to
    say which kind of chat the message came from."""
    for chat_type, expected in (("p2p", True), ("group", False)):
        event = P2ImMessageReceiveV1(
            {
                "schema": "2.0",
                "header": {"event_id": "evt-3", "event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou-user"}},
                    "message": {
                        "message_id": "om-msg-3",
                        "chat_id": "oc-chat",
                        "chat_type": chat_type,
                        "message_type": "text",
                        "content": json.dumps({"text": "你好"}),
                    },
                },
            }
        )
        inbound = inbound_from_sdk_event(
            event,
            FeishuLongConnectionConfig(
                channel_instance_id="feishu-main",
                owner_user_id="u1",
                agent_slug="valuz-helper",
                app_id="cli_app_1",
                app_secret="app-secret",
            ),
        )
        assert inbound.context.is_direct_chat is expected


@pytest.mark.asyncio
async def test_membership_reads_feishu_string_counts() -> None:
    """Feishu returns user_count as a string, and ``bool("0")`` is True — which
    read every empty group as occupied, hiding the join link on exactly the
    groups that needed it."""
    from valuz_agent.integrations.feishu_long_connection import (
        FeishuChat,
        _with_membership,
    )

    class _Resp:
        def __init__(self, count: str) -> None:
            self.data = SimpleNamespace(user_count=count)

        def success(self) -> bool:
            return True

    class _FakeClient:
        def __init__(self) -> None:
            counts = {"oc-empty": "0", "oc-joined": "2"}
            self.im = SimpleNamespace(
                v1=SimpleNamespace(
                    chat=SimpleNamespace(
                        aget=lambda req: self._get(req, counts),
                    )
                )
            )

        async def _get(self, req, counts):
            return _Resp(counts[req.chat_id])

    resolved = await _with_membership(
        _FakeClient(),
        [
            FeishuChat(chat_id="oc-empty", name="空群", bot_owned=True),
            FeishuChat(chat_id="oc-joined", name="有人", bot_owned=True),
            FeishuChat(chat_id="oc-theirs", name="别人的", bot_owned=False),
        ],
    )

    assert [c.has_people for c in resolved] == [False, True, True]
