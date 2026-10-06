"""Plugin-owned automations (ADR-034; docs task card 04 §D, plugin-development/12 §6.3).

A third-party plugin declares code automations in its manifest; on install / update
Valuz creates them FOR THE INSTALLING USER, marked ``extension_id`` /
``extension_name`` on the row, and deletes them on uninstall.

**Design: one managed project per (user, plugin).** A code automation needs a
project and an entry path inside the project directory
(``code_runner.resolve_paths``). Instead of teaching the executor about a second
script location, each plugin gets an ordinary, user-visible managed project
(``Plugin: <name>``, allocated like any ``create_project`` with no root) and its
automation scripts are copied into ``<project cwd>/.valuz/extensions/<id>/``
(``.valuz`` is already the platform-managed, scanner-hidden tree). The code
automation's entry is then the project-relative path of the copied script, so the
existing runner, executor, cancel, artifact and file-delivery paths work
unchanged. The project is found again through the automations that name it
(``extension_id``); if the user deletes the project (which deletes its
automations) the next sync simply creates a fresh one.

An automation is identified within its plugin by the manifest ``name``. The row
carries that identity in ``origin_tool_call_id`` as ``origin_key(id, name)`` — a
hashed provenance marker that can never collide with a kernel tool-use id — so
renaming the row in the UI does not orphan it.

Everything here takes the owner explicitly and a caller-owned session; it never
reads the ambient request context.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.automations.contracts import (
    DEFAULT_CODE_TIMEOUT_S,
    ArtifactResult,
    CodeExecution,
    ConversationResult,
    JsonInput,
    NoneInput,
    normalise_entry,
)
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.automations.models import AutomationRow
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload,
    AutomationUpdatePayload,
    CronTrigger,
    IntervalTrigger,
    ManualTrigger,
    Trigger,
)
from valuz_agent.modules.automations.service import AutomationService

logger = logging.getLogger(__name__)

#: Where a plugin's scripts live inside its managed project.
EXTENSIONS_DIRNAME = ".valuz/extensions"
NAME_SEPARATOR = " · "
MAX_AUTOMATION_NAME = 50


def origin_key(extension_id: str, name: str) -> str:
    """The ``origin_tool_call_id`` that identifies one declared automation."""
    digest = hashlib.sha256(f"{extension_id}\0{name}".encode()).hexdigest()
    return f"ext:{digest[:40]}"


def row_name(display: str, title: str) -> str:
    """``<plugin> · <title>`` clipped to the 50 characters an automation name allows
    (the title is kept whole, the plugin part gives way first)."""
    title = title.strip() or "automation"
    if len(title) >= MAX_AUTOMATION_NAME:
        return title[:MAX_AUTOMATION_NAME]
    room = MAX_AUTOMATION_NAME - len(title) - len(NAME_SEPARATOR)
    head = display.strip()[: max(room, 0)].rstrip()
    return f"{head}{NAME_SEPARATOR}{title}" if head else title


@dataclass(frozen=True)
class ExtensionAutomationSpec:
    """One manifest ``automations[]`` entry, ready to become an automation row."""

    name: str
    title: str
    runtime: Literal["python", "shell"]
    entry: str  # relative to the plugin package (as in the manifest)
    input_schema: dict[str, Any] | None = None
    result: Literal["conversation", "artifact"] = "artifact"
    timeout_s: int | None = None
    trigger: str | dict[str, Any] = "manual"

    def project_entry(self, extension_id: str) -> str:
        return normalise_entry(f"{EXTENSIONS_DIRNAME}/{extension_id}/{self.entry}")

    def automation_trigger(self) -> Trigger:
        if isinstance(self.trigger, dict):
            if "cron" in self.trigger:
                return CronTrigger(
                    cron_expr=str(self.trigger["cron"]), timezone=self.trigger.get("timezone")
                )
            if "intervalSec" in self.trigger:
                return IntervalTrigger(seconds=int(self.trigger["intervalSec"]))
        return ManualTrigger()


@dataclass
class SyncResult:
    #: manifest automation name -> automation id
    ids: dict[str, str] = field(default_factory=dict)
    project_id: str | None = None
    deleted: int = 0


async def build_service(db: AsyncSession, user_id: str) -> AutomationService:
    """An ``AutomationService`` over ``db`` for ``user_id`` (no ambient context)."""
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    locale = await get_default_locale(db, user_id=user_id)
    default_tz = await get_effective_default_timezone(db, user_id=user_id)
    project_svc = ProjectService(datastore=ProjectDatastore(db), event_bus=event_bus)
    connector_svc = ConnectorService(datastore=ConnectorDatastore(db))
    return AutomationService(
        db=db,
        event_bus=event_bus,
        project_service=project_svc,
        agent_service=AgentService(db=db, connector_service=connector_svc),
        locale=locale,
        default_timezone=default_tz,
    )


class ExtensionAutomations:
    """Create / update / delete / look up the automations of one user's plugins."""

    def __init__(self, db: AsyncSession, user_id: str, service: AutomationService | None = None):
        self._db = db
        self._user_id = user_id
        self._ds = AutomationDatastore(db)
        self._service = service

    async def service(self) -> AutomationService:
        if self._service is None:
            self._service = await build_service(self._db, self._user_id)
        return self._service

    # -- lookups -------------------------------------------------------------------

    async def rows(self, extension_id: str) -> list[AutomationRow]:
        return await self._ds.list_by_extension(self._user_id, extension_id)

    async def ids_by_name(self, extension_id: str, names: list[str]) -> dict[str, AutomationRow]:
        keyed = {origin_key(extension_id, n): n for n in names}
        found: dict[str, AutomationRow] = {}
        for row in await self.rows(extension_id):
            name = keyed.get(row.origin_tool_call_id or "")
            if name is not None:
                found[name] = row
        return found

    # -- project + files -----------------------------------------------------------

    async def _project(self, extension_id: str, display: str, rows: list[AutomationRow]) -> str:
        """The managed project of this plugin: the one its automations already use,
        else a fresh one."""
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.modules.projects.datastore import ProjectDatastore
        from valuz_agent.modules.projects.service import ProjectService

        project_svc = ProjectService(datastore=ProjectDatastore(self._db), event_bus=event_bus)
        for row in rows:
            try:
                await project_svc.get_project(self._user_id, row.project_id)
                return row.project_id
            except KeyError:
                continue
        created = await project_svc.create_project(self._user_id, f"Plugin: {display}")
        return created.id

    async def _project_cwd(self, project_id: str) -> Path:
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.modules.projects.datastore import ProjectDatastore
        from valuz_agent.modules.projects.service import ProjectService

        detail = await ProjectService(
            datastore=ProjectDatastore(self._db), event_bus=event_bus
        ).get_project(self._user_id, project_id)
        if not detail.cwd:
            raise RuntimeError(f"project {project_id} has no working directory")
        return Path(detail.cwd)

    @staticmethod
    def install_scripts(
        cwd: Path, extension_id: str, package: Path, specs: list[ExtensionAutomationSpec]
    ) -> Path:
        """Copy the plugin's automation scripts into ``<cwd>/.valuz/extensions/<id>/``.

        The ``automations/`` tree plus any entry that lives elsewhere in the package
        (path preserved). The previous copy is replaced.
        """
        target = cwd / EXTENSIONS_DIRNAME / extension_id
        staging = target.with_name(f".{extension_id}.new")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        real_package = package.resolve()
        sources = [Path("automations")] + [Path(s.entry) for s in specs]
        for rel in sources:
            src = (package / rel).resolve()
            if real_package not in src.parents or not src.exists():
                continue
            dst = staging / rel
            if src.is_dir():
                if not dst.exists():
                    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)
        return target

    @staticmethod
    def remove_scripts(cwd: Path, extension_id: str) -> None:
        shutil.rmtree(cwd / EXTENSIONS_DIRNAME / extension_id, ignore_errors=True)

    # -- sync ----------------------------------------------------------------------

    async def sync(
        self,
        extension_id: str,
        extension_name: str,
        specs: list[ExtensionAutomationSpec],
        package: Path,
        *,
        enabled: bool = True,
    ) -> SyncResult:
        """Make the user's automations of ``extension_id`` match ``specs``.

        Creates the missing ones, updates the rest in place (so run history stays),
        deletes the ones the manifest no longer declares. ``enabled=False`` keeps /
        leaves them paused.
        """
        service = await self.service()
        rows = await self.rows(extension_id)
        result = SyncResult()
        by_key = {r.origin_tool_call_id: r for r in rows if r.origin_tool_call_id}
        wanted = {origin_key(extension_id, s.name) for s in specs}
        for stale in rows:
            if stale.origin_tool_call_id not in wanted:
                await service.delete(stale.id, user_id=self._user_id)
                result.deleted += 1
        if not specs:
            for stale in rows:
                try:
                    cwd = await self._project_cwd(stale.project_id)
                except Exception:  # noqa: BLE001 — the project may be gone already
                    continue
                self.remove_scripts(cwd, extension_id)
            return result
        live = [r for r in rows if r.origin_tool_call_id in wanted]
        project_id = await self._project(extension_id, extension_name, live)
        result.project_id = project_id
        cwd = await self._project_cwd(project_id)
        self.install_scripts(cwd, extension_id, package, specs)
        for spec in specs:
            key = origin_key(extension_id, spec.name)
            row: AutomationRow | None = by_key.get(key)
            if row is not None and row.project_id != project_id:
                await service.delete(row.id, user_id=self._user_id)
                row = None
            execution = CodeExecution(
                runtime=spec.runtime,
                entry=spec.project_entry(extension_id),
                timeout_seconds=spec.timeout_s or DEFAULT_CODE_TIMEOUT_S,
            )
            input_contract: NoneInput | JsonInput = (
                JsonInput(schema=spec.input_schema) if spec.input_schema else NoneInput()
            )
            name = row_name(extension_name, spec.title or spec.name)
            result_contract: ArtifactResult | ConversationResult = (
                ArtifactResult() if spec.result == "artifact" else ConversationResult()
            )
            if row is None:
                payload = AutomationCreatePayload(
                    name=name,
                    project_kind="project",
                    project_id=project_id,
                    trigger=spec.automation_trigger(),
                    execution=execution,
                    input=input_contract,
                    result=result_contract,
                )
                detail = await service.create(
                    payload,
                    origin_tool_call_id=key,
                    user_id=self._user_id,
                    extension_id=extension_id,
                    extension_name=extension_name,
                )
                automation_id = detail.automation_id
            else:
                update = AutomationUpdatePayload(
                    name=name
                    if row.name != name and _is_managed_name(row, extension_name)
                    else None,
                    trigger=spec.automation_trigger(),
                    execution=execution,
                    input=input_contract,
                    result=result_contract,
                )
                await service.update(row.id, update, user_id=self._user_id)
                fresh = await self._ds.get_automation(self._user_id, row.id)
                if fresh is not None and fresh.extension_name != extension_name:
                    fresh.extension_name = extension_name
                    await self._ds.update_automation(fresh)
                automation_id = row.id
            result.ids[spec.name] = automation_id
            if not enabled:
                await service.pause(automation_id, user_id=self._user_id)
            elif row is not None and row.status == "paused":
                await service.resume(automation_id, user_id=self._user_id)
        return result

    async def set_paused(self, extension_id: str, paused: bool) -> None:
        """Pause (plugin disabled) or resume (plugin enabled) every automation of it."""
        service = await self.service()
        for row in await self.rows(extension_id):
            if paused and row.status != "paused":
                await service.pause(row.id, user_id=self._user_id)
            elif not paused and row.status == "paused":
                await service.resume(row.id, user_id=self._user_id)

    async def delete_all(self, extension_id: str) -> int:
        """Delete every automation of the plugin (uninstall); the count."""
        service = await self.service()
        rows = await self.rows(extension_id)
        cwds: set[Path] = set()
        for row in rows:
            try:
                cwds.add(await self._project_cwd(row.project_id))
            except Exception:  # noqa: BLE001
                pass
            await service.delete(row.id, user_id=self._user_id)
        for cwd in cwds:
            self.remove_scripts(cwd, extension_id)
        return len(rows)


def _is_managed_name(row: AutomationRow, extension_name: str) -> bool:
    """Only rename a row the user has not renamed (its name still has our shape)."""
    return NAME_SEPARATOR in row.name or row.name == row_name(extension_name, "")


__all__ = [
    "EXTENSIONS_DIRNAME",
    "ExtensionAutomationSpec",
    "ExtensionAutomations",
    "SyncResult",
    "build_service",
    "origin_key",
    "row_name",
]
