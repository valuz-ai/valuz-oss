from __future__ import annotations

from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.skills_filesystem import (
    _coerce_version,
    _compute_dir_hash,
    _detect_manifest,
    _read_manifest_cached,
)
from valuz_agent.integrations.skills_official_bootstrap import is_bundled_skill
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest


def _default_official_skill_root(user_id: str) -> Path:
    """Canonical home for officially-distributed skills.

    Always reads through ``fs_registry`` so the location stays
    consistent with the bootstrap sync target — both surfaces resolve
    to ``~/.valuz-oss/official-skills/`` by default.
    """
    return fs_registry.official_skill_root(user_id=user_id)


class OfficialSkillSource:
    name = "official"

    def __init__(self, official_dir: Path | None = None) -> None:
        self._dir = official_dir

    def _roots(self, user_id: str) -> list[tuple[Path, bool]]:
        """``(root, ships_with_the_install)`` in INCREASING precedence.

        The per-user root holds official content that belongs to one user: a
        template skill materialized by an agent-pack import, an externally
        installed official skill, and — on an install that predates system
        roots — legacy copies of the shipped packages.

        Shipped roots come last, so a shipped package wins a slug collision.
        That is the point of the ordering: a legacy copy must never shadow the
        version this release actually carries. It also matches
        ``capability_resolver.official_skill_dir``, which resolves the same way
        for the always-on baseline.

        An explicit ``official_dir`` keeps the single-root behaviour the
        callers that pass one already rely on.
        """
        from valuz_agent.infra.fs_registry import fs_registry

        if self._dir is not None:
            return [(self._dir, False)]
        roots: list[tuple[Path, bool]] = [(_default_official_skill_root(user_id), False)]
        roots.extend((root, True) for root in fs_registry.system_skill_roots())
        return roots

    def list_skills(
        self, ctx: RuntimeContext, *, compute_content_hash: bool = True
    ) -> list[SkillManifest]:
        """List official skill manifests.

        ``compute_content_hash`` gates ``_compute_dir_hash`` (reads every file in
        each skill dir — slow on a network filesystem, needed only by the indexer).
        Display/catalog listing passes ``False`` and reads only each SKILL.md
        (cached). See ``FilesystemSkillSource.list_skills``.
        """
        if ctx.user_id is None:
            raise ValueError("user_id is required to list official skills")

        by_slug: dict[str, SkillManifest] = {}
        for root, shipped in self._roots(ctx.user_id):
            if not root.exists():
                continue
            for manifest in self._list_root(
                root, shipped=shipped, compute_content_hash=compute_content_hash
            ):
                by_slug[manifest.slug] = manifest  # later root wins
        return [by_slug[slug] for slug in sorted(by_slug)]

    def _list_root(
        self, official_dir: Path, *, shipped: bool, compute_content_hash: bool
    ) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for skill_dir in sorted(p for p in official_dir.iterdir() if p.is_dir()):
            manifest_path = _detect_manifest(skill_dir)
            if manifest_path is None:
                continue

            metadata, body, _raw, manifest_hash = _read_manifest_cached(manifest_path)
            name = str(metadata.get("name") or skill_dir.name)
            description = str(metadata.get("description") or self._summary_from_body(body))
            tags = metadata.get("tags")
            version = _coerce_version(metadata.get("version"))
            content_hash = _compute_dir_hash(skill_dir) if compute_content_hash else None

            # Anything under a system root ships with the install and is
            # bundled by definition — the ``.bundled-version`` marker only ever
            # described a COPY, and there is no copy any more. Under the
            # per-user root the marker still tells a materialized bundle apart
            # from an externally installed official skill.
            bundled = True if shipped else is_bundled_skill(skill_dir)
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
