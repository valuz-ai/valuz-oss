from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
import websockets

from valuz_agent.integrations import wecom_aibot_long_connection as wecom_runtime
from valuz_agent.integrations.wecom_aibot_long_connection import (
    CHANNEL_EXECUTION_ERROR_MESSAGE,
    CHANNEL_QUEUED_MESSAGE,
    CHANNEL_RECEIVED_MESSAGE,
    WeComAIBotLongConnectionRunner,
    WeComAIBotServerDisconnectedError,
)
from valuz_agent.modules.channels import (
    AgentChannelRouteDecision,
    AgentPlacement,
    ChannelRouteDecisionKind,
)
from valuz_agent.modules.channels.adapters.base import InboundChannelMessage
from valuz_agent.modules.channels.adapters.wecom_aibot import (
    WECOM_AIBOT_RESPOND_MSG_CMD,
    WeComAIBotConfig,
)
from valuz_agent.modules.channels.service import ChannelIngressResult


class FakeWebSocket:
    def __init__(self, incoming: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming = [json.dumps(frame) for frame in incoming]
        self._pending_reply_ack: str | None = None
        self.closed = False

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        if frame.get("cmd") == WECOM_AIBOT_RESPOND_MSG_CMD:
            req_id = frame.get("headers", {}).get("req_id")
            if self._pending_reply_ack is not None:
                raise AssertionError("reply sent before previous reply ack was received")
            self._pending_reply_ack = str(req_id)
        self.sent.append(frame)

    async def recv(self) -> str:
        if self._pending_reply_ack is not None:
            req_id = self._pending_reply_ack
            self._pending_reply_ack = None
            return json.dumps({"headers": {"req_id": req_id}, "errcode": 0, "errmsg": "ok"})
        if self._incoming:
            return self._incoming.pop(0)
        raise EOFError

    async def close(self) -> None:
        self.closed = True


class ReplyFailingWebSocket(FakeWebSocket):
    async def send(self, data: str) -> None:
        frame = json.loads(data)
        if frame.get("cmd") == WECOM_AIBOT_RESPOND_MSG_CMD:
            raise ConnectionError("socket already closing")
        await super().send(data)


@pytest.mark.asyncio
async def test_wecom_aibot_connect_disables_proxy_and_protocol_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    fake_ws = FakeWebSocket([])

    async def fake_connect(url: str, **kwargs: Any) -> FakeWebSocket:
        captured["url"] = url
        captured.update(kwargs)
        return fake_ws

    monkeypatch.setattr(websockets, "connect", fake_connect)

    connected = await wecom_runtime._connect_websocket("wss://wecom.example.test")

    assert connected is fake_ws
    assert captured["url"] == "wss://wecom.example.test"
    assert captured["proxy"] is None
    assert captured["ping_interval"] is None
    assert captured["ping_timeout"] is None
    assert captured["compression"] is None


@pytest.mark.asyncio
async def test_wecom_aibot_supervisor_startup_schedules_background_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = wecom_runtime.WeComAIBotSupervisor()
    started = asyncio.Event()
    release = asyncio.Event()

    async def restart() -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(supervisor, "restart", restart)

    await asyncio.wait_for(supervisor.startup(), timeout=0.1)
    await asyncio.wait_for(started.wait(), timeout=0.1)
    release.set()
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_wecom_aibot_runner_still_dispatches_when_immediate_reply_fails() -> None:
    fake_ws = ReplyFailingWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "@RobotA 改一下项目问题"},
                },
            },
        ]
    )
    dispatched: list[InboundChannelMessage] = []

    async def dispatch(message: InboundChannelMessage) -> ChannelIngressResult:
        dispatched.append(message)
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="developer",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    async def stream_session_events(_user_id: str, _session_id: str):
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        session_event_stream_factory=stream_session_events,
        heartbeat_interval_s=999,
    )

    await runner.run_once(asyncio.Event())

    assert fake_ws.closed is True
    assert [message.text for message in dispatched] == ["改一下项目问题"]


