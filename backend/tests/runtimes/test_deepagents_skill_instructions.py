"""Mounted skills' SKILL.md bodies are injected into the deepagents prompt.

deepagents' ``SkillsMiddleware`` lists mounted skills (name + description +
path) and leaves reading the file to the model — measured on real tasks that
step never happens (#1148). ``SkillInstructionsMiddleware`` loads the bodies
once per graph from the ``skills_metadata`` state ``SkillsMiddleware``
populates and appends them to every model request's system message, within a
size budget. These tests drive the real ``SkillsMiddleware`` discovery so the
path format it records is the one we download.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage
from src.runtimes.deepagents.middleware import SkillInstructionsMiddleware


def _skill(root: Path, name: str, body: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\n{body}\n",
        encoding="utf-8",
    )


def _discover(tmp_path: Path) -> tuple[FilesystemBackend, dict[str, Any]]:
    """Run deepagents' own discovery over ``<tmp>/.agents/skills``."""
    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    update = SkillsMiddleware(backend=backend, sources=["/.agents/skills"]).before_agent(
        cast(Any, {}), cast(Any, None), cast(Any, {})
    )
    assert update is not None
    return backend, dict(update)


async def _captured_system_text(middleware: SkillInstructionsMiddleware, base: str = "BASE") -> str:
    captured: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> Any:
        captured.append(request)
        return object()

    await middleware.awrap_model_call(
        ModelRequest(
            model=cast(Any, object()),
            messages=[HumanMessage(content="hi")],
            system_message=SystemMessage(content=base),
        ),
        handler,
    )
    content = captured[0].system_message.content  # type: ignore[union-attr]
    assert isinstance(content, str)
    return content


async def test_bodies_are_appended_to_the_system_prompt(tmp_path: Path) -> None:
    skills = tmp_path / ".agents" / "skills"
    _skill(skills, "alpha", "Do the alpha thing.\n\n1. step one\n2. step two")
    _skill(skills, "beta", "Do the beta thing.")
    backend, state = _discover(tmp_path)

    middleware = SkillInstructionsMiddleware(backend)
    assert await middleware.abefore_agent(state, None) is None
    text = await _captured_system_text(middleware)

    assert text.startswith("BASE\n\n")
    assert "## Mounted Skill Instructions" in text
    assert '<skill name="alpha" path="/.agents/skills/alpha/SKILL.md">' in text
    assert "1. step one\n2. step two\n</skill>" in text
    assert (
        '<skill name="beta" path="/.agents/skills/beta/SKILL.md">\nDo the beta thing.\n</skill>'
        in text
    )
    # Frontmatter is metadata for the listing, not instructions.
    assert "name: alpha" not in text
    assert "description: The alpha skill." not in text


async def test_sync_hooks_match_async(tmp_path: Path) -> None:
    _skill(tmp_path / ".agents" / "skills", "alpha", "Do the alpha thing.")
    backend, state = _discover(tmp_path)
    middleware = SkillInstructionsMiddleware(backend)
    assert middleware.before_agent(state, None) is None

    captured: list[ModelRequest] = []

    def handler(request: ModelRequest) -> Any:
        captured.append(request)
        return object()

    middleware.wrap_model_call(
        ModelRequest(
            model=cast(Any, object()),
            messages=[HumanMessage(content="hi")],
            system_message=SystemMessage(content="BASE"),
        ),
        handler,
    )
    assert "Do the alpha thing." in cast(str, captured[0].system_message.content)  # type: ignore[union-attr]


async def test_no_mounted_skills_leaves_the_request_alone(tmp_path: Path) -> None:
    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    middleware = SkillInstructionsMiddleware(backend)
    assert await middleware.abefore_agent({}, None) is None
    assert await _captured_system_text(middleware) == "BASE"
    # A system-less request stays system-less rather than growing a message.
    captured: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> Any:
        captured.append(request)
        return object()

    await middleware.awrap_model_call(
        ModelRequest(model=cast(Any, object()), messages=[HumanMessage(content="hi")]),
        handler,
    )
    assert captured[0].system_message is None


async def test_per_skill_cap_truncates_with_a_pointer_to_the_file(tmp_path: Path) -> None:
    _skill(tmp_path / ".agents" / "skills", "alpha", "x" * 500)
    backend, state = _discover(tmp_path)
    middleware = SkillInstructionsMiddleware(backend, per_skill_chars=100)
    await middleware.abefore_agent(state, None)
    text = await _captured_system_text(middleware)

    pointer = "\n\n[... truncated — read `/.agents/skills/alpha/SKILL.md` for the rest]"
    assert "x" * 100 + pointer in text
    assert "x" * 101 not in text


async def test_total_budget_overflow_falls_back_to_a_read_instruction(tmp_path: Path) -> None:
    skills = tmp_path / ".agents" / "skills"
    _skill(skills, "alpha", "a" * 60)
    _skill(skills, "beta", "b" * 60)
    backend, state = _discover(tmp_path)
    middleware = SkillInstructionsMiddleware(backend, total_chars=100)
    await middleware.abefore_agent(state, None)
    text = await _captured_system_text(middleware)

    assert "a" * 60 in text
    assert "b" * 60 not in text
    assert "too large to include inline" in text
    assert "- **beta**: `/.agents/skills/beta/SKILL.md`" in text


async def test_unreadable_skill_is_skipped_not_fatal(tmp_path: Path) -> None:
    _skill(tmp_path / ".agents" / "skills", "alpha", "Do the alpha thing.")
    backend, state = _discover(tmp_path)
    state["skills_metadata"].append(
        {"name": "ghost", "path": "/.agents/skills/ghost/SKILL.md", "description": "gone"}
    )
    middleware = SkillInstructionsMiddleware(backend)
    await middleware.abefore_agent(state, None)
    text = await _captured_system_text(middleware)

    assert "Do the alpha thing." in text
    assert "ghost" not in text


async def test_bodies_load_once_per_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _skill(tmp_path / ".agents" / "skills", "alpha", "Do the alpha thing.")
    backend, state = _discover(tmp_path)
    calls: list[list[str]] = []
    real = backend.adownload_files

    async def counting(paths: list[str]) -> Any:
        calls.append(list(paths))
        return await real(paths)

    monkeypatch.setattr(backend, "adownload_files", counting)
    middleware = SkillInstructionsMiddleware(backend)
    await middleware.abefore_agent(state, None)
    await middleware.abefore_agent(state, None)
    await _captured_system_text(middleware)
    await _captured_system_text(middleware)

    assert calls == [["/.agents/skills/alpha/SKILL.md"]]
