"""Skill loading from SKILL.md files with YAML frontmatter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    name: str
    description: str
    instruction: str
    tools: list[str] = field(default_factory=list)
    slash_command: str | None = None


class SkillLoader:
    def __init__(self, directories: list[str]) -> None:
        """directories: scan order = priority (project > user > bundled)."""
        self.directories = directories
        self._skills: dict[str, Skill] = {}

    def load(self) -> list[Skill]:
        for d in self.directories:
            for skill_file in Path(d).rglob("SKILL.md"):
                skill = self._parse(skill_file)
                if skill and skill.name not in self._skills:
                    self._skills[skill.name] = skill
        return list(self._skills.values())

    def _parse(self, path: Path) -> Skill | None:
        text = path.read_text()
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        meta: dict[str, Any] = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        name = meta.get("name")
        if not name:
            return None
        return Skill(
            name=name,
            description=meta.get("description", ""),
            instruction=body,
            tools=meta.get("allowed-tools") or meta.get("tools") or [],
            slash_command=meta.get("slash_command"),
        )

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def manifests(self) -> str:
        """One-line descriptions of all Skills, injected into system_prompt."""
        lines: list[str] = []
        for s in self._skills.values():
            line = f"- **{s.name}**: {s.description}"
            if s.slash_command:
                line += f" (`{s.slash_command}`)"
            lines.append(line)
        return "\n".join(lines)
