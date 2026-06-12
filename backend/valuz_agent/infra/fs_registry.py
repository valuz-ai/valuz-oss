"""Single point of truth for every local filesystem write the host performs.

Why this exists
---------------
The migration to Agent Harness V5 adds another writer to the local disk
(the kernel materializes per-session skill copies into ``{project.cwd}/.claude/skills/``).
On top of the existing valuz writers (data dir, secrets, doc assets, doc previews,
session attachments, skill staging, promoted skill targets) the surface is wide
enough that we need a single registry to:

1. **Audit**: any future "what wrote here?" question has one place to look.
2. **Test**: tests can swap a single registry rather than monkey-patching ``Path.home()``
   in a dozen modules.
3. **Sandbox readiness**: when we move to per-project sandboxes, only this file
   needs to learn about the new boundary.

Strict rule (enforced in Slice 8): valuz business modules MUST acquire any
host-writable path through ``FsRegistry``. Direct use of ``Path.home()``,
``os.path.expanduser``, or hardcoded ``~/.claude/...`` strings is forbidden
outside this module and ``infra.config``.

The kernel (``backend/kernel/``) is exempt from this rule — it owns its own
materialization roots under ``project.cwd`` and we feed it a clean cwd path
via ``project_cwd()``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from valuz_agent.infra.config import settings
from valuz_agent.ports.workspace import LocalWorkspaceHandle, WorkspaceHandle

ProjectKind = Literal["chat", "project"]
SkillSource = Literal["claude", "codex"]


class FsRegistry:
    """Resolves and ensures every host-writable path the host application uses.

    All public methods return ``Path`` objects and ensure the parent directory
    exists when the returned path is a file, or the directory itself exists when
    the returned path is a directory. They never write file content.
    """

    # ---- FS-1 / FS-2 — data root + secrets ----

    def data_dir(self) -> Path:
        path = settings.data_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def secrets_dir(self) -> Path:
        path = settings.secrets_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-3 — project cwd (project.cwd in V5 kernel terms) ----

    def project_cwd(
        self, project_id: str, kind: ProjectKind, root_path: str | None = None
    ) -> Path:
        """Return the absolute cwd for a project.

        - ``kind="project"``: caller-supplied ``root_path`` is used as-is. The
          path must already be absolute; it is not created.
        - ``kind="chat"``: a managed cwd is allocated under
          ``data_dir/projects/{project_id}/`` and created on demand. This
          satisfies V5's invariant that ``project.cwd`` is always present.
        """
        if kind == "project":
            if not root_path:
                raise ValueError("project requires an explicit root_path")
            path = Path(root_path).expanduser()
            if not path.is_absolute():
                raise ValueError(f"project root_path must be absolute: {root_path}")
            return path

        path = self.data_dir() / "projects" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_handle(
        self, project_id: str, kind: ProjectKind, root_path: str | None = None
    ) -> WorkspaceHandle:
        """Return a ``WorkspaceHandle`` for the project's cwd.

        The project-domain seam (see ``ports/workspace.py``): the local
        form hands back a ``LocalWorkspaceHandle`` over the real cwd; a
        future remote form would return a handle backed by the kernel
        file API without changing call sites.
        """
        return LocalWorkspaceHandle(self.project_cwd(project_id, kind, root_path))

    # ---- FS-4 / FS-5 — doc assets and previews ----

    def doc_asset_dir(self, doc_id: str) -> Path:
        path = self.data_dir() / "docs" / "assets" / doc_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def doc_preview_path(self, doc_id: str) -> Path:
        parent = self.data_dir() / "docs" / "preview"
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{doc_id}.md"

    # ---- FS-6 — session attachments (V5 UserMessage.attachments source) ----

    def attachment_dir(self, session_id: str) -> Path:
        path = self.data_dir() / "attachments" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-7 — skill-creator staging (project-cwd-keyed) ----
    #
    # Staging lives **inside the project cwd** under ``.skill-staging/``
    # so the agent can write to it via a relative ``./`` path it computes
    # from ``$PWD`` (its actual working directory). No session_id appears
    # in the path — concurrent sessions in the same project share this
    # subdir and rely on slug uniqueness; ``submit_skill`` validates the
    # slug is present at the expected path before the user is shown a
    # confirmation card.
    #
    # The legacy ``data_dir/skill-creator/staging/{session_id}/`` paths
    # are preserved as read-only fallbacks via
    # ``legacy_skill_staging_session_dir`` so any in-flight or already
    # staged content from before this refactor doesn't disappear.
    SKILL_STAGING_SUBDIR = ".skill-staging"

    def skill_staging_root_for_project(self, project_cwd: str | Path) -> Path:
        path = Path(project_cwd) / self.SKILL_STAGING_SUBDIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def skill_staging_dir_for_project(self, project_cwd: str | Path, slug: str) -> Path:
        path = self.skill_staging_root_for_project(project_cwd) / slug
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- Legacy (pre-2026-05 layout) — read-only fallback for content
    #    staged before the cwd-keyed convention landed. --

    def legacy_skill_staging_root(self) -> Path:
        return settings.skill_staging_dir

    def legacy_skill_staging_session_dir(self, session_id: str) -> Path:
        return self.legacy_skill_staging_root() / session_id

    # ---- FS-8 — user-scoped permanent skill targets ----

    def user_skill_root(self, source: SkillSource = "claude") -> Path:
        """Return the canonical write-target for promoted user skills.

        Default is ``~/.agents/skills/`` — the directory the Open Agent
        Skills standard (agentskills.io) tells agents to scan, so other
        compatible hosts discover the same library.
        ``$VALUZ_USER_SKILLS_DIR`` overrides for tests, packaged
        installers, or sandboxed runs.

        ``source`` is kept for API compatibility but ignored: the host
        manages a single skill catalog that any kernel runtime can
        materialize from. Legacy CLI locations
        (``~/.claude/skills/``, ``~/.codex/skills/``) are still readable
        via ``legacy_user_skill_roots()`` so skills the user authored in
        those CLIs are still discoverable.
        """
        del source  # one canonical root now; legacy roots are read-only
        override = os.environ.get("VALUZ_USER_SKILLS_DIR")
        if override:
            path = Path(override).expanduser()
        else:
            path = Path.home() / ".agents" / "skills"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_skill_dir(self, slug: str, source: SkillSource = "claude") -> Path:
        return self.user_skill_root(source) / slug

    def official_skill_root(self) -> Path:
        """Return the canonical home for bundled / official skills.

        Defaults to ``<data_dir>/official-skills/`` (i.e.
        ``~/.valuz/app/official-skills/``) so the host owns the location.
        ``$VALUZ_OFFICIAL_SKILLS_DIR`` overrides for tests / sandboxed
        runs. The directory is created lazily by
        ``sync_bundled_official_skills`` on first boot.
        """
        override = os.environ.get("VALUZ_OFFICIAL_SKILLS_DIR")
        if override:
            return Path(override).expanduser()
        return self.data_dir() / "official-skills"

    def legacy_user_skill_roots(self) -> list[Path]:
        """Return the legacy CLI skill locations for read-only discovery.

        Used by ``providers.skills_filesystem`` to surface skills the
        user authored in their Claude Code / Codex CLI before adopting
        Valuz. New promotions never write here — the canonical target
        is ``user_skill_root()`` (``~/.agents/skills/``).
        """
        roots: list[Path] = []
        for sub in (".claude/skills", ".codex/skills"):
            candidate = Path.home() / sub
            if candidate.exists():
                roots.append(candidate)
        return roots

    # ---- FS-9 — project-scoped permanent skill targets ----

    def project_skill_root(self, project_cwd: str | Path) -> Path:
        return Path(project_cwd) / ".claude" / "skills"

    def project_skill_dir(self, project_cwd: str | Path, slug: str) -> Path:
        return self.project_skill_root(project_cwd) / slug

    # ---- FS-11 — task project directories (lead-dispatch-mvp §S6) ----
    #
    # Layout under project.cwd:
    #   tasks/<task_id>-<slug>.md       — task narrative file (file-as-truth)
    #   tasks/<task_id>/runs/run-N/     — per-subtask cwd, ONLY for opt-in
    #                                     repo-worktree isolation (v2.1)
    #
    # Note: lead and members run in the SHARED project cwd by default (v2.1,
    # M10 附录 D) so they read/write project files natively. There is no
    # per-task ``workdir/`` subdir anymore.

    def task_path(self, project_cwd: str | Path, task_id: str, slug: str) -> Path:
        """Return the path to the task narrative markdown file.

        ``<project_cwd>/tasks/<task_id>-<slug>.md``
        Parent directory is created on demand; the file itself is not written.

        The ``slug`` is the lead agent's handle, which may now be CJK
        (VALUZ-AGENT-SLUG allows Chinese slugs as logical identifiers). The
        ``task_id`` already guarantees filename uniqueness, so the slug
        suffix is purely a human-readable hint — sanitize it to ASCII
        ``[A-Za-z0-9-]`` here so a CJK slug never leaks into an on-disk path
        (keeps git / cross-tool behavior boring). Falls back to ``task``
        when nothing ASCII survives.
        """
        import re

        ascii_slug = re.sub(r"[^A-Za-z0-9-]+", "-", slug).strip("-") or "task"
        parent = Path(project_cwd) / "tasks"
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{task_id}-{ascii_slug}.md"

    def subrun_dir(
        self,
        project_cwd: str | Path,
        task_id: str,
        n: int,
        mode: str = "isolated",
        base_ref: str = "HEAD",
    ) -> Path:
        """Return (and create) the working directory for sub-run number *n*.

        ``<project_cwd>/tasks/<task_id>/runs/run-N/``

        *mode* controls materialisation:
          ``isolated`` (default) — plain ``mkdir``. No git involvement.
          ``repo-worktree``      — attempt ``git worktree add -b <branch> <dir> <base_ref>``
                                   if *project_cwd* is inside a git repository;
                                   falls back to plain mkdir + a warning when the
                                   project is not a git repo (or git is unavailable).
        """
        import logging
        import subprocess

        _log = logging.getLogger(__name__)

        run_dir = Path(project_cwd) / "tasks" / task_id / "runs" / f"run-{n}"

        if mode == "isolated":
            run_dir.mkdir(parents=True, exist_ok=True)
            return run_dir

        if mode == "repo-worktree":
            # Only attempt worktree if the project_cwd is inside a git repo
            git_root: str | None = None
            try:
                result = subprocess.run(
                    ["git", "-C", str(project_cwd), "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    git_root = result.stdout.strip()
            except Exception:  # noqa: BLE001
                pass

            if git_root is None:
                _log.warning(
                    "subrun_dir: project_cwd %s is not a git repo; "
                    "falling back to plain mkdir for run-%d (mode=repo-worktree)",
                    project_cwd,
                    n,
                )
                run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir

            # Build a branch name unique to this run
            branch = f"task/{task_id}/run-{n}"
            run_dir.parent.mkdir(parents=True, exist_ok=True)

            try:
                wt_result = subprocess.run(
                    [
                        "git",
                        "-C",
                        git_root,
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(run_dir),
                        base_ref,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if wt_result.returncode != 0:
                    _log.warning(
                        "subrun_dir: git worktree add failed (%s); "
                        "falling back to mkdir for run-%d",
                        wt_result.stderr.strip(),
                        n,
                    )
                    run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "subrun_dir: git worktree add raised %s; falling back to mkdir for run-%d",
                    exc,
                    n,
                )
                run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir

        # Unknown mode — fall back to isolated
        _log.warning("subrun_dir: unknown mode %r; using isolated", mode)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    # ---- FS-10 — parser plugin local assets (model files, licenses) ----
    #
    # Each plugin gets its own subdirectory under ``data_dir/models/``.
    # ``RapidOcrSetupJob`` writes PP-OCRv5 ONNX files + the Apache 2.0
    # ``LICENSE`` + a ``READY`` marker into ``models/light_local/rapidocr/``.
    # ``parser_light_local._build_rapidocr`` reads the same directory and
    # constructs ``rapidocr.RapidOCR`` with explicit ``params={"Det.model_path":
    # ...}`` so the library's auto-download path is short-circuited — the
    # runtime only consults the directory we already prepared with
    # explicit user authorization.

    def parser_model_dir(self, plugin_id: str, subkind: str | None = None) -> Path:
        """Return the canonical model-asset directory for a parser plugin.

        ``subkind`` namespaces multiple bundles within one plugin (e.g.
        ``parser_model_dir("light_local", "rapidocr")`` →
        ``~/.valuz/app/models/light_local/rapidocr/``). Created on demand.
        """
        if not plugin_id or "/" in plugin_id or ".." in plugin_id:
            raise ValueError(f"invalid plugin_id: {plugin_id!r}")
        if subkind is not None and ("/" in subkind or ".." in subkind):
            raise ValueError(f"invalid subkind: {subkind!r}")
        path = self.data_dir() / "models" / plugin_id
        if subkind:
            path = path / subkind
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-13 — onboarding example project directory ----
    #
    # User-visible directory for the onboarding "示例项目".  Lives under
    # ``user_project_root`` (default ``~/Valuz``) so it appears in the
    # user's home folder rather than in the hidden ``~/.valuz`` data dir.

    def example_project_dir(self) -> Path:
        """Return (and create) the example-project directory.

        ``<user_project_root>/示例项目`` — created on demand.
        Used exclusively by the onboarding ``POST /v1/onboarding/example-project``
        endpoint; the path is then handed to ``ProjectService.create_project``
        as ``root_path``.
        """
        path = settings.user_project_root / "示例项目"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-12 — memory store directories (memory-system-design §2.1) ----
    #
    #   global  → <data_dir>/memory/                       (cross-project, per-user)
    #   project → <project_cwd>/.valuz/memory/             (project, cross-session+task)
    #   task    → <project_cwd>/.valuz/memory/tasks/<id>/  (single task, lead+members)
    #
    # Each scope dir holds topic files ``<name>.md`` (frontmatter) + a single
    # ``MEMORY.md`` index. Returns (and creates) the scope directory.

    def memory_dir(
        self,
        scope: Literal["global", "project", "task"],
        *,
        project_cwd: str | Path | None = None,
        task_id: str | None = None,
    ) -> Path:
        if scope == "global":
            path = self.data_dir() / "memory"
        elif scope == "project":
            if not project_cwd:
                raise ValueError("project memory requires project_cwd")
            path = Path(project_cwd) / ".valuz" / "memory"
        elif scope == "task":
            if not project_cwd or not task_id:
                raise ValueError("task memory requires project_cwd and task_id")
            if "/" in task_id or ".." in task_id:
                raise ValueError(f"invalid task_id: {task_id!r}")
            path = Path(project_cwd) / ".valuz" / "memory" / "tasks" / task_id
        else:  # pragma: no cover - guarded by Literal
            raise ValueError(f"unknown memory scope: {scope!r}")
        path.mkdir(parents=True, exist_ok=True)
        return path


fs_registry = FsRegistry()

__all__ = ["FsRegistry", "fs_registry"]
