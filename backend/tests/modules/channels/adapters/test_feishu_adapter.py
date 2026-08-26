from __future__ import annotations

import hashlib
import json

from valuz_agent.modules.channels.adapters import (
    ChannelVerificationError,
    FeishuChannelAdapter,
    FeishuChannelConfig,
    FeishuUrlVerificationResponse,
    InboundChannelMessage,
)


def _signature(*, timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    seed = f"{timestamp}{nonce}{encrypt_key}".encode()
    return hashlib.sha256(seed + body).hexdigest()


def test_feishu_url_verification_returns_challenge_after_token_check() -> None:
    body = json.dumps(
        {
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-code",
        }
    ).encode()
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(
            channel_instance_id="feishu-main",
            agent_slug="developer",
            verification_token="verify-token",
        )
    )

    result = adapter.parse_callback(raw_body=body, headers={})

    assert result == FeishuUrlVerificationResponse(challenge="challenge-code")


def test_feishu_url_verification_rejects_wrong_token() -> None:
    body = json.dumps(
        {
            "type": "url_verification",
            "token": "wrong-token",
            "challenge": "challenge-code",
        }
    ).encode()
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(
            channel_instance_id="feishu-main",
            agent_slug="developer",
            verification_token="verify-token",
        )
    )

    try:
        adapter.parse_callback(raw_body=body, headers={})
    except ChannelVerificationError:
        pass
    else:  # pragma: no cover - direct test runner assertion
        raise AssertionError("expected ChannelVerificationError")


def test_feishu_signature_uses_timestamp_nonce_encrypt_key_and_raw_body() -> None:
    body = json.dumps({"schema": "2.0", "header": {"event_id": "evt-1"}}).encode()
    timestamp = "1774524781"
    nonce = "nonce-1"
    encrypt_key = "encrypt-key"
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(
            channel_instance_id="feishu-main",
            agent_slug="developer",
            encrypt_key=encrypt_key,
        )
    )

    assert adapter.verify_signature(
        raw_body=body,
        headers={
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": nonce,
            "X-Lark-Signature": _signature(
                timestamp=timestamp,
                nonce=nonce,
                encrypt_key=encrypt_key,
                body=body,
            ),
        },
    )


def test_feishu_message_event_normalizes_text_and_thread_context() -> None:
    body = json.dumps(
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
                    "root_id": "om-root",
                    "parent_id": "om-parent",
                    "chat_id": "oc-chat",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": '<at user_id="ou-bot">developer</at> 修一下登录报错'}
                    ),
                    "mentions": [{"id": {"open_id": "ou-bot"}, "name": "developer"}],
                },
            },
        }
    ).encode()
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(channel_instance_id="feishu-main", agent_slug="developer")
    )

    result = adapter.parse_callback(raw_body=body, headers={})

    assert isinstance(result, InboundChannelMessage)
    assert result.text == "修一下登录报错"
    assert result.context.request_id == "evt-1"
    assert result.context.external_message_id == "om-msg"
    assert result.context.external_user_id == "ou-user"
    assert result.context.external_chat_id == "oc-chat"
    assert result.context.external_thread_id == "om-root"
    assert result.context.is_top_level_mention is False
    assert result.context.continuation_hint is True
    assert result.context.mentioned_agent_slug == "developer"


def test_feishu_top_level_message_extracts_continue_hint() -> None:
    body = json.dumps(
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
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": '<at user_id="ou-bot">developer</at> 继续刚才的问题'}
                    ),
                    "mentions": [{"id": {"open_id": "ou-bot"}, "name": "developer"}],
                },
            },
        }
    ).encode()
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(channel_instance_id="feishu-main", agent_slug="developer")
    )

    result = adapter.parse_callback(raw_body=body, headers={})

    assert isinstance(result, InboundChannelMessage)
    assert result.context.is_top_level_mention is True
    assert result.context.continuation_hint is False
    assert result.context.explicit_continue_hint is True
    assert result.context.explicit_new_hint is False


def test_feishu_quoted_top_level_mention_is_a_continuation() -> None:
    body = json.dumps(
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
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": '<at user_id="ou-bot">developer</at> 补一下单测'}
                    ),
                    "mentions": [{"id": {"open_id": "ou-bot"}, "name": "developer"}],
                    "quote_msg_id": "om-old",
                },
            },
        }
    ).encode()
    adapter = FeishuChannelAdapter(
        FeishuChannelConfig(channel_instance_id="feishu-main", agent_slug="developer")
    )

    result = adapter.parse_callback(raw_body=body, headers={})

    assert isinstance(result, InboundChannelMessage)
    assert result.text == "补一下单测"
    assert result.context.is_top_level_mention is False
    assert result.context.continuation_hint is True
