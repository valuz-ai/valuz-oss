from __future__ import annotations

import hashlib

from valuz_agent.modules.channels.adapters import (
    ChannelVerificationError,
    InboundChannelMessage,
    WeComChannelAdapter,
    WeComChannelConfig,
)


class _FakeDecryptor:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def decrypt(self, encrypted: str) -> str:
        return self.values[encrypted]


def _signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypted])).encode()).hexdigest()


def test_wecom_url_verification_checks_signature_and_decrypts_echo() -> None:
    token = "token-1"
    timestamp = "1774524781"
    nonce = "nonce-1"
    echostr = "encrypted-echo"
    adapter = WeComChannelAdapter(
        WeComChannelConfig(
            channel_instance_id="wecom-main",
            owner_user_id="u1",
            agent_slug="developer",
            token=token,
            encoding_aes_key="a" * 43,
            bot_name="开发者",
        ),
        decryptor=_FakeDecryptor({"encrypted-echo": "ok"}),
    )

    result = adapter.verify_url(
        query={
            "msg_signature": _signature(token, timestamp, nonce, echostr),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echostr,
        }
    )

    assert result == "ok"


def test_wecom_url_verification_rejects_wrong_signature() -> None:
    adapter = WeComChannelAdapter(
        WeComChannelConfig(
            channel_instance_id="wecom-main",
            owner_user_id="u1",
            agent_slug="developer",
            token="token-1",
            encoding_aes_key="a" * 43,
        ),
        decryptor=_FakeDecryptor({"encrypted-echo": "ok"}),
    )

    try:
        adapter.verify_url(
            query={
                "msg_signature": "bad",
                "timestamp": "1774524781",
                "nonce": "nonce-1",
                "echostr": "encrypted-echo",
            }
        )
    except ChannelVerificationError:
        pass
    else:  # pragma: no cover - direct test runner assertion
        raise AssertionError("expected ChannelVerificationError")


def test_wecom_message_callback_decrypts_xml_and_normalizes_message() -> None:
    token = "token-1"
    timestamp = "1774524781"
    nonce = "nonce-1"
    encrypted = "encrypted-payload"
    raw_xml = f"""
    <xml>
      <ToUserName><![CDATA[corp-id]]></ToUserName>
      <Encrypt><![CDATA[{encrypted}]]></Encrypt>
    </xml>
    """.encode()
    decrypted = """
    <xml>
      <ToUserName><![CDATA[corp-id]]></ToUserName>
      <FromUserName><![CDATA[UserA]]></FromUserName>
      <CreateTime>1774524781</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[@开发者 修一下登录报错]]></Content>
      <MsgId>msg-1</MsgId>
      <AgentID>1000002</AgentID>
      <ChatId><![CDATA[group-chat]]></ChatId>
    </xml>
    """
    adapter = WeComChannelAdapter(
        WeComChannelConfig(
            channel_instance_id="wecom-main",
            owner_user_id="u1",
            agent_slug="developer",
            token=token,
            encoding_aes_key="a" * 43,
            bot_name="开发者",
        ),
        decryptor=_FakeDecryptor({encrypted: decrypted}),
    )

    result = adapter.parse_callback(
        raw_body=raw_xml,
        query={
            "msg_signature": _signature(token, timestamp, nonce, encrypted),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )

    assert isinstance(result, InboundChannelMessage)
    assert result.text == "修一下登录报错"
    assert result.context.request_id == "msg-1"
    assert result.context.external_message_id == "msg-1"
    assert result.context.external_user_id == "UserA"
    assert result.context.external_chat_id == "group-chat"
    assert result.context.external_thread_id is None
    assert result.context.is_top_level_mention is True
    assert result.context.continuation_hint is False


def test_wecom_message_without_bot_prefix_is_a_chat_continuation() -> None:
    token = "token-1"
    timestamp = "1774524781"
    nonce = "nonce-1"
    encrypted = "encrypted-payload"
    raw_xml = f"""
    <xml>
      <ToUserName><![CDATA[corp-id]]></ToUserName>
      <Encrypt><![CDATA[{encrypted}]]></Encrypt>
    </xml>
    """.encode()
    decrypted = """
    <xml>
      <FromUserName><![CDATA[UserA]]></FromUserName>
      <CreateTime>1774524781</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[继续修测试]]></Content>
      <MsgId>msg-2</MsgId>
      <AgentID>1000002</AgentID>
      <ChatId><![CDATA[group-chat]]></ChatId>
    </xml>
    """
    adapter = WeComChannelAdapter(
        WeComChannelConfig(
            channel_instance_id="wecom-main",
            owner_user_id="u1",
            agent_slug="developer",
            token=token,
            encoding_aes_key="a" * 43,
            bot_name="开发者",
        ),
        decryptor=_FakeDecryptor({encrypted: decrypted}),
    )

    result = adapter.parse_callback(
        raw_body=raw_xml,
        query={
            "msg_signature": _signature(token, timestamp, nonce, encrypted),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )

    assert isinstance(result, InboundChannelMessage)
    assert result.text == "继续修测试"
    assert result.context.external_thread_id is None
    assert result.context.is_top_level_mention is False
    assert result.context.continuation_hint is True


def test_wecom_message_with_quote_field_is_a_continuation_even_when_mentioned() -> None:
    token = "token-1"
    timestamp = "1774524781"
    nonce = "nonce-1"
    encrypted = "encrypted-payload"
    raw_xml = f"""
    <xml>
      <ToUserName><![CDATA[corp-id]]></ToUserName>
      <Encrypt><![CDATA[{encrypted}]]></Encrypt>
    </xml>
    """.encode()
    decrypted = """
    <xml>
      <FromUserName><![CDATA[UserA]]></FromUserName>
      <CreateTime>1774524781</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[@开发者 补一下单测]]></Content>
      <MsgId>msg-3</MsgId>
      <QuoteMsgId>msg-old</QuoteMsgId>
      <AgentID>1000002</AgentID>
      <ChatId><![CDATA[group-chat]]></ChatId>
    </xml>
    """
    adapter = WeComChannelAdapter(
        WeComChannelConfig(
            channel_instance_id="wecom-main",
            owner_user_id="u1",
            agent_slug="developer",
            token=token,
            encoding_aes_key="a" * 43,
            bot_name="开发者",
        ),
        decryptor=_FakeDecryptor({encrypted: decrypted}),
    )

    result = adapter.parse_callback(
        raw_body=raw_xml,
        query={
            "msg_signature": _signature(token, timestamp, nonce, encrypted),
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )

    assert isinstance(result, InboundChannelMessage)
    assert result.text == "补一下单测"
    assert result.context.is_top_level_mention is False
    assert result.context.continuation_hint is True
