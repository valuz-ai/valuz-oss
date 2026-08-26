"""Environment-backed channel configuration for the first IM integration."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from valuz_agent.modules.channels.adapters import FeishuChannelConfig, WeComChannelConfig
from valuz_agent.modules.channels.adapters.wecom_aibot import WeComAIBotConfig


class ChannelConfigError(ValueError):
    """Raised when an external channel callback has no usable local config."""


def agent_channels_active() -> bool:
    """Whether this deployment should run IM channel long connections.

    Auto mode (the default) keeps them to single-tenant local installs: a
    shared ``database_url`` marks a multi-user server deployment, where every
    replica would otherwise open every user's bot connection. The explicit
    setting overrides auto in both directions.
    """
    from valuz_agent.infra.config import settings

    if settings.agent_channels_enabled is not None:
        return settings.agent_channels_enabled
    return not settings.database_url


@dataclass(frozen=True, slots=True)
class WeComAIBotBindingConfig:
    enabled: bool
    channel_instance_id: str
    owner_user_id: str
    agent_slug: str
    bot_id: str
    has_secret: bool


def load_feishu_config(
    channel_instance_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> FeishuChannelConfig:
    env = environ or os.environ
    _ensure_instance("VALUZ_FEISHU_CHANNEL_INSTANCE_ID", channel_instance_id, env)
    return FeishuChannelConfig(
        channel_instance_id=channel_instance_id,
        agent_slug=_required(env, "VALUZ_FEISHU_AGENT_SLUG"),
        verification_token=env.get("VALUZ_FEISHU_VERIFICATION_TOKEN") or None,
        encrypt_key=env.get("VALUZ_FEISHU_ENCRYPT_KEY") or None,
    )


def load_wecom_config(
    channel_instance_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> WeComChannelConfig:
    env = environ or os.environ
    _ensure_instance("VALUZ_WECOM_CHANNEL_INSTANCE_ID", channel_instance_id, env)
    return WeComChannelConfig(
        channel_instance_id=channel_instance_id,
        owner_user_id=_required(env, "VALUZ_WECOM_OWNER_USER_ID"),
        agent_slug=_required(env, "VALUZ_WECOM_AGENT_SLUG"),
        token=_required(env, "VALUZ_WECOM_TOKEN"),
        encoding_aes_key=_required(env, "VALUZ_WECOM_ENCODING_AES_KEY"),
        corp_id=env.get("VALUZ_WECOM_CORP_ID") or None,
        bot_name=env.get("VALUZ_WECOM_BOT_NAME") or None,
    )


def load_wecom_aibot_config(
    environ: Mapping[str, str] | None = None,
) -> WeComAIBotConfig:
    env = environ or _merged_local_channel_env()
    if not _truthy(env.get("VALUZ_WECOM_AIBOT_ENABLED")):
        raise ChannelConfigError("WeCom AIBot channel is disabled")
    return WeComAIBotConfig(
        channel_instance_id=env.get("VALUZ_WECOM_AIBOT_CHANNEL_INSTANCE_ID")
        or "wecom-aibot-main",
        owner_user_id=_required(env, "VALUZ_WECOM_AIBOT_OWNER_USER_ID"),
        agent_slug=_required(env, "VALUZ_WECOM_AIBOT_AGENT_SLUG"),
        bot_id=_required(env, "VALUZ_WECOM_AIBOT_BOT_ID"),
        secret=_required(env, "VALUZ_WECOM_AIBOT_SECRET"),
        bot_name=env.get("VALUZ_WECOM_AIBOT_BOT_NAME") or None,
        ws_url=env.get("VALUZ_WECOM_AIBOT_WS_URL") or "wss://openws.work.weixin.qq.com",
    )


def read_wecom_aibot_binding(
    environ: Mapping[str, str] | None = None,
) -> WeComAIBotBindingConfig:
    env = environ or _merged_local_channel_env()
    return WeComAIBotBindingConfig(
        enabled=_truthy(env.get("VALUZ_WECOM_AIBOT_ENABLED")),
        channel_instance_id=env.get("VALUZ_WECOM_AIBOT_CHANNEL_INSTANCE_ID")
        or "wecom-aibot-main",
        owner_user_id=env.get("VALUZ_WECOM_AIBOT_OWNER_USER_ID") or "",
        agent_slug=env.get("VALUZ_WECOM_AIBOT_AGENT_SLUG") or "",
        bot_id=env.get("VALUZ_WECOM_AIBOT_BOT_ID") or "",
        has_secret=bool(env.get("VALUZ_WECOM_AIBOT_SECRET")),
    )


def write_wecom_aibot_binding_env(
    path: Path,
    *,
    enabled: bool,
    channel_instance_id: str,
    owner_user_id: str,
    agent_slug: str,
    bot_id: str,
    secret: str | None,
) -> WeComAIBotBindingConfig:
    current = _read_env_file_values(path)
    secret_value = secret if secret is not None else current.get("VALUZ_WECOM_AIBOT_SECRET")
    updates = {
        "VALUZ_WECOM_AIBOT_ENABLED": "true" if enabled else "false",
        "VALUZ_WECOM_AIBOT_CHANNEL_INSTANCE_ID": channel_instance_id,
        "VALUZ_WECOM_AIBOT_OWNER_USER_ID": owner_user_id,
        "VALUZ_WECOM_AIBOT_AGENT_SLUG": agent_slug,
        "VALUZ_WECOM_AIBOT_BOT_ID": bot_id,
    }
    if secret_value:
        updates["VALUZ_WECOM_AIBOT_SECRET"] = secret_value
    _write_env_values(path, updates)
    os.environ.update(updates)
    if secret_value:
        os.environ["VALUZ_WECOM_AIBOT_SECRET"] = secret_value
    else:
        os.environ.pop("VALUZ_WECOM_AIBOT_SECRET", None)
    return read_wecom_aibot_binding({**current, **updates})


def local_channel_env_path() -> Path:
    if getattr(sys, "frozen", False):
        from valuz_agent.infra.fs_registry import fs_registry
        from valuz_agent.infra.local_identity import resolve_local_user_id

        return fs_registry.data_dir(resolve_local_user_id()) / ".env"
    return Path(__file__).resolve().parents[3] / ".env"


def _ensure_instance(
    key: str,
    requested_channel_instance_id: str,
    env: Mapping[str, str],
) -> None:
    configured = env.get(key)
    if configured and configured != requested_channel_instance_id:
        raise ChannelConfigError(
            f"channel instance '{requested_channel_instance_id}' is not configured"
        )


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise ChannelConfigError(f"missing required channel env var {key}")
    return value


def _merged_local_channel_env() -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(_read_env_file_values(local_channel_env_path()))
    return merged


def _read_env_file_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _write_env_values(path: Path, updates: Mapping[str, str]) -> None:
    keys = set(updates)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in keys:
                continue
        kept.append(line)
    if kept and kept[-1].strip():
        kept.append("")
    kept.append("# WeCom AIBot long connection")
    for key, value in updates.items():
        kept.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "ChannelConfigError",
    "WeComAIBotBindingConfig",
    "load_feishu_config",
    "load_wecom_aibot_config",
    "load_wecom_config",
    "local_channel_env_path",
    "read_wecom_aibot_binding",
    "write_wecom_aibot_binding_env",
]
