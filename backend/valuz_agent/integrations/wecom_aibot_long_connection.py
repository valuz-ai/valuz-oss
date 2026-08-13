"""Enterprise WeChat AIBot long-connection runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from valuz_agent.infra import secret_store
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.channels.adapters.base import InboundChannelMessage
from valuz_agent.modules.channels.adapters.wecom_aibot import (
    WECOM_AIBOT_EVENT_CALLBACK_CMD,
    WECOM_AIBOT_HEARTBEAT_CMD,
    WECOM_AIBOT_MSG_CALLBACK_CMD,
    WECOM_AIBOT_SUBSCRIBE_CMD,
    Frame,
    WeComAIBotConfig,
    build_heartbeat_frame,
    build_stream_reply_frame,
    build_subscribe_frame,
    frame_req_id,
    generate_req_id,
    is_success_ack,
    parse_wecom_aibot_frame,
)
from valuz_agent.modules.channels.config import ChannelConfigError, load_wecom_aibot_config
from valuz_agent.modules.channels.datastore import AgentChannelBindingDatastore
from valuz_agent.modules.channels.reply_text import (
    AssistantReplyText,
    session_event_type_and_data,
)
from valuz_agent.modules.channels.schemas import ChannelRouteDecisionKind
from valuz_agent.modules.channels.service import ChannelIngressResult

logger = logging.getLogger(__name__)

CHANNEL_EXECUTION_ERROR_MESSAGE = "执行异常，任务没有成功提交，请稍后重试或联系管理员。"
CHANNEL_NO_ROUTE_MESSAGE = "消息已收到，但没有找到可执行的项目绑定。"
CHANNEL_RECEIVED_MESSAGE = "收到，正在处理。"
CHANNEL_QUEUED_MESSAGE = "已加入队列，当前任务结束后会继续处理。"
CHANNEL_EMPTY_RESULT_MESSAGE = "执行完成，但没有返回文本结果。"
WECOM_AIBOT_DISCONNECTED_EVENT = "disconnected_event"


class WeComAIBotServerDisconnectedError(ConnectionError):
    """Raised when WeCom tells this old long connection to close."""


class WebSocketLike(Protocol):
    async def send(self, data: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


WebSocketFactory = Callable[[str], WebSocketLike | Awaitable[WebSocketLike]]
DispatchInbound = Callable[[InboundChannelMessage], Awaitable[ChannelIngressResult | None]]
ReqIdFactory = Callable[[str], str]
AuthenticatedCallback = Callable[[], None]
SessionEventStreamFactory = Callable[[str, str], AsyncIterator[Any]]


@dataclass(frozen=True, slots=True)
class WeComAIBotRuntimeStatus:
    status: str
    connected: bool = False
    last_error: str | None = None


class WeComAIBotLongConnectionRunner:
    def __init__(
        self,
        config: WeComAIBotConfig,
        *,
        dispatch: DispatchInbound,
        websocket_factory: WebSocketFactory | None = None,
        req_id_factory: ReqIdFactory = generate_req_id,
        on_authenticated: AuthenticatedCallback | None = None,
        session_event_stream_factory: SessionEventStreamFactory | None = None,
        heartbeat_interval_s: float = 30.0,
        auth_timeout_s: float = 10.0,
        reply_ack_timeout_s: float = 5.0,
    ) -> None:
        self._config = config
        self._dispatch = dispatch
        self._websocket_factory = websocket_factory or _connect_websocket
        self._req_id_factory = req_id_factory
        self._on_authenticated = on_authenticated
        self._session_event_stream_factory = (
            session_event_stream_factory or _subscribe_session_events
        )
        self._heartbeat_interval_s = heartbeat_interval_s
        self._auth_timeout_s = auth_timeout_s
        self._reply_ack_timeout_s = reply_ack_timeout_s
        self._deferred_frames: deque[Frame] = deque()
        self._send_lock = asyncio.Lock()

    async def run_once(self, stop_event: asyncio.Event) -> None:
        websocket = await _maybe_await(self._websocket_factory(self._config.ws_url))
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await self._authenticate(websocket)
            self._on_authenticated and self._on_authenticated()
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(websocket, stop_event),
                name="wecom-aibot-heartbeat",
            )
            await self._receive_loop(websocket, stop_event)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await _await_cancelled(heartbeat_task)
            await websocket.close()

    async def _authenticate(self, websocket: WebSocketLike) -> None:
        req_id = self._req_id_factory(WECOM_AIBOT_SUBSCRIBE_CMD)
        await self._send_frame(
            websocket,
            build_subscribe_frame(
                bot_id=self._config.bot_id,
                secret=self._config.secret,
                req_id=req_id,
            ),
        )
        while True:
            frame = await asyncio.wait_for(_recv_frame(websocket), timeout=self._auth_timeout_s)
            if frame_req_id(frame) != req_id:
                logger.debug("ignoring frame before WeCom AIBot auth ack: %s", frame.get("cmd"))
                continue
            if not is_success_ack(frame, req_id):
                raise ChannelConfigError(
                    f"WeCom AIBot auth failed: {frame.get('errmsg') or frame.get('errcode')}"
                )
            logger.info("WeCom AIBot authenticated: channel=%s", self._config.channel_instance_id)
            return

    async def _receive_loop(self, websocket: WebSocketLike, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                frame = await self._recv_next_frame(websocket)
            except EOFError:
                return
            cmd = frame.get("cmd")
            if cmd == WECOM_AIBOT_MSG_CALLBACK_CMD:
                inbound = parse_wecom_aibot_frame(frame, self._config)
                if inbound is not None:
                    await self._try_send_channel_reply(
                        websocket,
                        inbound,
                        CHANNEL_RECEIVED_MESSAGE,
                        False,
                    )
                    try:
                        result = await self._dispatch(inbound)
                    except Exception:
                        logger.exception(
                            "WeCom AIBot inbound dispatch failed: channel=%s agent=%s msg=%s",
                            self._config.channel_instance_id,
                            self._config.agent_slug,
                            inbound.context.external_message_id,
                        )
                        await self._try_send_channel_reply(
                            websocket,
                            inbound,
                            CHANNEL_EXECUTION_ERROR_MESSAGE,
                            True,
                        )
                    else:
                        await self._stream_dispatch_result(websocket, inbound, result)
                continue
            if cmd == WECOM_AIBOT_EVENT_CALLBACK_CMD:
                event = frame.get("body")
                if _event_type(frame) == WECOM_AIBOT_DISCONNECTED_EVENT:
                    raise WeComAIBotServerDisconnectedError(
                        "WeCom AIBot server sent disconnected_event"
                    )
                logger.info("WeCom AIBot event callback ignored: %s", event)
                continue
            if frame_req_id(frame).startswith(WECOM_AIBOT_HEARTBEAT_CMD):
                continue
            logger.debug("WeCom AIBot ignored frame: %s", cmd or frame_req_id(frame))

    async def _recv_next_frame(self, websocket: WebSocketLike) -> Frame:
        if self._deferred_frames:
            return self._deferred_frames.popleft()
        return await _recv_frame(websocket)

    async def _try_send_channel_reply(
        self,
        websocket: WebSocketLike,
        inbound: InboundChannelMessage,
        content: str,
        finish: bool,
    ) -> bool:
        try:
            await self._send_channel_reply(websocket, inbound, content, finish)
        except (ChannelConfigError, TimeoutError) as exc:
            logger.warning("WeCom AIBot reply was not acknowledged: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001 - channel replies are best-effort
            logger.warning("WeCom AIBot reply failed: %s", exc, exc_info=True)
            return False
        return True

    async def _send_channel_reply(
        self,
        websocket: WebSocketLike,
        inbound: InboundChannelMessage,
        content: str,
        finish: bool,
    ) -> None:
        req_id = inbound.context.request_id
        stream_id = _reply_stream_id(inbound)
        if not req_id or not stream_id:
            logger.warning("WeCom AIBot cannot reply to message without req_id/msgid")
            return
        await self._send_frame(
            websocket,
            build_stream_reply_frame(
                req_id=req_id,
                stream_id=stream_id,
                content=content,
                finish=finish,
            ),
        )
        await self._wait_for_reply_ack(websocket, req_id)

    async def _wait_for_reply_ack(self, websocket: WebSocketLike, req_id: str) -> None:
        while True:
            frame = await asyncio.wait_for(
                _recv_frame(websocket),
                timeout=self._reply_ack_timeout_s,
            )
            if frame.get("cmd") or frame_req_id(frame) != req_id:
                self._deferred_frames.append(frame)
                continue
            if int(frame.get("errcode", -1)) != 0:
                raise ChannelConfigError(
                    f"WeCom AIBot reply ack failed: {frame.get('errmsg') or frame.get('errcode')}"
                )
            return

    async def _stream_dispatch_result(
        self,
        websocket: WebSocketLike,
        inbound: InboundChannelMessage,
        result: ChannelIngressResult | None,
    ) -> None:
        if result is not None and result.direct_reply:
            # A binding command: configuration, answered directly (no session).
            await self._try_send_channel_reply(
                websocket,
                inbound,
                result.direct_reply,
                True,
            )
            return

        if result is not None and result.decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION:
            await self._try_send_channel_reply(
                websocket,
                inbound,
                CHANNEL_QUEUED_MESSAGE,
                True,
            )
            return

        session_id = result.session_id if result is not None else None
        if not session_id:
            await self._try_send_channel_reply(
                websocket,
                inbound,
                _route_feedback_message(result),
                True,
            )
            return

        user_id = inbound.context.user_id
        answer = AssistantReplyText()
        logger.info(
            "WeCom AIBot streaming session output: channel=%s agent=%s session=%s",
            self._config.channel_instance_id,
            self._config.agent_slug,
            session_id,
        )
        try:
            async for event in self._session_event_stream_factory(user_id, session_id):
                event_type, data = session_event_type_and_data(event)
                logger.debug(
                    "WeCom AIBot session event: channel=%s agent=%s session=%s type=%s",
                    self._config.channel_instance_id,
                    self._config.agent_slug,
                    session_id,
                    event_type,
                )
                if answer.observe(event_type, data):
                    await self._try_send_channel_reply(
                        websocket,
                        inbound,
                        answer.text,
                        False,
                    )
                    continue
                if event_type == "session_error":
                    logger.warning(
                        "WeCom AIBot observed session error: channel=%s agent=%s session=%s",
                        self._config.channel_instance_id,
                        self._config.agent_slug,
                        session_id,
                    )
                    await self._try_send_channel_reply(
                        websocket,
                        inbound,
                        CHANNEL_EXECUTION_ERROR_MESSAGE,
                        True,
                    )
                    return
                if _is_terminal_session_event(event_type, data):
                    await self._try_send_channel_reply(
                        websocket,
                        inbound,
                        answer.text.strip() or CHANNEL_EMPTY_RESULT_MESSAGE,
                        True,
                    )
                    return
        except Exception:
            logger.exception(
                "WeCom AIBot session event stream failed: channel=%s agent=%s session=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
                session_id,
            )
            await self._try_send_channel_reply(
                websocket,
                inbound,
                CHANNEL_EXECUTION_ERROR_MESSAGE,
                True,
            )
            return

        if answer.text:
            await self._try_send_channel_reply(websocket, inbound, answer.text, True)

    async def _heartbeat_loop(self, websocket: WebSocketLike, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._heartbeat_interval_s)
            except TimeoutError:
                await self._send_frame(
                    websocket,
                    build_heartbeat_frame(self._req_id_factory(WECOM_AIBOT_HEARTBEAT_CMD)),
                )

    async def _send_frame(self, websocket: WebSocketLike, frame: Frame) -> None:
        async with self._send_lock:
            await _send_frame(websocket, frame)


class WeComAIBotSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}
        self._statuses: dict[str, WeComAIBotRuntimeStatus] = {}
        self._startup_task: asyncio.Task[None] | None = None

    @property
    def status(self) -> WeComAIBotRuntimeStatus:
        if not self._statuses:
            return WeComAIBotRuntimeStatus(status="stopped")
        if any(status.connected for status in self._statuses.values()):
            return WeComAIBotRuntimeStatus(status="connected", connected=True)
        return next(iter(self._statuses.values()))

    def status_for(self, agent_slug: str) -> WeComAIBotRuntimeStatus:
        return self._statuses.get(agent_slug, WeComAIBotRuntimeStatus(status="stopped"))

    async def startup(self) -> None:
        if self._startup_task is not None and not self._startup_task.done():
            return
        self._startup_task = asyncio.create_task(
            self._startup_connect(),
            name="wecom-aibot-startup-connect",
        )

    async def restart(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()
        from valuz_agent.modules.channels.config import agent_channels_active

        if not agent_channels_active():
            return
        configs = await _load_enabled_wecom_aibot_configs()
        if not configs:
            return
        for config in configs:
            stop_event = asyncio.Event()
            self._stop_events[config.agent_slug] = stop_event
            self._statuses[config.agent_slug] = WeComAIBotRuntimeStatus(status="connecting")
            runner = WeComAIBotLongConnectionRunner(
                config,
                dispatch=_dispatch_to_channel_ingress,
                on_authenticated=self._mark_connected_callback(config.agent_slug),
            )
            self._tasks[config.agent_slug] = asyncio.create_task(
                self._run_loop(config.agent_slug, runner, stop_event),
                name=f"wecom-aibot-{config.agent_slug}",
            )

    async def shutdown(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()

    async def _shutdown_connections(self) -> None:
        for stop_event in self._stop_events.values():
            stop_event.set()
        for task in self._tasks.values():
            task.cancel()
            await _await_cancelled(task)
        self._tasks.clear()
        self._stop_events.clear()
        self._statuses.clear()

    async def _cancel_startup_task(self) -> None:
        task = self._startup_task
        if task is None:
            return
        if task is asyncio.current_task():
            return
        self._startup_task = None
        if not task.done():
            task.cancel()
            await _await_cancelled(task)

    async def _startup_connect(self) -> None:
        try:
            await asyncio.sleep(0)
            await self.restart()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - channels must never break app startup
            self._statuses["startup"] = WeComAIBotRuntimeStatus(
                status="error",
                connected=False,
                last_error=str(exc),
            )
            logger.warning("WeCom AIBot startup connection failed: %s", exc, exc_info=True)
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None

    async def _run_loop(
        self,
        agent_slug: str,
        runner: WeComAIBotLongConnectionRunner,
        stop_event: asyncio.Event,
    ) -> None:
        backoff_s = 1.0
        while not stop_event.is_set():
            try:
                self._statuses[agent_slug] = WeComAIBotRuntimeStatus(status="connecting")
                await runner.run_once(stop_event)
                if stop_event.is_set():
                    return
                raise ConnectionError("WeCom AIBot connection closed")
            except asyncio.CancelledError:
                raise
            except WeComAIBotServerDisconnectedError as exc:
                self._statuses[agent_slug] = WeComAIBotRuntimeStatus(
                    status="disconnected",
                    connected=False,
                    last_error=str(exc),
                )
                logger.info("WeCom AIBot server disconnected old connection: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001 - background runner must survive outages
                self._statuses[agent_slug] = WeComAIBotRuntimeStatus(
                    status="error",
                    connected=False,
                    last_error=str(exc),
                )
                logger.warning("WeCom AIBot connection failed: %s", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff_s)
                except TimeoutError:
                    backoff_s = min(backoff_s * 2, 30.0)

    def _mark_connected(self, agent_slug: str) -> None:
        self._statuses[agent_slug] = WeComAIBotRuntimeStatus(status="connected", connected=True)

    def _mark_connected_callback(self, agent_slug: str) -> AuthenticatedCallback:
        def callback() -> None:
            self._mark_connected(agent_slug)

        return callback


async def _dispatch_to_channel_ingress(
    inbound: InboundChannelMessage,
) -> ChannelIngressResult | None:
    from valuz_agent.api.deps import get_channel_ingress_service

    user_id = inbound.context.user_id
    if not user_id:
        logger.warning("WeCom AIBot inbound missing owner user id; message ignored")
        return None
    service_gen = get_channel_ingress_service()
    service = await service_gen.__anext__()
    try:
        result = await service.handle_inbound_message(user_id=user_id, inbound=inbound)
        logger.info(
            "WeCom AIBot routed message: decision=%s session=%s",
            result.decision.kind.value,
            result.session_id,
        )
        return result
    finally:
        try:
            await service_gen.__anext__()
        except StopAsyncIteration:
            pass


async def _load_enabled_wecom_aibot_configs() -> list[WeComAIBotConfig]:
    # Owner comes from each binding row, never from ambient process identity:
    # editions that override request identity (e.g. a logged-in commercial
    # user) store bindings under that user, which the device-fingerprint
    # local id would never match — the supervisor would silently load nothing.
    async with async_unit_of_work() as db:
        rows = await AgentChannelBindingDatastore(db).list_enabled(
            platform="wecom_aibot",
        )
    configs: list[WeComAIBotConfig] = []
    for row in rows:
        secret = secret_store.get(row.owner_user_id, row.secret_ref) if row.secret_ref else None
        if not secret:
            logger.warning("WeCom AIBot binding for %s has no stored secret", row.agent_slug)
            continue
        configs.append(
            WeComAIBotConfig(
                channel_instance_id=row.channel_instance_id,
                owner_user_id=row.owner_user_id,
                agent_slug=row.agent_slug,
                bot_id=row.bot_id,
                secret=secret,
                bot_name=row.bot_name,
                ws_url=row.ws_url or "wss://openws.work.weixin.qq.com",
            )
        )
    if configs:
        return configs
    try:
        return [load_wecom_aibot_config()]
    except ChannelConfigError as exc:
        logger.info("WeCom AIBot not started: %s", exc)
        return []


async def _connect_websocket(url: str) -> WebSocketLike:
    import websockets

    return await websockets.connect(
        url,
        compression=None,
        ping_interval=None,
        ping_timeout=None,
        proxy=None,
    )


async def _subscribe_session_events(user_id: str, session_id: str) -> AsyncIterator[Any]:
    from valuz_agent.adapters import kernel_client

    async for event in kernel_client.subscribe_session_events(user_id, session_id):
        yield event


async def _send_frame(websocket: WebSocketLike, frame: Frame) -> None:
    await websocket.send(json.dumps(frame, ensure_ascii=False))


async def _recv_frame(websocket: WebSocketLike) -> Frame:
    raw = await websocket.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("WeCom AIBot frame must be a JSON object")
    return data


def _reply_stream_id(inbound: InboundChannelMessage) -> str | None:
    source_id = inbound.context.external_message_id or inbound.context.request_id
    return f"stream-{source_id}" if source_id else None


def _event_type(frame: Frame) -> str:
    body = frame.get("body")
    if not isinstance(body, dict):
        return ""
    event = body.get("event")
    if not isinstance(event, dict):
        return ""
    return str(event.get("eventtype") or "")


def _is_terminal_session_event(event_type: str, data: dict[str, Any]) -> bool:
    if event_type == "session_idle":
        return True
    if event_type != "session_update":
        return False
    status = data.get("status")
    return status in {"idle", "terminated"}


def _route_feedback_message(result: ChannelIngressResult | None) -> str:
    if result is None:
        return CHANNEL_NO_ROUTE_MESSAGE
    decision = result.decision
    if decision.kind == ChannelRouteDecisionKind.ASK_PROJECT:
        candidate_names = [
            candidate.project_name or candidate.project_id for candidate in decision.candidates
        ]
        if candidate_names:
            return "这个 Agent 派驻了多个项目，请在消息里说明项目名后再试。可选项目：" + "、".join(
                candidate_names
            )
        return "这个 Agent 派驻了多个项目，请在消息里说明项目名后再试。"
    if decision.kind == ChannelRouteDecisionKind.NOT_DEPLOYED:
        return "这个 Agent 还没有派驻到项目，暂时无法执行。"
    return CHANNEL_NO_ROUTE_MESSAGE


async def _maybe_await(value: WebSocketLike | Awaitable[WebSocketLike]) -> WebSocketLike:
    if inspect.isawaitable(value):
        return await value
    return value


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


wecom_aibot_supervisor = WeComAIBotSupervisor()


__all__ = [
    "CHANNEL_EXECUTION_ERROR_MESSAGE",
    "CHANNEL_EMPTY_RESULT_MESSAGE",
    "CHANNEL_NO_ROUTE_MESSAGE",
    "CHANNEL_QUEUED_MESSAGE",
    "CHANNEL_RECEIVED_MESSAGE",
    "WeComAIBotLongConnectionRunner",
    "WeComAIBotRuntimeStatus",
    "WeComAIBotServerDisconnectedError",
    "WeComAIBotSupervisor",
    "wecom_aibot_supervisor",
]
