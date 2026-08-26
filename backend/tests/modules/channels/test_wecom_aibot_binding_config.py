from __future__ import annotations

import tempfile
from pathlib import Path

from valuz_agent.modules.channels.config import (
    load_wecom_aibot_config,
    read_wecom_aibot_binding,
    write_wecom_aibot_binding_env,
)


def test_read_wecom_aibot_binding_reports_safe_secret_status() -> None:
    binding = read_wecom_aibot_binding(
        {
            "VALUZ_WECOM_AIBOT_ENABLED": "true",
            "VALUZ_WECOM_AIBOT_CHANNEL_INSTANCE_ID": "wecom-main",
            "VALUZ_WECOM_AIBOT_OWNER_USER_ID": "u1",
            "VALUZ_WECOM_AIBOT_AGENT_SLUG": "developer",
            "VALUZ_WECOM_AIBOT_BOT_ID": "bot-1",
            "VALUZ_WECOM_AIBOT_SECRET": "secret-1",
        }
    )

    assert binding.enabled is True
    assert binding.channel_instance_id == "wecom-main"
    assert binding.owner_user_id == "u1"
    assert binding.agent_slug == "developer"
    assert binding.bot_id == "bot-1"
    assert binding.has_secret is True


def test_write_wecom_aibot_binding_env_preserves_secret_when_omitted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ".env"
        path.write_text(
            "APP_ENV=dev\n"
            "VALUZ_WECOM_AIBOT_SECRET=old-secret\n"
            "VALUZ_WECOM_AIBOT_AGENT_SLUG=old-agent\n",
            encoding="utf-8",
        )

        binding = write_wecom_aibot_binding_env(
            path,
            enabled=True,
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret=None,
        )

        written = path.read_text(encoding="utf-8")
        assert "APP_ENV=dev" in written
        assert "VALUZ_WECOM_AIBOT_SECRET=old-secret" in written
        assert "VALUZ_WECOM_AIBOT_AGENT_SLUG=developer" in written
        assert binding.has_secret is True


def test_write_wecom_aibot_binding_env_replaces_secret_when_supplied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ".env"
        path.write_text("VALUZ_WECOM_AIBOT_SECRET=old-secret\n", encoding="utf-8")

        write_wecom_aibot_binding_env(
            path,
            enabled=True,
            channel_instance_id="wecom-aibot-main",
            owner_user_id="u1",
            agent_slug="developer",
            bot_id="bot-1",
            secret="new-secret",
        )

        written = path.read_text(encoding="utf-8")
        assert "VALUZ_WECOM_AIBOT_SECRET=new-secret" in written
        assert "old-secret" not in written


def test_load_wecom_aibot_config_reads_secret_for_runtime_only() -> None:
    config = load_wecom_aibot_config(
        {
            "VALUZ_WECOM_AIBOT_ENABLED": "true",
            "VALUZ_WECOM_AIBOT_CHANNEL_INSTANCE_ID": "wecom-main",
            "VALUZ_WECOM_AIBOT_OWNER_USER_ID": "u1",
            "VALUZ_WECOM_AIBOT_AGENT_SLUG": "developer",
            "VALUZ_WECOM_AIBOT_BOT_ID": "bot-1",
            "VALUZ_WECOM_AIBOT_SECRET": "secret-1",
            "VALUZ_WECOM_AIBOT_BOT_NAME": "RobotA",
            "VALUZ_WECOM_AIBOT_WS_URL": "wss://example.test/ws",
        }
    )

    assert config.channel_instance_id == "wecom-main"
    assert config.owner_user_id == "u1"
    assert config.agent_slug == "developer"
    assert config.bot_id == "bot-1"
    assert config.secret == "secret-1"
    assert config.bot_name == "RobotA"
    assert config.ws_url == "wss://example.test/ws"
