from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.ports.extensions import ext
from valuz_agent.ports.runtime_resource import (
    LocalManagedAgentMutationPort,
    LocalRuntimeResourceApplyPort,
    RuntimeResourceContractError,
    ensure_managed_root_containment,
    require_sync_apply_origin,
    validate_skill_reference,
)
from valuz_agent.ports.sandbox_maintenance import UnsupportedSandboxMaintenancePort
from valuz_agent.ports.skill_runtime import CatalogOnlyUntilClaimed


def test_oss_extensions_have_local_defaults() -> None:
    assert isinstance(ext.managed_agent_mutation, LocalManagedAgentMutationPort)
    assert isinstance(ext.runtime_resource_apply, LocalRuntimeResourceApplyPort)
    assert isinstance(ext.sandbox_maintenance, UnsupportedSandboxMaintenancePort)


@pytest.mark.asyncio
async def test_sync_apply_is_the_only_projection_origin() -> None:
    port = LocalRuntimeResourceApplyPort()
    result = await port.apply("user-1", "agent", {"id": "a-1"}, origin="sync_apply")
    assert result.resource_id == "a-1"
    with pytest.raises(RuntimeResourceContractError):
        await port.apply("user-1", "agent", {"id": "a-1"}, origin="local")  # type: ignore[arg-type]


def test_skill_refs_and_realpath_containment_are_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    skill = root / "safe"
    skill.mkdir()
    assert validate_skill_reference("safe") == "safe"
    assert ensure_managed_root_containment(root, skill) == skill
    with pytest.raises(RuntimeResourceContractError):
        validate_skill_reference("../outside")
    with pytest.raises(RuntimeResourceContractError):
        validate_skill_reference(str(skill))
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = root / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeResourceContractError):
        ensure_managed_root_containment(root, escape)


def test_catalog_only_policy_does_not_grant_execution() -> None:
    decision = CatalogOnlyUntilClaimed().decide(
        user_id="user-1", source_path="/tmp/skill", slug="skill"
    )
    assert decision.execution_eligible is False
    assert decision.owner_user_id is None


@pytest.mark.asyncio
async def test_unsupported_sandbox_maintenance_does_not_fake_success() -> None:
    port = UnsupportedSandboxMaintenancePort()
    probe = await port.probe("sandbox-1")
    assert probe.supported is False
    with pytest.raises(Exception, match="freeze"):
        await port.freeze("sandbox-1", fencing_token="f-1")


def test_origin_guard_rejects_local_mutation() -> None:
    require_sync_apply_origin("sync_apply")
    with pytest.raises(RuntimeResourceContractError):
        require_sync_apply_origin("local")  # type: ignore[arg-type]
