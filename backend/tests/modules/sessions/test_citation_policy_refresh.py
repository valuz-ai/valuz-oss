from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from valuz_agent.adapters.system_prompt_builder import (
    CITATION_POLICY_REVISION,
    ensure_citation_system_policy,
)
from valuz_agent.modules.sessions import capabilities
from valuz_agent.ports.citation_quality import (
    CitationQualityPolicyRegistry,
    CitationQualityPolicySnapshot,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.message_context import HostRef


def _session(*, skill_path: str | None = None, current_policy: bool = False) -> SimpleNamespace:
    instructions = "Keep answers concise."
    metadata = {"valuz": {"project_id": "project-1"}, "other": {"keep": True}}
    if current_policy:
        instructions = ensure_citation_system_policy(instructions)
        metadata["valuz"]["citation_policy_revision"] = CITATION_POLICY_REVISION
        metadata["valuz"]["citation_enabled"] = True
        metadata["valuz"]["citation_verification_enabled"] = True
        metadata["valuz"]["task_coverage_enabled"] = True
    return SimpleNamespace(
        id="session-1",
        user_id="owner-1",
        status="idle",
        skills=(skill_path,) if skill_path else (),
        instructions=instructions,
        metadata=metadata,
    )


@pytest.fixture
def citation_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "citation"
    path.mkdir()
    monkeypatch.setattr(
        "valuz_agent.adapters.capability_resolver.citation_skill_dir",
        lambda user_id: path,
    )
    return path


async def test_refresh_adds_skill_policy_and_revision_without_losing_metadata(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        task_coverage_enabled_override=True,
    )

    assert changed is True
    assert len(updates) == 1
    body = updates[0]
    assert body.skills == [str(citation_dir.resolve())]
    assert body.instructions.count("<citation-system-policy") == 1
    assert body.metadata["other"] == {"keep": True}
    assert body.metadata["valuz"]["citation_policy_revision"] == CITATION_POLICY_REVISION
    assert body.metadata["valuz"]["task_coverage_enabled"] is True


async def test_refresh_is_idempotent(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(skill_path=str(citation_dir.resolve()), current_policy=True)
    snapshot = await ext.citation_quality_policies.resolve(
        "owner-1",
        session_metadata=session.metadata,
    )
    session.metadata["valuz"]["citation_quality_policy"] = snapshot.session_metadata()
    task_policy = dict(snapshot.config["task_coverage"])
    task_policy["layers"] = [dict(item) for item in snapshot.layers]
    session.metadata["valuz"]["task_coverage_policy"] = task_policy

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def unexpected_update(*args: object, **kwargs: object) -> None:
        raise AssertionError("idempotent refresh must not write")

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", unexpected_update)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        task_coverage_enabled_override=True,
    )

    assert changed is False


async def test_missing_skill_still_upgrades_policy_and_fails_closed_at_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "citation-missing"
    monkeypatch.setattr(
        "valuz_agent.adapters.capability_resolver.citation_skill_dir",
        lambda user_id: missing,
    )
    session = _session()
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        task_coverage_enabled_override=True,
    )

    assert changed is True
    assert updates[0].skills == []
    assert "<citation-system-policy" in updates[0].instructions