@pytest.mark.asyncio
async def test_wecom_aibot_runner_streams_agent_output_after_dispatch() -> None:
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "@RobotA 改一下项目问题"},
                },
            },
        ]
    )
    dispatched: list[InboundChannelMessage] = []

    async def dispatch(message: InboundChannelMessage) -> ChannelIngressResult:
        dispatched.append(message)
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="developer",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    async def stream_session_events(user_id: str, session_id: str):
        assert user_id == "u1"
        assert session_id == "session-1"
        yield SimpleNamespace(type="text_delta", data={"text": "Hel"})
        yield SimpleNamespace(type="text_delta", data={"text": "lo"})
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        session_event_stream_factory=stream_session_events,
        heartbeat_interval_s=999,
    )

    await runner.run_once(asyncio.Event())

    assert fake_ws.sent[0] == {
        "cmd": "aibot_subscribe",
        "headers": {"req_id": "aibot_subscribe-fixed"},
        "body": {"bot_id": "bot-1", "secret": "secret-1"},
    }
    assert fake_ws.sent[1] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": False,
                "content": CHANNEL_RECEIVED_MESSAGE,
            },
        },
    }
    assert fake_ws.sent[2] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": False,
                "content": "Hel",
            },
        },
    }
    assert fake_ws.sent[3] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": False,
                "content": "Hello",
            },
        },
    }
    assert fake_ws.sent[4] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": True,
                "content": "Hello",
            },
        },
    }
    assert fake_ws.closed is True
    assert [message.text for message in dispatched] == ["改一下项目问题"]


@pytest.mark.asyncio
async def test_wecom_aibot_runner_keeps_every_segment_of_a_multi_step_answer() -> None:
    """A turn that calls a tool and keeps talking emits one ``assistant_message``
    per segment. The closing frame must carry the whole answer — the earlier
    segments were on screen while streaming and may not vanish at the end."""
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "@RobotA 查一下这个账号"},
                },
            },
        ]
    )

    async def dispatch(_message: InboundChannelMessage) -> ChannelIngressResult:
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.REUSE_SESSION,
                agent_slug="developer",
                project_id="project-1",
                session_id="session-1",
                reason="existing thread binding",
            ),
            session_id="session-1",
        )

    async def stream_session_events(_user_id: str, _session_id: str):
        yield SimpleNamespace(type="text_delta", data={"text": "当前可用点数："})
        yield SimpleNamespace(type="text_delta", data={"text": "30,187"})
        # The segment seals — its deltas are superseded, not doubled.
        yield SimpleNamespace(type="assistant_message", data={"text": "当前可用点数：30,187"})
        yield SimpleNamespace(type="tool_use", data={"id": "toolu-1", "name": "clickhouse"})
        # A subagent streams onto the same tap; the app shows it inside the
        # tool card, never in the answer.
        yield SimpleNamespace(
            type="text_delta",
            data={"text": "扫表中", "parent_tool_use_id": "toolu-1"},
        )
        yield SimpleNamespace(
            type="assistant_message",
            data={"text": "扫表中", "parent_tool_use_id": "toolu-1"},
        )
        yield SimpleNamespace(type="text_delta", data={"text": "补一点："})
        yield SimpleNamespace(type="text_delta", data={"text": "扣点是单条记录扣减。"})
        yield SimpleNamespace(
            type="assistant_message", data={"text": "补一点：扣点是单条记录扣减。"}
        )
        yield SimpleNamespace(type="session_idle", data={"stop_reason": "end_turn"})

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        session_event_stream_factory=stream_session_events,
        heartbeat_interval_s=999,
    )

    await runner.run_once(asyncio.Event())

    streamed = [
        frame["body"]["stream"]
        for frame in fake_ws.sent
        if frame.get("cmd") == WECOM_AIBOT_RESPOND_MSG_CMD
    ]
    whole_answer = "当前可用点数：30,187\n\n补一点：扣点是单条记录扣减。"
    assert streamed[-1] == {
        "id": "stream-msg-1",
        "finish": True,
        "content": whole_answer,
    }
    # The bubble only ever grows: no frame walks the answer backwards.
    contents = [stream["content"] for stream in streamed[1:]]
    assert contents == [
        "当前可用点数：",
        "当前可用点数：30,187",
        "当前可用点数：30,187\n\n补一点：",
        whole_answer,
        whole_answer,
    ]


