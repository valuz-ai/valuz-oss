"""WeCom AIBot long-connection protocol adapter."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from valuz_agent.modules.channels.adapters.base import InboundChannelMessage
from valuz_agent.modules.channels.adapters.intents import (
    detect_session_intent_hints,
    has_message_reference,
)
from valuz_agent.modules.channels.schemas import ChannelMentionContext

WECOM_AIBOT_WS_URL = "wss://openws.work.weixin.qq.com"
WECOM_AIBOT_SUBSCRIBE_CMD = "aibot_subscribe"
WECOM_AIBOT_HEARTBEAT_CMD = "ping"
WECOM_AIBOT_MSG_CALLBACK_CMD = "aibot_msg_callback"
WECOM_AIBOT_EVENT_CALLBACK_CMD = "aibot_event_callback"
WECOM_AIBOT_RESPOND_MSG_CMD = "aibot_respond_msg"

Frame = dict[str, Any]


@dataclass(frozen=True, slots=True)
class WeComAIBotConfig:
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    bot_id: str
    secret: str
    bot_name: str | None = None
    ws_url: str = WECOM_AIBOT_WS_URL


def build_subscribe_frame(*, bot_id: str, secret: str, req_id: str | None = None) -> Frame:
    return {
        "cmd": WECOM_AIBOT_SUBSCRIBE_CMD,
        "headers": {"req_id": req_id or generate_req_id(WECOM_AIBOT_SUBSCRIBE_CMD)},
        "body": {
            "bot_id": bot_id,
            "secret": secret,
        },
    }


def build_heartbeat_frame(req_id: str | None = None) -> Frame:
    return {
        "cmd": WECOM_AIBOT_HEARTBEAT_CMD,
        "headers": {"req_id": req_id or generate_req_id(WECOM_AIBOT_HEARTBEAT_CMD)},
    }


def build_stream_reply_frame(
    *,
    req_id: str,
    stream_id: str,
    content: str,
    finish: bool = True,
) -> Frame:
    return {
        "cmd": WECOM_AIBOT_RESPOND_MSG_CMD,
        "headers": {"req_id": req_id},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": finish,
                "content": content,
            },
        },
    }


def generate_req_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def parse_wecom_aibot_frame(
    frame: Frame,
    config: WeComAIBotConfig,
) -> InboundChannelMessage | None:
    if frame.get("cmd") != WECOM_AIBOT_MSG_CALLBACK_CMD:
        return None
    body = _mapping(frame.get("body"))
    if body.get("msgtype") != "text":
        return None

    text_body = _mapping(body.get("text"))
    text = _normalize_aibot_text(str(text_body.get("content") or ""), config.bot_name)
    if not text:
        return None

    from_body = _mapping(body.get("from"))
    headers = _mapping(frame.get("headers"))
    msg_id = str(body.get("msgid") or headers.get("req_id") or generate_req_id("wecom-aibot-msg"))
    req_id = str(headers.get("req_id") or msg_id)
    chattype = str(body.get("chattype") or "")
    external_chat_id = str(body.get("chatid") or from_body.get("userid") or msg_id)
    explicit_continue_hint, explicit_new_hint = detect_session_intent_hints(text)
    has_reference = has_message_reference(body, text_body, text)

    context = ChannelMentionContext(
        user_id=config.owner_user_id,
        channel_instance_id=config.channel_instance_id,
        external_chat_id=external_chat_id,
        external_thread_id=None,
        mentioned_agent_slug=config.agent_slug,
        is_top_level_mention=not has_reference,
        continuation_hint=has_reference,
        explicit_continue_hint=explicit_continue_hint,
        explicit_new_hint=explicit_new_hint,
        request_id=req_id,
        external_message_id=msg_id,
        external_user_id=str(from_body.get("userid") or "") or None,
    )
    return InboundChannelMessage(
        text=text,
        context=context,
        params={"query": text, "content": text},
        channel_context={
            "platform": "wecom",
            "transport": "aibot_long_connection",
            "chattype": chattype,
            "aibotid": str(body.get("aibotid") or config.bot_id),
            "message_type": "text",
        },
    )


def is_success_ack(frame: Frame, req_id: str) -> bool:
    headers = _mapping(frame.get("headers"))
    return headers.get("req_id") == req_id and int(frame.get("errcode", -1)) == 0


def frame_req_id(frame: Frame) -> str:
    return str(_mapping(frame.get("headers")).get("req_id") or "")


def _normalize_aibot_text(text: str, bot_name: str | None) -> str:
    normalized = " ".join(text.strip().split())
    if bot_name:
        for prefix in (f"@{bot_name}", bot_name):
            if normalized.startswith(prefix):
                return normalized[len(prefix) :].strip()
    return re.sub(r"^@\S+\s*", "", normalized).strip()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "Frame",
    "WECOM_AIBOT_EVENT_CALLBACK_CMD",
    "WECOM_AIBOT_HEARTBEAT_CMD",
    "WECOM_AIBOT_MSG_CALLBACK_CMD",
    "WECOM_AIBOT_RESPOND_MSG_CMD",
    "WECOM_AIBOT_SUBSCRIBE_CMD",
    "WECOM_AIBOT_WS_URL",
    "WeComAIBotConfig",
    "build_heartbeat_frame",
    "build_stream_reply_frame",
    "build_subscribe_frame",
    "frame_req_id",
    "generate_req_id",
    "is_success_ack",
    "parse_wecom_aibot_frame",
]
