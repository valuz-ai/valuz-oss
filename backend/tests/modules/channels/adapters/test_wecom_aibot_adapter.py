from __future__ import annotations

from valuz_agent.modules.channels.adapters.wecom_aibot import (
    WeComAIBotConfig,
    build_heartbeat_frame,
    build_subscribe_frame,
    parse_wecom_aibot_frame,
)


def test_build_subscribe_frame_uses_bot_credentials() -> None:
    frame = build_subscribe_frame(
        bot_id="bot-1",
        secret="secret-1",
        req_id="aibot_subscribe-test",
    )

    assert frame == {
        "cmd": "aibot_subscribe",
        "headers": {"req_id": "aibot_subscribe-test"},
        "body": {
            "bot_id": "bot-1",
            "secret": "secret-1",
        },
    }


def test_build_heartbeat_frame_uses_ping_command() -> None:
    assert build_heartbeat_frame("ping-test") == {
        "cmd": "ping",
        "headers": {"req_id": "ping-test"},
    }


def test_parse_text_callback_normalizes_channel_message() -> None:
    inbound = parse_wecom_aibot_frame(
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
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is not None
    assert inbound.text == "改一下项目问题"
    assert inbound.context.channel_instance_id == "wecom-aibot-main"
    assert inbound.context.external_chat_id == "chat-1"
    assert inbound.context.mentioned_agent_slug == "developer"
    assert inbound.context.request_id == "req-1"
    assert inbound.context.external_message_id == "msg-1"
    assert inbound.context.external_user_id == "user-1"
    assert inbound.context.continuation_hint is False
    assert inbound.context.explicit_continue_hint is False
    assert inbound.context.explicit_new_hint is False
    assert inbound.channel_context == {
        "platform": "wecom",
        "transport": "aibot_long_connection",
        "chattype": "group",
        "aibotid": "bot-1",
        "message_type": "text",
    }


def test_parse_text_callback_extracts_session_intent_hints() -> None:
    inbound = parse_wecom_aibot_frame(
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
                "text": {"content": "@RobotA 继续刚才的问题"},
            },
        },
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is not None
    assert inbound.context.is_top_level_mention is True
    assert inbound.context.continuation_hint is False
    assert inbound.context.explicit_continue_hint is True
    assert inbound.context.explicit_new_hint is False


def test_parse_text_callback_treats_quoted_message_as_continuation_without_at() -> None:
    inbound = parse_wecom_aibot_frame(
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
                "text": {"content": "补一下单测"},
                "quote_msg": {"msgid": "stream-old-user-message"},
            },
        },
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is not None
    assert inbound.text == "补一下单测"
    assert inbound.context.is_top_level_mention is False
    assert inbound.context.continuation_hint is True
    assert inbound.context.explicit_continue_hint is False
    assert inbound.context.explicit_new_hint is False


def test_parse_text_callback_treats_quoted_at_message_as_continuation() -> None:
    inbound = parse_wecom_aibot_frame(
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
                "text": {"content": "@RobotA 补一下单测"},
                "quote_msg": {"msgid": "stream-old-user-message"},
            },
        },
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is not None
    assert inbound.text == "补一下单测"
    assert inbound.context.is_top_level_mention is False
    assert inbound.context.continuation_hint is True
    assert inbound.context.explicit_continue_hint is False
    assert inbound.context.explicit_new_hint is False


def test_parse_text_callback_extracts_explicit_new_session_hint() -> None:
    inbound = parse_wecom_aibot_frame(
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
                "text": {"content": "@RobotA 新开一个任务改登录"},
            },
        },
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is not None
    assert inbound.context.explicit_continue_hint is False
    assert inbound.context.explicit_new_hint is True


def test_parse_callback_ignores_non_text_messages() -> None:
    inbound = parse_wecom_aibot_frame(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req-1"},
            "body": {
                "msgid": "msg-1",
                "aibotid": "bot-1",
                "chattype": "single",
                "from": {"userid": "user-1"},
                "msgtype": "image",
                "image": {"url": "https://example.test/image"},
            },
        },
        WeComAIBotConfig(
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="secret-1",
        ),
    )

    assert inbound is None