async def test_refresh_stamps_trusted_quality_policy_and_replaces_user_value(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(skill_path=str(citation_dir.resolve()), current_policy=True)
    session.metadata["valuz"]["citation_quality_policy"] = {
        "policy_id": "user-forged",
        "revision": "disabled",
        "mode": "required-on-evidence",
        "config": {},
    }
    updates: list[object] = []

    class _OssPolicy:
        async def resolve(
            self,
            user_id: str,
            *,
            session_metadata: dict,
        ) -> CitationQualityPolicySnapshot:
            assert user_id == "owner-1"
            assert session_metadata["other"] == {"keep": True}
            return CitationQualityPolicySnapshot(
                policy_id="trusted-oss",
                revision="trusted-oss-v1",
                mode="required-on-evidence",
                config={"rules": {"factual_claim": {"citation_required": True}}},
                layer="oss",
            )

    class _DistributionPolicy:
        async def resolve(
            self,
            user_id: str,
            *,
            session_metadata: dict,
        ) -> CitationQualityPolicySnapshot:
            return CitationQualityPolicySnapshot(
                policy_id="trusted-edition",
                revision="trusted-v1",
                mode="strict-domain",
                config={"rules": {"numeric_claim": {"require_unit": True}}},
                layer="distribution",
            )

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    registry = CitationQualityPolicyRegistry(oss_provider=_OssPolicy())
    registry.register("distribution", _DistributionPolicy())
    monkeypatch.setattr(ext, "citation_quality_policies", registry)
    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        task_coverage_enabled_override=True,
    )

    assert changed is True
    stamped = updates[0].metadata["valuz"]["citation_quality_policy"]
    assert stamped["policy_id"] == "effective-citation-policy"
    assert stamped["revision"].startswith("citation-effective-")
    assert stamped["mode"] == "strict-domain"
    assert stamped["config"] == {
        "rules": {
            "factual_claim": {"citation_required": True},
            "numeric_claim": {"require_unit": True},
        }
    }
    assert stamped["layers"] == [
        {
            "layer": "oss",
            "policy_id": "trusted-oss",
            "revision": "trusted-oss-v1",
            "status": "active",
        },
        {
            "layer": "distribution",
            "policy_id": "trusted-edition",
            "revision": "trusted-v1",
            "status": "active",
        },
    ]


async def test_verification_remains_independent_when_citation_rendering_is_disabled(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(skill_path=str(citation_dir.resolve()), current_policy=True)
    session.metadata["valuz"]["citation_quality_policy"] = {
        "policy_id": "old",
        "revision": "old",
        "mode": "strict-domain",
        "config": {},
    }
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=False,
        verification_enabled_override=True,
        task_coverage_enabled_override=False,
    )

    assert changed is True
    body = updates[0]
    # Audit-only still needs the private Evidence binding protocol.  Keeping
    # it must not implicitly turn user-visible Citation projection back on.
    assert body.skills == [str(citation_dir.resolve())]
    assert "<citation-system-policy" in body.instructions
    assert body.metadata["valuz"]["citation_policy_revision"] == CITATION_POLICY_REVISION
    assert body.metadata["valuz"]["citation_enabled"] is False
    assert body.metadata["valuz"]["citation_verification_enabled"] is True
    assert body.metadata["valuz"]["task_coverage_enabled"] is False
    assert body.metadata["valuz"]["citation_quality_policy"]["policy_id"]


async def test_task_coverage_does_not_implicitly_load_citation_quality_policy(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(skill_path=str(citation_dir.resolve()), current_policy=True)
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=False,
        verification_enabled_override=False,
        task_coverage_enabled_override=True,
    )

    assert changed is True
    body = updates[0]
    assert body.skills == []
    assert "<citation-system-policy" not in body.instructions
    assert body.metadata["valuz"]["citation_enabled"] is False
    assert body.metadata["valuz"]["citation_verification_enabled"] is False
    assert body.metadata["valuz"]["task_coverage_enabled"] is True
    assert "citation_quality_policy" not in body.metadata["valuz"]
    task_policy = body.metadata["valuz"]["task_coverage_policy"]
    assert task_policy["revision"] == "oss-task-coverage-v2"
    assert task_policy["review_guidance"]["supplement_rules"] == {
        "append_only": True,
        "do_not_repeat_completed_content": True,
        "preserve_visible_history": True,
    }


