from __future__ import annotations

import hashlib
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.skills_filesystem import (
    _coerce_version,
    _compute_dir_hash,
    _detect_manifest,
    _extract_frontmatter,
    _read_text,
)
from valuz_agent.integrations.skills_official_bootstrap import is_bundled_skill
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest


def _default_official_skill_root() -> Path:
    """Canonical home for officially-distributed skills.

    Always reads through ``fs_registry`` so the location stays
    consistent with the bootstrap sync target — both surfaces resolve
    to ``~/.valuz/app/official-skills/`` by default.
    """
    return fs_registry.official_skill_root()


class OfficialSkillSource:
    name = "official"

    def __init__(self, official_dir: Path | None = None) -> None:
        self._dir = official_dir or _default_official_skill_root()

    def list_skills(self, ctx: RuntimeContext) -> list[SkillManifest]:
        if not self._dir.exists():
            return []

        manifests: list[SkillManifest] = []
        for skill_dir in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            manifest_path = _detect_manifest(skill_dir)
            if manifest_path is None:
                continue

            raw_manifest = _read_text(manifest_path)
            metadata, body = _extract_frontmatter(raw_manifest)
            name = str(metadata.get("name") or skill_dir.name)
            description = str(metadata.get("description") or self._summary_from_body(body))
            tags = metadata.get("tags")
            version = _coerce_version(metadata.get("version"))
            manifest_hash = hashlib.sha256(raw_manifest.encode()).hexdigest()
            content_hash = _compute_dir_hash(skill_dir)

            bundled = is_bundled_skill(skill_dir)
            manifests.append(
                SkillManifest(
                    id=f"official:{skill_dir.name}",
                    name=name,
                    description=description,
                    scope="official",
                    source="official",
                    path=str(skill_dir.resolve(strict=False)),
                    slug=skill_dir.name,
                    readonly=True,
                    deletable=False,
                    is_locked=False if bundled else True,
                    lock_reason=None if bundled else "Connect Reportify to unlock official skills",
                    origin_label="Built-in" if bundled else "Official",
                    tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
                    content_hash=content_hash,
                    manifest_hash=manifest_hash,
                    version=version,
                )
            )
        return manifests

    @staticmethod
    def _summary_from_body(body: str) -> str:
        for line in body.splitlines():
            candidate = line.strip()
            if candidate and not candidate.startswith("#"):
                return candidate[:180]
        return "Official skill."
