"""Feishu long-connection runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from valuz_agent.infra import secret_store
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.channels.adapters import (
    FeishuChannelAdapter,
    FeishuChannelConfig,
    InboundChannelMessage,
)
from valuz_agent.modules.channels.config import ChannelConfigError
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
# Retired: the turn is acknowledged with a reaction on the user's message
# (``ACK_REACTION_EMOJI``) instead of a placeholder reply.
CHANNEL_QUEUED_MESSAGE = "已加入队列，当前任务结束后会继续处理。"
CHANNEL_EMPTY_RESULT_MESSAGE = "执行完成，但没有返回文本结果。"
CHANNEL_BIND_PROMPT_MESSAGE = "这个群要绑定到哪个项目？绑定后，群里的对话都会进入该项目。"
CHANNEL_BIND_TRUNCATED_MESSAGE = "（只列出了部分项目，其余请在 Valuz 项目页里绑定。）"
CHANNEL_BIND_HOWTO_MESSAGE = "回复「绑定项目 项目名」即可完成绑定，例如：绑定项目 研究。"


@dataclass(frozen=True, slots=True)
class FeishuLongConnectionConfig:
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuRuntimeStatus:
    status: str
    connected: bool = False
    last_error: str | None = None


class FeishuWsClient(Protocol):
    on_reconnecting: Callable[[], None]
    on_reconnected: Callable[[], None]

    async def _connect(self) -> None: ...

    async def _disconnect(self) -> None: ...

    async def _ping_loop(self) -> None: ...


ClientFactory = Callable[[FeishuLongConnectionConfig, Any], FeishuWsClient]
DispatchInbound = Callable[[InboundChannelMessage], Awaitable[ChannelIngressResult | None]]
ReplySender = Callable[
    [FeishuLongConnectionConfig, InboundChannelMessage, str],
    Awaitable[str | None],
]
ReplyUpdater = Callable[[FeishuLongConnectionConfig, str, str], Awaitable[None]]
ReactionAdder = Callable[[FeishuLongConnectionConfig, str, str], Awaitable[str | None]]
ReactionRemover = Callable[[FeishuLongConnectionConfig, str, str], Awaitable[None]]
AuthenticatedCallback = Callable[[], None]
ReconnectingCallback = Callable[[], None]
SessionEventStreamFactory = Callable[[str, str], AsyncIterator[Any]]


class CardStream(Protocol):
    """A live Feishu streaming card the runner writes the answer into."""

    async def push(self, content: str, *, final: bool) -> None: ...


CardStreamOpener = Callable[
    [FeishuLongConnectionConfig, InboundChannelMessage],
    Awaitable["CardStream | None"],
]

# Feishu emoji key used as the "picked this up" acknowledgement. Reacting to the
# user's own message keeps the chat free of throwaway placeholder messages.
ACK_REACTION_EMOJI = "OnIt"

# Minimum spacing between streaming updates. Model deltas arrive far faster than
# any chat API accepts; without this the stream is a burst of rate-limit errors.
STREAM_PATCH_MIN_INTERVAL_S = 0.7

# Element the streaming card writes into (see ``_stream_card_json``).
STREAM_CARD_ELEMENT_ID = "answer"


class FeishuLongConnectionRunner:
    def __init__(
        self,
        config: FeishuLongConnectionConfig,
        *,
        dispatch: DispatchInbound,
        client_factory: ClientFactory | None = None,
        reply_sender: ReplySender | None = None,
        reply_updater: ReplyUpdater | None = None,
        reaction_adder: ReactionAdder | None = None,
        reaction_remover: ReactionRemover | None = None,
        card_stream_opener: CardStreamOpener | None = None,
        on_authenticated: AuthenticatedCallback | None = None,
        on_reconnecting: ReconnectingCallback | None = None,
        session_event_stream_factory: SessionEventStreamFactory | None = None,
    ) -> None:
        self._config = config
        self._dispatch = dispatch
        self._client_factory = client_factory or _new_sdk_client
        self._reply_sender = reply_sender or _send_feishu_text_reply
        self._reply_updater = reply_updater or _patch_feishu_text_message
        self._reaction_adder = reaction_adder or _add_feishu_reaction
        self._reaction_remover = reaction_remover or _remove_feishu_reaction
        self._card_stream_opener = card_stream_opener or _open_feishu_card_stream
        self._on_authenticated = on_authenticated
        self._on_reconnecting = on_reconnecting
        self._session_event_stream_factory = (
            session_event_stream_factory or _subscribe_session_events
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    async def run_once(self, stop_event: asyncio.Event) -> None:
        self._loop = asyncio.get_running_loop()
        event_handler = _build_event_handler(
            self._config,
            self._handle_event,
            bot_added=self._handle_bot_added,
        )
        client = self._client_factory(self._config, event_handler)
        client.on_reconnecting = self._handle_reconnecting
        client.on_reconnected = self._handle_reconnected
        ping_task: asyncio.Task[None] | None = None
        try:
            await client._connect()
            self._handle_reconnected()
            ping_task = asyncio.create_task(client._ping_loop(), name="feishu-ping")
            await stop_event.wait()
        finally:
            if ping_task is not None:
                ping_task.cancel()
                await _await_cancelled(ping_task)
            await client._disconnect()
            for task in list(self._dispatch_tasks):
                task.cancel()
                await _await_cancelled(task)
            self._dispatch_tasks.clear()

    def _handle_event(self, event: Any) -> None:
        try:
            inbound = inbound_from_sdk_event(event, self._config)
        except Exception:
            logger.exception(
                "Feishu event parse failed: channel=%s agent=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
            )
            return

        loop = self._loop
        if loop is None:
            logger.warning("Feishu event received before runner loop was ready")
            return
        task = loop.create_task(self._dispatch_event(inbound), name="feishu-dispatch")
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_event(self, inbound: InboundChannelMessage) -> None:
        # Acknowledge by reacting to the user's own message instead of posting a
        # placeholder: the chat keeps only real content, and the reaction is
        # cleared once the answer lands.
        reaction_id = await self._try_add_ack_reaction(inbound)
        try:
            try:
                result = await self._dispatch(inbound)
            except Exception:
                logger.exception(
                    "Feishu inbound dispatch failed: channel=%s agent=%s msg=%s",
                    self._config.channel_instance_id,
                    self._config.agent_slug,
                    inbound.context.external_message_id,
                )
                await self._patch_or_send_channel_reply(
                    inbound,
                    None,
                    CHANNEL_EXECUTION_ERROR_MESSAGE,
                )
                return
            logger.info(
                "Feishu routed message: decision=%s session=%s",
                result.decision.kind.value if result is not None else "none",
                result.session_id if result is not None else None,
            )
            await self._stream_dispatch_result(inbound, result, None)
        finally:
            await self._try_remove_ack_reaction(inbound, reaction_id)

    async def _try_add_ack_reaction(self, inbound: InboundChannelMessage) -> str | None:
        message_id = inbound.context.external_message_id
        if not message_id:
            return None
        try:
            return await self._reaction_adder(self._config, message_id, ACK_REACTION_EMOJI)
        except Exception as exc:  # noqa: BLE001 - acknowledgement is best-effort
            logger.warning("Feishu ack reaction failed: %s", exc)
            return None

    async def _try_remove_ack_reaction(
        self,
        inbound: InboundChannelMessage,
        reaction_id: str | None,
    ) -> None:
        message_id = inbound.context.external_message_id
        if not message_id or not reaction_id:
            return
        try:
            await self._reaction_remover(self._config, message_id, reaction_id)
        except Exception as exc:  # noqa: BLE001 - acknowledgement is best-effort
            logger.warning("Feishu ack reaction removal failed: %s", exc)

    async def _try_send_channel_reply(
        self,
        inbound: InboundChannelMessage,
        content: str,
    ) -> str | None:
        try:
            return await self._reply_sender(self._config, inbound, content)
        except ChannelConfigError as exc:
            logger.warning("Feishu reply was not accepted: %s", exc)
        except Exception as exc:  # noqa: BLE001 - channel replies are best-effort
            logger.warning("Feishu reply failed: %s", exc, exc_info=True)
        return None

    async def _try_patch_channel_reply(self, message_id: str, content: str) -> bool:
        try:
            await self._reply_updater(self._config, message_id, content)
        except ChannelConfigError as exc:
            logger.warning("Feishu reply update was not accepted: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001 - channel replies are best-effort
            logger.warning("Feishu reply update failed: %s", exc, exc_info=True)
            return False
        return True

    async def _patch_or_send_channel_reply(
        self,
        inbound: InboundChannelMessage,
        reply_message_id: str | None,
        content: str,
    ) -> str | None:
        if reply_message_id and await self._try_patch_channel_reply(reply_message_id, content):
            return reply_message_id
        return await self._try_send_channel_reply(inbound, content)

    async def _stream_dispatch_result(
        self,
        inbound: InboundChannelMessage,
        result: ChannelIngressResult | None,
        reply_message_id: str | None,
    ) -> None:
        if result is not None and result.direct_reply:
            # A binding command: configuration, answered directly. No session,
            # no streaming card — just the outcome.
            await self._patch_or_send_channel_reply(
                inbound, reply_message_id, result.direct_reply
            )
            return

        if result is not None and result.decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION:
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                CHANNEL_QUEUED_MESSAGE,
            )
            return

        session_id = result.session_id if result is not None else None
        if not session_id:
            await self._patch_or_send_channel_reply(
                inbound,
                reply_message_id,
                _route_feedback_message(result),
            )
            return

        user_id = inbound.context.user_id
        sink = _StreamingReplySink(self, inbound, reply_message_id)
        logger.info(
            "Feishu streaming session output: channel=%s agent=%s session=%s",
            self._config.channel_instance_id,
            self._config.agent_slug,
            session_id,
        )
        try:
            async for event in self._session_event_stream_factory(user_id, session_id):
                event_type, data = session_event_type_and_data(event)
                logger.debug(
                    "Feishu session event: channel=%s agent=%s session=%s type=%s",
                    self._config.channel_instance_id,
                    self._config.agent_slug,
                    session_id,
                    event_type,
                )
                if await sink.observe(event_type, data):
                    continue
                if event_type == "session_error":
                    logger.warning(
                        "Feishu observed session error: channel=%s agent=%s session=%s",
                        self._config.channel_instance_id,
                        self._config.agent_slug,
                        session_id,
                    )
                    await sink.fail(CHANNEL_EXECUTION_ERROR_MESSAGE)
                    return
                if _is_terminal_session_event(event_type, data):
                    await sink.finish()
                    return
        except Exception:
            logger.exception(
                "Feishu session event stream failed: channel=%s agent=%s session=%s",
                self._config.channel_instance_id,
                self._config.agent_slug,
                session_id,
            )
            await sink.fail(CHANNEL_EXECUTION_ERROR_MESSAGE)
            return

        await sink.finish()

    def _handle_bot_added(self, event: Any) -> None:
        """Flow C: the bot was pulled into a group — offer the project picker.

        Binding right here is the whole point: the person who just added the
        bot is present and knows what the group is for, and never has to switch
        to the desktop app to say so.
        """
        chat_id = _bot_added_chat_id(event)
        if not chat_id:
            return
        loop = self._loop
        if loop is None:
            return
        task = loop.create_task(
            self._offer_project_picker(chat_id), name="feishu-bind-offer"
        )
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _offer_project_picker(self, chat_id: str) -> None:
        try:
            projects = await _list_bindable_projects(self._config.owner_user_id)
            if not projects:
                return
            await _send_feishu_card_to_chat(
                self._config,
                chat_id,
                _project_picker_card(chat_id, projects),
            )
        except Exception as exc:  # noqa: BLE001 - a failed offer must not crash the runner
            logger.warning("Feishu project picker offer failed: %s", exc, exc_info=True)

    def _handle_reconnecting(self) -> None:
        self._on_reconnecting and self._on_reconnecting()

    def _handle_reconnected(self) -> None:
        self._on_authenticated and self._on_authenticated()


class _StreamingReplySink:
    """Accumulates the answer and pushes it out as it grows.

    Preferred transport is a Feishu streaming card (``cardkit``): it is the only
    one built for incremental output — editing a text message is capped at ~20
    edits and marks the message as edited every time. The card is opened lazily
    on the first content so a turn that produces nothing never posts an empty
    bubble, and any failure downgrades to the text path for the rest of the turn.
    """

    def __init__(
        self,
        runner: FeishuLongConnectionRunner,
        inbound: InboundChannelMessage,
        reply_message_id: str | None,
    ) -> None:
        self._runner = runner
        self._inbound = inbound
        self._reply_message_id = reply_message_id
        self._card: CardStream | None = None
        self._card_unavailable = False
        self._answer = AssistantReplyText()
        self._flushed = ""
        self._last_push_at = 0.0

    async def observe(self, event_type: str, data: dict[str, Any]) -> bool:
        """Fold one session event in, pushing it out when the answer moved."""
        if not self._answer.observe(event_type, data):
            return False
        await self._maybe_flush()
        return True

    async def finish(self) -> None:
        content = self._answer.text.strip()
        if not content:
            # Nothing streamed: a card was never opened, so this is the only
            # message the turn produces.
            await self._runner._patch_or_send_channel_reply(
                self._inbound,
                self._reply_message_id,
                CHANNEL_EMPTY_RESULT_MESSAGE,
            )
            return
        await self._push(content, final=True)

    async def fail(self, message: str) -> None:
        if self._card is not None:
            # Close the card on whatever it managed to stream, then report the
            # failure separately — a half-written card must not keep spinning.
            await self._push(self._answer.text.strip() or message, final=True)
            await self._runner._try_send_channel_reply(self._inbound, message)
            return
        await self._runner._patch_or_send_channel_reply(
            self._inbound,
            self._reply_message_id,
            message,
        )

    async def _maybe_flush(self) -> None:
        content = self._answer.text
        if content == self._flushed:
            return
        now = time.monotonic()
        if now - self._last_push_at < STREAM_PATCH_MIN_INTERVAL_S:
            return
        await self._push(content, final=False)

    async def _push(self, content: str, *, final: bool) -> None:
        if not content:
            return
        if await self._ensure_card():
            assert self._card is not None
            try:
                await self._card.push(content, final=final)
            except Exception as exc:  # noqa: BLE001 - degrade, never drop the answer
                logger.warning("Feishu card stream push failed: %s", exc)
                self._card = None
                self._card_unavailable = True
            else:
                self._flushed = content
                self._last_push_at = time.monotonic()
                return
        # Text fallback: only worth an update when the content actually moved.
        message_id = await self._runner._patch_or_send_channel_reply(
            self._inbound,
            self._reply_message_id,
            content,
        )
        self._reply_message_id = message_id
        self._flushed = content
        self._last_push_at = time.monotonic()

    async def _ensure_card(self) -> bool:
        if self._card is not None:
            return True
        if self._card_unavailable:
            return False
        try:
            self._card = await self._runner._card_stream_opener(
                self._runner._config, self._inbound
            )
        except Exception as exc:  # noqa: BLE001 - degrade to the text path
            logger.warning("Feishu card stream open failed: %s", exc)
            self._card = None
        if self._card is None:
            self._card_unavailable = True
            return False
        return True


class FeishuSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_events: dict[str, asyncio.Event] = {}
        self._statuses: dict[str, FeishuRuntimeStatus] = {}
        self._startup_task: asyncio.Task[None] | None = None

    def status_for(self, agent_slug: str) -> FeishuRuntimeStatus:
        return self._statuses.get(agent_slug, FeishuRuntimeStatus(status="stopped"))

    async def startup(self) -> None:
        if self._startup_task is not None and not self._startup_task.done():
            return
        self._startup_task = asyncio.create_task(
            self._startup_connect(),
            name="feishu-startup-connect",
        )

    async def restart(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()
        from valuz_agent.modules.channels.config import agent_channels_active

        if not agent_channels_active():
            return
        configs = await _load_enabled_feishu_configs()
        for config in configs:
            stop_event = asyncio.Event()
            self._stop_events[config.agent_slug] = stop_event
            self._statuses[config.agent_slug] = FeishuRuntimeStatus(status="connecting")
            runner = FeishuLongConnectionRunner(
                config,
                dispatch=_dispatch_to_channel_ingress,
                on_authenticated=self._mark_connected_callback(config.agent_slug),
                on_reconnecting=self._mark_connecting_callback(config.agent_slug),
            )
            self._tasks[config.agent_slug] = asyncio.create_task(
                self._run_loop(config.agent_slug, runner, stop_event),
                name=f"feishu-{config.agent_slug}",
            )

    async def shutdown(self) -> None:
        await self._cancel_startup_task()
        await self._shutdown_connections()

    async def _startup_connect(self) -> None:
        try:
            await asyncio.sleep(0)
            await self.restart()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - channels must never break app startup
            self._statuses["startup"] = FeishuRuntimeStatus(
                status="error",
                connected=False,
                last_error=str(exc),
            )
            logger.warning("Feishu startup connection failed: %s", exc, exc_info=True)
        finally:
            if self._startup_task is asyncio.current_task():
                self._startup_task = None

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

    async def _run_loop(
        self,
        agent_slug: str,
        runner: FeishuLongConnectionRunner,
        stop_event: asyncio.Event,
    ) -> None:
        backoff_s = 1.0
        while not stop_event.is_set():
            try:
                self._statuses[agent_slug] = FeishuRuntimeStatus(status="connecting")
                await runner.run_once(stop_event)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background runner must survive outages
                self._statuses[agent_slug] = FeishuRuntimeStatus(
                    status="error",
                    connected=False,
                    last_error=str(exc),
                )
                logger.warning("Feishu connection failed: %s", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff_s)
                except TimeoutError:
                    backoff_s = min(backoff_s * 2, 30.0)

    def _mark_connected(self, agent_slug: str) -> None:
        self._statuses[agent_slug] = FeishuRuntimeStatus(status="connected", connected=True)

    def _mark_connecting(self, agent_slug: str) -> None:
        self._statuses[agent_slug] = FeishuRuntimeStatus(status="connecting")

    def _mark_connected_callback(self, agent_slug: str) -> AuthenticatedCallback:
        def callback() -> None:
            self._mark_connected(agent_slug)

        return callback

    def _mark_connecting_callback(self, agent_slug: str) -> ReconnectingCallback:
        def callback() -> None:
            self._mark_connecting(agent_slug)

        return callback


def inbound_from_sdk_event(
    event: Any,
    config: FeishuLongConnectionConfig,
) -> InboundChannelMessage:
    from lark_oapi.core.json import JSON  # type: ignore[import-untyped]

    raw_body = JSON.marshal(event).encode("utf-8")
    parsed = FeishuChannelAdapter(
        FeishuChannelConfig(
            channel_instance_id=config.channel_instance_id,
            agent_slug=config.agent_slug,
            verification_token=config.verification_token,
            encrypt_key=None,
        )
    ).parse_callback(raw_body=raw_body, headers={})
    if not isinstance(parsed, InboundChannelMessage):
        raise ValueError("Feishu SDK event did not produce an inbound message")
    return replace(
        parsed,
        context=replace(parsed.context, user_id=config.owner_user_id),
    )


async def _dispatch_to_channel_ingress(
    inbound: InboundChannelMessage,
) -> ChannelIngressResult | None:
    from valuz_agent.api.deps import get_channel_ingress_service

    user_id = inbound.context.user_id
    if not user_id:
        logger.warning("Feishu inbound missing owner user id; message ignored")
        return None
    service_gen = get_channel_ingress_service()
    service = await service_gen.__anext__()
    try:
        return await service.handle_inbound_message(user_id=user_id, inbound=inbound)
    finally:
        try:
            await service_gen.__anext__()
        except StopAsyncIteration:
            pass


async def _load_enabled_feishu_configs() -> list[FeishuLongConnectionConfig]:
    # Owner comes from each binding row, never from ambient process identity:
    # editions that override request identity (e.g. a logged-in commercial
    # user) store bindings under that user, which the device-fingerprint
    # local id would never match — the supervisor would silently load nothing.
    async with async_unit_of_work() as db:
        rows = await AgentChannelBindingDatastore(db).list_enabled(
            platform="feishu",
        )
    configs: list[FeishuLongConnectionConfig] = []
    for row in rows:
        secret = _read_secret(row.owner_user_id, row.secret_ref)
        if not secret.get("app_secret"):
            logger.warning("Feishu binding for %s has no stored app secret", row.agent_slug)
            continue
        configs.append(
            FeishuLongConnectionConfig(
                channel_instance_id=row.channel_instance_id,
                owner_user_id=row.owner_user_id,
                agent_slug=row.agent_slug,
                app_id=row.bot_id,
                app_secret=str(secret["app_secret"]),
                verification_token=_optional_str(secret.get("verification_token")),
                encrypt_key=_optional_str(secret.get("encrypt_key")),
            )
        )
    return configs


def _read_secret(user_id: str, secret_ref: str | None) -> dict[str, Any]:
    if not secret_ref:
        return {}
    raw = secret_store.get(user_id, secret_ref)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"verification_token": raw}
    return data if isinstance(data, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _build_event_handler(
    config: FeishuLongConnectionConfig,
    callback: Callable[[Any], None],
    bot_added: Callable[[Any], None] | None = None,
) -> Any:
    from lark_oapi.event.dispatcher_handler import (  # type: ignore[import-untyped]
        EventDispatcherHandler,
    )

    builder = (
        EventDispatcherHandler.builder(
            config.encrypt_key or "",
            config.verification_token or "",
        )
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_ignore_feishu_event)
        .register_p2_im_message_receive_v1(callback)
    )
    if bot_added is not None:
        builder = builder.register_p2_im_chat_member_bot_added_v1(bot_added)
    return builder.build()


async def _send_feishu_text_reply(
    config: FeishuLongConnectionConfig,
    inbound: InboundChannelMessage,
    content: str,
) -> str | None:
    from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    source_message_id = inbound.context.external_message_id
    if not source_message_id:
        raise ChannelConfigError("Feishu cannot reply without source message id")
    client = _new_openapi_client(config)
    # Plain reply, not ``reply_in_thread``: threading turns every answer into a
    # Feishu topic, which reads as a side-channel instead of an ordinary chat.
    body = (
        ReplyMessageRequestBody.builder()
        .msg_type("text")
        .content(_feishu_text_content(content))
        .build()
    )
    request = (
        ReplyMessageRequest.builder()
        .message_id(source_message_id)
        .request_body(body)
        .build()
    )
    response = await client.im.v1.message.areply(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reply failed: {response.code} {response.msg or ''}".strip()
        )
    return response.data.message_id if response.data is not None else None


def _stream_card_json() -> str:
    """Card schema 2.0 in streaming mode with one markdown element to fill.

    ``streaming_mode`` is what makes Feishu render the typewriter effect and
    accept incremental content updates; a plain card would need a full re-render
    per token.
    """
    return json.dumps(
        {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "streaming_config": {
                    "print_frequency_ms": {"default": 30},
                    "print_step": {"default": 2},
                    "print_strategy": "fast",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "",
                        "element_id": STREAM_CARD_ELEMENT_ID,
                    }
                ]
            },
        },
        ensure_ascii=False,
    )


class _FeishuCardStream:
    """Writes an answer into a Feishu streaming card.

    Sequence numbers order the updates server-side (they may overtake each other
    in flight), so every push takes the next one.
    """

    def __init__(self, config: FeishuLongConnectionConfig, card_id: str) -> None:
        self._config = config
        self._card_id = card_id
        self._sequence = 1

    async def push(self, content: str, *, final: bool) -> None:
        from lark_oapi.api.cardkit.v1 import (  # type: ignore[import-untyped]
            ContentCardElementRequest,
            ContentCardElementRequestBody,
        )

        client = _new_openapi_client(self._config)
        self._sequence += 1
        request = (
            ContentCardElementRequest.builder()
            .card_id(self._card_id)
            .element_id(STREAM_CARD_ELEMENT_ID)
            .request_body(
                ContentCardElementRequestBody.builder()
                .content(content)
                .sequence(self._sequence)
                .build()
            )
            .build()
        )
        response = await client.cardkit.v1.card_element.acontent(request)
        if not response.success():
            raise ChannelConfigError(
                f"Feishu card stream update failed: {response.code} "
                f"{response.msg or ''}".strip()
            )
        if final:
            await self._close(client)

    async def _close(self, client: Any) -> None:
        """Leave streaming mode so the card stops showing the typing cursor."""
        from lark_oapi.api.cardkit.v1 import SettingsCardRequest, SettingsCardRequestBody

        self._sequence += 1
        request = (
            SettingsCardRequest.builder()
            .card_id(self._card_id)
            .request_body(
                SettingsCardRequestBody.builder()
                .settings(json.dumps({"config": {"streaming_mode": False}}))
                .sequence(self._sequence)
                .build()
            )
            .build()
        )
        response = await client.cardkit.v1.card.asettings(request)
        if not response.success():
            logger.warning(
                "Feishu card stream close failed: %s %s",
                response.code,
                response.msg or "",
            )


async def _open_feishu_card_stream(
    config: FeishuLongConnectionConfig,
    inbound: InboundChannelMessage,
) -> CardStream | None:
    """Create a streaming card and post it as the reply to the user's message.

    Returns ``None`` when the card path is unavailable (most often the app is
    missing the cardkit permission) so the caller can fall back to plain text.
    """
    from lark_oapi.api.cardkit.v1 import CreateCardRequest, CreateCardRequestBody
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

    source_message_id = inbound.context.external_message_id
    if not source_message_id:
        return None
    client = _new_openapi_client(config)
    create = (
        CreateCardRequest.builder()
        .request_body(
            CreateCardRequestBody.builder().type("card_json").data(_stream_card_json()).build()
        )
        .build()
    )
    created = await client.cardkit.v1.card.acreate(create)
    if not created.success() or created.data is None or not created.data.card_id:
        logger.info(
            "Feishu streaming card unavailable (%s %s); falling back to text",
            created.code,
            created.msg or "",
        )
        return None
    card_id = created.data.card_id
    reply = (
        ReplyMessageRequest.builder()
        .message_id(source_message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(json.dumps({"type": "card", "data": {"card_id": card_id}}))
            .build()
        )
        .build()
    )
    response = await client.im.v1.message.areply(reply)
    if not response.success():
        logger.info(
            "Feishu streaming card reply rejected (%s %s); falling back to text",
            response.code,
            response.msg or "",
        )
        return None
    return _FeishuCardStream(config, card_id)


def _bot_added_chat_id(event: Any) -> str | None:
    event_body = getattr(event, "event", None)
    return getattr(event_body, "chat_id", None) if event_body is not None else None


async def _list_bindable_projects(user_id: str) -> list[tuple[str, str]]:
    """``(project_id, name)`` for the picker — real projects only.

    Chat projects are per-session and ephemeral; offering one as a group's
    home would bind the group to something that disappears.
    """
    from valuz_agent.modules.projects.service import project_name_map, project_root_paths

    names = await project_name_map(user_id)
    return [
        (project_id, names.get(project_id, project_id))
        for project_id, kind, _root in await project_root_paths(user_id)
        if kind == "project"
    ]


def _project_picker_card(chat_id: str, projects: list[tuple[str, str]]) -> str:
    """Card listing the projects, asking the reader to reply with a command.

    Buttons would be the obvious design, but a card button click arrives as a
    **callback** (``card.action.trigger``), not an event, and the SDK's
    long-connection client drops callback frames outright
    (``MessageType.CARD`` → ``return``). Callbacks need a public HTTPS endpoint,
    which a local-first desktop install does not have. The reply command path
    (``绑定项目 X``) rides the ordinary message event, so it works everywhere the
    bot works.
    """
    # A card is not a browser: past this the Valuz project page is the right
    # surface for picking among many projects.
    shown = projects[:20]
    lines = [CHANNEL_BIND_PROMPT_MESSAGE, ""]
    lines += [f"- **{name}**" for _project_id, name in shown]
    if len(projects) > len(shown):
        lines.append(CHANNEL_BIND_TRUNCATED_MESSAGE)
    lines += ["", CHANNEL_BIND_HOWTO_MESSAGE]
    return json.dumps(
        {
            "schema": "2.0",
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}]},
        },
        ensure_ascii=False,
    )


async def _send_feishu_card_to_chat(
    config: FeishuLongConnectionConfig, chat_id: str, card_json: str
) -> None:
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    client = _new_openapi_client(config)
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(card_json)
            .build()
        )
        .build()
    )
    response = await client.im.v1.message.acreate(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu card send failed: {response.code} {response.msg or ''}".strip()
        )


async def create_feishu_chat(
    *, app_id: str, app_secret: str, name: str
) -> tuple[str, str | None]:
    """Create a group with the bot already in it; returns ``(chat_id, link)``.

    Adding a bot to an existing group needs a client menu that is missing or
    disabled in plenty of setups (not a group, not the owner, disabled by the
    tenant admin). Creating the group from here sidesteps all of that: the app
    is the creator, so the bot is a member by construction.

    The creator is the bot, not the human — so a share link comes back with it,
    which is how the person joins. Asking for their open_id instead would mean
    they had to message the bot first, which is exactly the kind of setup step
    this is meant to remove.
    """
    from lark_oapi.api.im.v1 import (
        CreateChatRequest,
        CreateChatRequestBody,
        LinkChatRequest,
        LinkChatRequestBody,
    )

    config = FeishuLongConnectionConfig(
        channel_instance_id="",
        owner_user_id="",
        agent_slug="",
        app_id=app_id,
        app_secret=app_secret,
    )
    client = _new_openapi_client(config)
    created = await client.im.v1.chat.acreate(
        CreateChatRequest.builder()
        .request_body(CreateChatRequestBody.builder().name(name).build())
        .build()
    )
    if not created.success() or created.data is None or not created.data.chat_id:
        raise ChannelConfigError(
            f"Feishu chat create failed: {created.code} {created.msg or ''}".strip()
        )
    chat_id = created.data.chat_id

    # Best-effort: without the link the group still exists and is bound, and the
    # person can still find it by name — failing the whole call would be worse.
    try:
        link = await client.im.v1.chat.alink(
            LinkChatRequest.builder()
            .chat_id(chat_id)
            .request_body(
                LinkChatRequestBody.builder().validity_period("permanently").build()
            )
            .build()
        )
        share_link = link.data.share_link if link.success() and link.data else None
    except Exception as exc:  # noqa: BLE001 - the group is already created
        logger.warning("Feishu chat link failed for %s: %s", chat_id, exc)
        share_link = None
    return chat_id, share_link


async def feishu_chat_link(*, app_id: str, app_secret: str, chat_id: str) -> str | None:
    """A share link for a group the bot is in.

    Generated on demand rather than stored: the link at creation time is easy
    to miss, and without a way to ask again a Valuz-created group becomes
    unreachable — nobody but the bot is in it yet.
    """
    from lark_oapi.api.im.v1 import LinkChatRequest, LinkChatRequestBody

    config = FeishuLongConnectionConfig(
        channel_instance_id="",
        owner_user_id="",
        agent_slug="",
        app_id=app_id,
        app_secret=app_secret,
    )
    client = _new_openapi_client(config)
    response = await client.im.v1.chat.alink(
        LinkChatRequest.builder()
        .chat_id(chat_id)
        .request_body(
            LinkChatRequestBody.builder().validity_period("permanently").build()
        )
        .build()
    )
    if not response.success():
        raise ChannelConfigError(
            f"Feishu chat link failed: {response.code} {response.msg or ''}".strip()
        )
    return response.data.share_link if response.data is not None else None


async def delete_feishu_chat(*, app_id: str, app_secret: str, chat_id: str) -> None:
    """Dissolve a group. Only valid for groups the app created — it owns those."""
    from lark_oapi.api.im.v1 import DeleteChatRequest

    config = FeishuLongConnectionConfig(
        channel_instance_id="",
        owner_user_id="",
        agent_slug="",
        app_id=app_id,
        app_secret=app_secret,
    )
    client = _new_openapi_client(config)
    response = await client.im.v1.chat.adelete(
        DeleteChatRequest.builder().chat_id(chat_id).build()
    )
    if not response.success():
        raise ChannelConfigError(
            f"Feishu chat delete failed: {response.code} {response.msg or ''}".strip()
        )


@dataclass(frozen=True, slots=True)
class FeishuChat:
    chat_id: str
    name: str
    # The app owns this group — i.e. it created it, and is the only identity
    # Feishu lets dissolve it. Bot-owned groups come back with no ``owner_id``.
    bot_owned: bool
    # A person is in the group. A Valuz-created group nobody joined has only
    # the bot, and is the only case where "join" is a real answer.
    has_people: bool = True


async def list_feishu_chats(*, app_id: str, app_secret: str) -> list[FeishuChat]:
    """Every group the bot is a member of.

    Powers the project page's group picker: the bot must already be in the
    group (that half of the flow only an IM client can do), and Valuz then owns
    which project it stands for.
    """
    from lark_oapi.api.im.v1 import ListChatRequest

    config = FeishuLongConnectionConfig(
        channel_instance_id="",
        owner_user_id="",
        agent_slug="",
        app_id=app_id,
        app_secret=app_secret,
    )
    client = _new_openapi_client(config)
    chats: list[FeishuChat] = []
    page_token: str | None = None
    # Bounded: a bot in more than a few hundred groups is not a picker problem.
    for _ in range(10):
        builder = ListChatRequest.builder().page_size(100)
        if page_token:
            builder = builder.page_token(page_token)
        response = await client.im.v1.chat.alist(builder.build())
        if not response.success():
            raise ChannelConfigError(
                f"Feishu chat list failed: {response.code} {response.msg or ''}".strip()
            )
        data = response.data
        for item in getattr(data, "items", None) or []:
            chat_id = getattr(item, "chat_id", None)
            if chat_id:
                chats.append(
                    FeishuChat(
                        chat_id=chat_id,
                        name=getattr(item, "name", None) or chat_id,
                        bot_owned=not getattr(item, "owner_id", None),
                    )
                )
        page_token = getattr(data, "page_token", None) if data is not None else None
        if not page_token or not getattr(data, "has_more", False):
            break
    return await _with_membership(client, chats)


async def _with_membership(client: Any, chats: list[FeishuChat]) -> list[FeishuChat]:
    """Fill in ``has_people`` for bot-owned groups.

    The list endpoint carries no member count, so this costs one detail call
    per bot-owned group — a handful at picker scale, and only for the groups
    where the answer changes anything (a group someone made themselves always
    has them in it).
    """
    from lark_oapi.api.im.v1 import GetChatRequest

    async def resolve(chat: FeishuChat) -> FeishuChat:
        if not chat.bot_owned:
            return chat
        try:
            response = await client.im.v1.chat.aget(
                GetChatRequest.builder().chat_id(chat.chat_id).build()
            )
        except Exception:  # noqa: BLE001 - a picker row must not fail the list
            return chat
        if not response.success() or response.data is None:
            return chat
        # Feishu returns the count as a string, and ``bool("0")`` is True —
        # which read every empty group as occupied.
        raw = getattr(response.data, "user_count", None)
        try:
            count = int(str(raw).strip() or 0)
        except ValueError:
            return chat
        return replace(chat, has_people=count > 0)

    return list(await asyncio.gather(*(resolve(chat) for chat in chats)))


async def _add_feishu_reaction(
    config: FeishuLongConnectionConfig,
    message_id: str,
    emoji_type: str,
) -> str | None:
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
    )

    client = _new_openapi_client(config)
    request = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        .build()
    )
    response = await client.im.v1.message_reaction.acreate(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reaction failed: {response.code} {response.msg or ''}".strip()
        )
    return response.data.reaction_id if response.data is not None else None


async def _remove_feishu_reaction(
    config: FeishuLongConnectionConfig,
    message_id: str,
    reaction_id: str,
) -> None:
    from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

    client = _new_openapi_client(config)
    request = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    response = await client.im.v1.message_reaction.adelete(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reaction removal failed: {response.code} {response.msg or ''}".strip()
        )


async def _patch_feishu_text_message(
    config: FeishuLongConnectionConfig,
    message_id: str,
    content: str,
) -> None:
    from lark_oapi.api.im.v1 import (
        PatchMessageRequest,
        PatchMessageRequestBody,
    )

    client = _new_openapi_client(config)
    body = PatchMessageRequestBody.builder().content(_feishu_text_content(content)).build()
    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    response = await client.im.v1.message.apatch(request)
    if not response.success():
        raise ChannelConfigError(
            f"Feishu reply update failed: {response.code} {response.msg or ''}".strip()
        )


async def _subscribe_session_events(user_id: str, session_id: str) -> AsyncIterator[Any]:
    from valuz_agent.adapters import kernel_client

    async for event in kernel_client.subscribe_session_events(user_id, session_id):
        yield event


def _new_openapi_client(config: FeishuLongConnectionConfig) -> Any:
    import lark_oapi as lark  # type: ignore[import-untyped]
    from lark_oapi.core.enum import LogLevel  # type: ignore[import-untyped]

    return (
        lark.Client.builder()
        .app_id(config.app_id)
        .app_secret(config.app_secret)
        .log_level(LogLevel.INFO)
        .build()
    )


def _feishu_text_content(content: str) -> str:
    return json.dumps({"text": content}, ensure_ascii=False)


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


def _ignore_feishu_event(event: Any) -> None:
    logger.info("Feishu event ignored: %s", type(event).__name__)


def _new_sdk_client(config: FeishuLongConnectionConfig, event_handler: Any) -> FeishuWsClient:
    import lark_oapi as lark
    from lark_oapi.core.enum import LogLevel
    from lark_oapi.ws import client as ws_client_module  # type: ignore[import-untyped]

    # The SDK stores its event loop in a module global. Keep it aligned with the
    # FastAPI loop so its private async connection API can be managed by us.
    ws_client_module.loop = asyncio.get_running_loop()
    return cast(
        FeishuWsClient,
        lark.ws.Client(
            config.app_id,
            config.app_secret,
            log_level=LogLevel.INFO,
            event_handler=event_handler,
            auto_reconnect=True,
        ),
    )


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


feishu_supervisor = FeishuSupervisor()


__all__ = [
    "FeishuLongConnectionConfig",
    "FeishuLongConnectionRunner",
    "FeishuRuntimeStatus",
    "FeishuSupervisor",
    "feishu_supervisor",
    "inbound_from_sdk_event",
]