@pytest.mark.asyncio
async def test_wecom_aibot_runner_closes_stream_when_route_has_no_session() -> None:
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "@RobotA 改一下项目问题"},
                },
            },
        ]
    )

    async def dispatch(_message: InboundChannelMessage) -> ChannelIngressResult:
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.ASK_PROJECT,
                agent_slug="developer",
                project_id=None,
                session_id=None,
                reason="multiple_deployments",
                candidates=(
                    AgentPlacement("project-a", "Alpha", "developer-a", "developer"),
                    AgentPlacement("project-b", "Beta", "developer-b", "developer"),
                ),
            )
        )

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        heartbeat_interval_s=999,
    )

    await runner.run_once(asyncio.Event())

    assert fake_ws.sent[2] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": True,
                "content": (
                    "这个 Agent 派驻了多个项目，请在消息里说明项目名后再试。"
                    "可选项目：Alpha、Beta"
                ),
            },
        },
    }


@pytest.mark.asyncio
async def test_wecom_aibot_runner_replies_when_message_is_queued() -> None:
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "继续改一下项目问题"},
                    "quote_msg": {"msgid": "stream-old-user-message"},
                },
            },
        ]
    )
    dispatched: list[InboundChannelMessage] = []

    async def dispatch(message: InboundChannelMessage) -> ChannelIngressResult:
        dispatched.append(message)
        return ChannelIngressResult(
            decision=AgentChannelRouteDecision(
                kind=ChannelRouteDecisionKind.QUEUE_SESSION,
                agent_slug="developer",
                project_id="project-1",
                session_id="session-1",
                reason="thread_binding_running",
            ),
            session_id="session-1",
        )

    async def stream_session_events(_user_id: str, _session_id: str):
        raise AssertionError("queued channel messages must not subscribe to session output")
        yield

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        session_event_stream_factory=stream_session_events,
        heartbeat_interval_s=999,
    )

    await runner.run_once(asyncio.Event())

    assert len(dispatched) == 1
    assert dispatched[0].text == "继续改一下项目问题"
    assert dispatched[0].context.is_top_level_mention is False
    assert dispatched[0].context.continuation_hint is True
    assert fake_ws.sent[2] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": True,
                "content": CHANNEL_QUEUED_MESSAGE,
            },
        },
    }


@pytest.mark.asyncio
async def test_wecom_aibot_runner_replies_when_dispatch_fails() -> None:
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-1"},
                "body": {
                    "msgid": "msg-1",
                    "aibotid": "bot-1",
                    "chatid": "chat-1",
                    "chattype": "group",
                    "from": {"userid": "user-1"},
                    "msgtype": "text",
                    "text": {"content": "@RobotA 改一下项目问题"},
                },
            },
        ]
    )
    stop = asyncio.Event()

    async def dispatch(_message: InboundChannelMessage) -> None:
        raise RuntimeError("session backend exploded")

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=dispatch,
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        heartbeat_interval_s=999,
    )

    await runner.run_once(stop)

    assert fake_ws.sent[1] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": False,
                "content": CHANNEL_RECEIVED_MESSAGE,
            },
        },
    }
    assert fake_ws.sent[2] == {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": "stream-msg-1",
                "finish": True,
                "content": CHANNEL_EXECUTION_ERROR_MESSAGE,
            },
        },
    }
    assert fake_ws.closed is True


@pytest.mark.asyncio
async def test_wecom_aibot_runner_treats_disconnected_event_as_server_disconnect() -> None:
    fake_ws = FakeWebSocket(
        [
            {
                "headers": {"req_id": "aibot_subscribe-fixed"},
                "errcode": 0,
                "errmsg": "ok",
            },
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": "event-1"},
                "body": {
                    "msgid": "event-msg-1",
                    "aibotid": "bot-1",
                    "msgtype": "event",
                    "create_time": 1785151774,
                    "event": {"eventtype": "disconnected_event"},
                },
            },
        ]
    )

    runner = WeComAIBotLongConnectionRunner(
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
        dispatch=lambda _message: None,  # type: ignore[arg-type,return-value]
        websocket_factory=lambda _url: fake_ws,
        req_id_factory=lambda prefix: f"{prefix}-fixed",
        heartbeat_interval_s=999,
    )

    try:
        await runner.run_once(asyncio.Event())
    except WeComAIBotServerDisconnectedError:
        pass
    else:
        raise AssertionError("expected server disconnected event to stop the runner")

    assert fake_ws.closed is True
