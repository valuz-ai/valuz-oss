"""Compose project collaborators at the host adapter boundary, in one UoW."""

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.skills.datastore import SkillDatastore


def build_project_service(db: AsyncSession) -> ProjectService:
    return ProjectService(
        datastore=ProjectDatastore(db),
        event_bus=event_bus,
        session_datastore=SessionDatastore(db),
        document_datastore=DocumentDatastore(db),
        automation_datastore=AutomationDatastore(db),
        skill_datastore=SkillDatastore(db),
        connector_datastore=ConnectorDatastore(db),
        member_datastore=ProjectMemberDatastore(db),
    )
