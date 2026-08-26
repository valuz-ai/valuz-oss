"""WeCom callback adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol

from valuz_agent.modules.channels.adapters.base import (
    ChannelVerificationError,
    InboundChannelMessage,
)
from valuz_agent.modules.channels.adapters.intents import (
    detect_session_intent_hints,
    has_message_reference,
)
from valuz_agent.modules.channels.schemas import ChannelMentionContext


@dataclass(frozen=True, slots=True)
class WeComChannelConfig:
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    token: str
    encoding_aes_key: str
    corp_id: str | None = None
    bot_name: str | None = None


class WeComDecryptor(Protocol):
    def decrypt(self, encrypted: str) -> str: ...


class WeComCallbackCrypto:
    """Decrypt WeCom callback payloads with the configured EncodingAESKey."""

    def __init__(self, *, encoding_aes_key: str, corp_id: str | None = None) -> None:
        self._encoding_aes_key = encoding_aes_key
        self._corp_id = corp_id

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphers = importlib.import_module("cryptography.hazmat.primitives.ciphers")
            algorithms = importlib.import_module(
                "cryptography.hazmat.primitives.ciphers.algorithms"
            )
            modes = importlib.import_module("cryptography.hazmat.primitives.ciphers.modes")
        except ImportError as exc:  # pragma: no cover - depends on optional runtime install
            raise ChannelVerificationError(
                "cryptography is required to decrypt WeCom callbacks"
            ) from exc

        aes_key = self._decode_aes_key()
        cipher = ciphers.Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        decryptor = cipher.decryptor()
        raw = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
        plain = _pkcs7_unpad(raw)
        if len(plain) < 20:
            raise ChannelVerificationError("invalid WeCom decrypted payload")

        msg_len = struct.unpack("!I", plain[16:20])[0]
        msg = plain[20 : 20 + msg_len]
        corp_id = plain[20 + msg_len :].decode("utf-8")
        if self._corp_id and corp_id != self._corp_id:
            raise ChannelVerificationError("WeCom corp_id mismatch")
        return msg.decode("utf-8")

    def _decode_aes_key(self) -> bytes:
        if len(self._encoding_aes_key) != 43:
            raise ChannelVerificationError("invalid WeCom EncodingAESKey length")
        return base64.b64decode(f"{self._encoding_aes_key}=")


class WeComChannelAdapter:
    def __init__(
        self,
        config: WeComChannelConfig,
        *,
        decryptor: WeComDecryptor | None = None,
    ) -> None:
        self.config = config
        self._decryptor = decryptor or WeComCallbackCrypto(
            encoding_aes_key=config.encoding_aes_key,
            corp_id=config.corp_id,
        )

    def verify_url(self, *, query: dict[str, str]) -> str:
        msg_signature = query.get("msg_signature") or ""
        timestamp = query.get("timestamp") or ""
        nonce = query.get("nonce") or ""
        echostr = query.get("echostr") or ""
        self._verify_signature(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echostr,
        )
        return self._decryptor.decrypt(echostr)

    def parse_callback(
        self,
        *,
        raw_body: bytes,
        query: dict[str, str],
    ) -> InboundChannelMessage | None:
        encrypted = _extract_xml_text(raw_body, "Encrypt")
        if not encrypted:
            return None
        self._verify_signature(
            msg_signature=query.get("msg_signature") or "",
            timestamp=query.get("timestamp") or "",
            nonce=query.get("nonce") or "",
            encrypted=encrypted,
        )
        decrypted = self._decryptor.decrypt(encrypted)
        message = _xml_to_dict(decrypted.encode())
        msg_type = message.get("MsgType")
        if msg_type != "text":
            return None

        msg_id = message.get("MsgId") or f"wecom_{message.get('CreateTime', '')}"
        chat_id = message.get("ChatId") or message.get("FromUserName") or ""
        text, was_bot_mention = _normalize_wecom_text(
            message.get("Content") or "",
            self.config.bot_name,
        )
        explicit_continue_hint, explicit_new_hint = detect_session_intent_hints(text)
        has_reference = has_message_reference(message, text)
        context = ChannelMentionContext(
            user_id="",
            channel_instance_id=self.config.channel_instance_id,
            external_chat_id=chat_id,
            external_thread_id=None,
            mentioned_agent_slug=self.config.agent_slug,
            is_top_level_mention=was_bot_mention and not has_reference,
            continuation_hint=(not was_bot_mention) or has_reference,
            explicit_continue_hint=explicit_continue_hint,
            explicit_new_hint=explicit_new_hint,
            request_id=msg_id,
            external_message_id=msg_id,
            external_user_id=message.get("FromUserName"),
        )
        return InboundChannelMessage(
            text=text,
            context=context,
            params={"query": text, "content": text},
            channel_context={
                "platform": "wecom",
                "agent_id": message.get("AgentID"),
                "message_type": msg_type,
            },
        )

    def _verify_signature(
        self,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> None:
        if not msg_signature or not timestamp or not nonce or not encrypted:
            raise ChannelVerificationError("missing WeCom callback signature fields")
        expected = wecom_signature(self.config.token, timestamp, nonce, encrypted)
        if not hmac.compare_digest(expected, msg_signature):
            raise ChannelVerificationError("invalid WeCom callback signature")


def wecom_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypted])).encode()).hexdigest()


def _extract_xml_text(raw_xml: bytes, tag: str) -> str | None:
    return _xml_to_dict(raw_xml).get(tag)


def _xml_to_dict(raw_xml: bytes) -> dict[str, str]:
    root = ET.fromstring(raw_xml)
    out: dict[str, str] = {}
    for child in root:
        out[child.tag] = child.text or ""
    return out


def _normalize_wecom_text(text: str, bot_name: str | None) -> tuple[str, bool]:
    normalized = " ".join(text.strip().split())
    was_bot_mention = False
    if bot_name:
        for prefix in (f"@{bot_name}", bot_name):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
                was_bot_mention = True
                break
    return normalized, was_bot_mention


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ChannelVerificationError("empty WeCom decrypted payload")
    pad = data[-1]
    if pad < 1 or pad > 32:
        raise ChannelVerificationError("invalid WeCom PKCS7 padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise ChannelVerificationError("invalid WeCom PKCS7 padding")
    return data[:-pad]


__all__ = [
    "WeComCallbackCrypto",
    "WeComChannelAdapter",
    "WeComChannelConfig",
    "wecom_signature",
]