@pytest.mark.parametrize(
    ("citation_enabled", "verification_enabled", "task_coverage_enabled"),
    [
        (False, False, False),
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
async def test_three_preferences_are_independent_for_every_truth_table_row(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    citation_enabled: bool,
    verification_enabled: bool,
    task_coverage_enabled: bool,
) -> None:
    session = _session()
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=citation_enabled,
        verification_enabled_override=verification_enabled,
        task_coverage_enabled_override=task_coverage_enabled,
    )

    body = updates[0]
    valuz = body.metadata["valuz"]
    evidence_binding_enabled = citation_enabled or verification_enabled
    assert (str(citation_dir.resolve()) in body.skills) is evidence_binding_enabled
    assert ("<citation-system-policy" in body.instructions) is evidence_binding_enabled
    assert valuz["citation_enabled"] is citation_enabled
    assert valuz["citation_verification_enabled"] is verification_enabled
    assert valuz["task_coverage_enabled"] is task_coverage_enabled
    assert ("citation_quality_policy" in valuz) is verification_enabled
    assert ("task_coverage_policy" in valuz) is task_coverage_enabled


class _WorkbenchPolicy:
    """Test double for an edition host-capability policy."""

    def __init__(self, host_types: set[str]) -> None:
        self.host_types = host_types

    def task_coverage_override(self, host_ref: HostRef) -> bool | None:
        return False if host_ref.host_type in self.host_types else None


@pytest.fixture
def workbench_policy() -> Iterator[_WorkbenchPolicy]:
    policy = _WorkbenchPolicy({"finance.research-desk", "finance.company-research"})
    ext.host_capability_policies.append(policy)
    try:
        yield policy
    finally:
        ext.host_capability_policies.remove(policy)


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host-override paths must not need a preferences read."""

    @asynccontextmanager
    async def _uow(commit: bool = False) -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(capabilities, "async_unit_of_work", _uow)


async def test_hosted_turn_policy_switches_task_coverage_off_and_stamps(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    workbench_policy: _WorkbenchPolicy,
    no_db: None,
) -> None:
    session = _session()
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    changed = await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        host_ref=HostRef(host_type="finance.research-desk", host_id="desk"),
    )

    assert changed is True
    valuz = updates[0].metadata["valuz"]
    assert valuz["task_coverage_enabled"] is False
    # The decision is stamped so host_ref-less turns keep it.
    assert valuz["task_coverage_host_override"] is False


async def test_stamp_keeps_hosted_decision_on_hostless_turns(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_db: None,
) -> None:
    # A previous hosted turn stamped the session; a queue-drain turn arrives
    # with no host_ref and the global preference would say True — the stamp
    # must win or the drain snaps coverage back on.
    session = _session()
    session.metadata["valuz"]["task_coverage_host_override"] = False
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)

    await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        host_ref=None,
    )

    valuz = updates[0].metadata["valuz"]
    assert valuz["task_coverage_enabled"] is False
    assert valuz["task_coverage_host_override"] is False


async def test_policy_without_opinion_defers_to_global_preference(
    citation_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    workbench_policy: _WorkbenchPolicy,
    no_db: None,
) -> None:
    session = _session()
    updates: list[object] = []

    async def get_session(user_id: str, session_id: str) -> object:
        return session

    async def update_session(user_id: str, session_id: str, body: object) -> object:
        updates.append(body)
        return session

    async def pref_true(db: object, user_id: str | None = None) -> bool:
        return True

    monkeypatch.setattr(capabilities.kernel_client, "get_session", get_session)
    monkeypatch.setattr(capabilities.kernel_client, "update_session", update_session)
    monkeypatch.setattr(
        "valuz_agent.modules.settings.preferences.get_conversation_task_coverage_enabled",
        pref_true,
    )

    await capabilities.refresh_citation_policy_for_session(
        "session-1",
        "owner-1",
        citation_enabled_override=True,
        verification_enabled_override=True,
        host_ref=HostRef(host_type="some.other-surface", host_id="x"),
    )

    valuz = updates[0].metadata["valuz"]
    assert valuz["task_coverage_enabled"] is True
    assert "task_coverage_host_override" not in valuz
