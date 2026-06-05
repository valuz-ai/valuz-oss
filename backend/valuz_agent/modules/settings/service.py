from dataclasses import dataclass, field
from typing import Any

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.settings.datastore import SettingsDatastore


@dataclass
class CapabilitiesSnapshot:
    has_any_llm_channel: bool = False
    reportify_connected: bool = False
    cloud_parse: bool = False
    local_advanced_parse: bool = False
    official_skills: bool = False
    research_tools: bool = False
    needs_reconnect: list[str] = field(default_factory=list)


@dataclass
class AboutInfo:
    app_version: str
    build_number: str
    platform: str
    commit_sha: str
    update_channel: str


@dataclass
class UpdateCheckResult:
    available: bool
    current_version: str
    latest_version: str | None = None
    force_update: bool = False
    release_notes: str | None = None
    download_url: str | None = None


class SettingsService:
    def __init__(self, datastore: SettingsDatastore, event_bus: EventBus) -> None:
        self._ds = datastore
        self._bus = event_bus

    async def get_app_settings(self) -> dict[str, Any]:
        raise NotImplementedError

    async def get_setting(self, key: str) -> Any:
        raise NotImplementedError

    async def patch_app_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def patch_onboarding(self, completed: bool) -> None:
        raise NotImplementedError

    async def derive_capabilities(self) -> CapabilitiesSnapshot:
        raise NotImplementedError

    async def list_shortcuts(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def patch_shortcuts(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def reset_shortcuts(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_about_info(self) -> AboutInfo:
        raise NotImplementedError

    async def check_updates(self) -> UpdateCheckResult:
        raise NotImplementedError
