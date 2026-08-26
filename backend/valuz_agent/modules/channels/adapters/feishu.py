"""Feishu callback adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from valuz_agent.modules.channels.adapters.base import (
    ChannelVerificationError,
    InboundChannelMessage,
)
from valuz_agent.modules.channels.adapters.intents import (
    detect_session_intent_hints,
    has_message_reference,
)
from valuz_agent.modules.channels.schemas import ChannelMentionContext

_AT_TAG_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE)
_PROJECT_HINT_RE = re.compile(r"(?:项目|project)\s*[:：]\s*(?P<name>[^\s，,。]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FeishuChannelConfig:
    channel_instance_id: str
    agent_slug: str
    verification_token: str | None = None
    encrypt_key: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuUrlVerificationResponse:
    challenge: str


class FeishuChannelAdapter:
    def __init__(self, config: FeishuChannelConfig) -> None:
        self.config = config

    def verify_signature(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        if not self.config.encrypt_key:
            return True
        timestamp = _header(headers, "X-Lark-Request-Timestamp")
        nonce = _header(headers, "X-Lark-Request-Nonce")
        signature = _header(headers, "X-Lark-Signature")
        if not timestamp or not nonce or not signature:
            return False
        seed = f"{timestamp}{nonce}{self.config.encrypt_key}".encode()
        expected = hashlib.sha256(seed + raw_body).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_callback(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> FeishuUrlVerificationResponse | InboundChannelMessage | None:
        if self.config.encrypt_key and not self.verify_signature(
            raw_body=raw_body,
            headers=headers,
        ):
            raise ChannelVerificationError("invalid Feishu callback signature")

        payload = json.loads(raw_body.decode("utf-8"))
        if payload.get("type") == "url_verification":
            token = str(payload.get("token") or "")
            if self.config.verification_token and token != self.config.verification_token:
                raise ChannelVerificationError("invalid Feishu verification token")
            return FeishuUrlVerificationResponse(challenge=str(payload.get("challenge") or ""))

        header = _as_dict(payload.get("header"))
        event = _as_dict(payload.get("event"))
        event_type = str(header.get("event_type") or payload.get("type") or "")
        if event_type != "im.message.receive_v1":
            return None

        sender = _as_dict(event.get("sender"))
        sender_id = _as_dict(sender.get("sender_id"))
        message = _as_dict(event.get("message"))
        message_id = str(message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        chat_type = str(message.get("chat_type") or "")
        root_id = str(message.get("root_id") or "")
        parent_id = str(message.get("parent_id") or "")
        # Only a real Feishu topic counts as a thread. Falling back to the
        # message id made every plain chat message its own "thread", so the
        # route key changed on each turn and no session was ever continued.
        thread_id = root_id or parent_id
        text = _normalize_feishu_text(message.get("content"))
        project_name = _extract_project_hint(text)
        explicit_continue_hint, explicit_new_hint = detect_session_intent_hints(text)
        has_reference = has_message_reference(message, text)
        is_top_level = not bool(root_id or parent_id) and not has_reference

        context = ChannelMentionContext(
            user_id="",
            channel_instance_id=self.config.channel_instance_id,
            external_chat_id=chat_id,
            external_thread_id=thread_id or None,
            mentioned_agent_slug=self.config.agent_slug,
            explicit_project_name=project_name,
            is_top_level_mention=is_top_level,
            is_direct_chat=chat_type == "p2p",
            continuation_hint=(not is_top_level) or has_reference,
            explicit_continue_hint=explicit_continue_hint,
            explicit_new_hint=explicit_new_hint,
            request_id=str(header.get("event_id") or message_id or ""),
            external_message_id=message_id or None,
            external_user_id=_first_str(sender_id, "open_id", "user_id", "union_id"),
        )
        return InboundChannelMessage(
            text=text,
            context=context,
            params={"query": text, "content": text},
            channel_context={
                "platform": "feishu",
                "event_type": event_type,
                "message_type": message.get("message_type"),
                "mentions": message.get("mentions") or [],
            },
        )


def _header(headers: dict[str, str], name: str) -> str | None:
    lower_name = name.lower()
    for key, value in headers.items():
        if key.lower() == lower_name:
            return value
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _normalize_feishu_text(raw_content: Any) -> str:
    content: dict[str, Any] = {}
    if isinstance(raw_content, str):
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            text = raw_content
        else:
            content = parsed if isinstance(parsed, dict) else {}
            text = str(content.get("text") or content.get("content") or "")
    elif isinstance(raw_content, dict):
        text = str(raw_content.get("text") or raw_content.get("content") or "")
    else:
        text = ""
    return " ".join(_AT_TAG_RE.sub("", text).strip().split())


def _extract_project_hint(text: str) -> str | None:
    match = _PROJECT_HINT_RE.search(text)
    if match is None:
        return None
    project_name = match.group("name").strip()
    return project_name or None


__all__ = [
    "FeishuChannelAdapter",
    "FeishuChannelConfig",
    "FeishuUrlVerificationResponse",
]
