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
data-dir path through ``FsRegistry`` — writes via ``data_dir()`` / the creating
helpers, and reads/probes via the non-creating ``resolve()`` (so the registry is
the single FS boundary for BOTH directions). Direct use of ``settings.data_dir``,
``Path.home()``, ``os.path.expanduser``, or hardcoded ``~/.claude/...`` strings
is forbidden outside this module, ``infra.config`` (path self-derivation), and
``boot.migrate_data_dir`` (the one-time root relocation).

The kernel (``backend/kernel/``) is exempt from this rule — it owns its own
materialization roots under ``project.cwd`` and we feed it a clean cwd path
via ``project_cwd()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from valuz_agent.infra.config import settings
from valuz_agent.ports.workspace import LocalWorkspaceHandle, WorkspaceHandle

ProjectKind = Literal["chat", "project"]
SkillSource = Literal["claude", "codex"]


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


class FsRegistry:
    """Resolves and ensures every host-writable path the host application uses.

    All public methods return ``Path`` objects and ensure the parent directory
    exists when the returned path is a file, or the directory itself exists when
    the returned path is a directory. They never write file content.
    """

    # ---- FS-1 / FS-2 — data root + secrets ----

    def user_dir_name(self, user_id: str) -> str:
        if not user_id:
            raise ValueError("user_id is required for user-scoped data dir")
        return user_id.replace("/", "__").replace("\\", "__")

    def _expand_user_template(self, root: Path, user_id: str) -> Path:
        raw = str(root)
        return Path(raw.replace("{user_id}", self.user_dir_name(user_id))).expanduser()

    def _shared_root(self) -> Path:
        raw = str(settings.data_dir)
        if "{user_id}" in raw:
            raw = raw.replace("{user_id}", "")
        return Path(raw).expanduser()

    def _expand_optional_user_template(self, root: str | Path, user_id: str | None) -> Path:
        raw = str(root)
        if "{user_id}" in raw:
            replacement = self.user_dir_name(user_id) if user_id else ""
            raw = raw.replace("{user_id}", replacement)
        return Path(raw).expanduser()

    def data_dir(self, user_id: str) -> Path:
        path = self._expand_user_template(settings.data_dir, user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _db_path(self, user_id: str) -> Path:
        path = self.data_dir(user_id) / settings.db_filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _kernel_db_path(self, user_id: str) -> Path:
        path = self.data_dir(user_id) / settings.kernel_db_filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def db_url(self, user_id: str) -> str:
        if settings.database_url:
            return settings.database_url
        return f"sqlite:///{self._db_path(user_id)}"

    def db_url_async(self, user_id: str) -> str:
        if settings.database_url:
            return _to_async_url(settings.database_url)
        return f"sqlite+aiosqlite:///{self._db_path(user_id)}"

    def kernel_db_url(self, user_id: str) -> str:
        if settings.kernel_database_url:
            return settings.kernel_database_url
        if settings.database_url:
            return settings.database_url
        return f"sqlite:///{self._kernel_db_path(user_id)}"

    def kernel_db_url_async(self, user_id: str) -> str:
        if settings.kernel_database_url:
            return _to_async_url(settings.kernel_database_url)
        if settings.database_url:
            return _to_async_url(settings.database_url)
        return f"sqlite+aiosqlite:///{self._kernel_db_path(user_id)}"

    def secrets_dir(self, user_id: str) -> Path:
        path = self.data_dir(user_id) / "secrets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cache_dir(self) -> Path:
        path = self._shared_root() / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def shared_root(self) -> Path:
        """Return the process-shared data root without requiring a user id."""
        path = self._shared_root()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def installation_file(self, user_id: str) -> Path:
        path = self.data_dir(user_id) / settings.installation_filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def browser_profile_dir(self, user_id: str) -> Path:
        """Dedicated, persistent Chrome ``--user-data-dir`` for the managed browser.

        An ISOLATED profile (never the user's everyday Chrome): a full-access
        agent only ever sees the logins the user puts here, which contains the
        blast radius. See docs/design/browser-feature.md §6 (security).
        """
        path = self.data_dir(user_id) / settings.browser_profile_subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def browser_bin_dir(self) -> Path:
        """Host bin dir prepended to the agent shell's PATH so a friendly
        ``chrome-devtools`` wrapper resolves (vs. the raw ``node <entry>`` /
        ``npx`` invocation). See docs/design/browser-feature.md §8.
        """
        path = self._shared_root() / "bin"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-3 — project cwd (project.cwd in V5 kernel terms) ----

    def project_cwd(
        self, user_id: str, project_id: str, kind: ProjectKind, root_path: str | None = None
    ) -> Path:
        """Return the absolute cwd for a project.

        - ``kind="project"``: caller-supplied ``root_path`` is used as-is. The
          path must already be absolute; it is not created.
        - ``kind="chat"``: a managed cwd is allocated under the configured
          user-visible project root and created on demand. Deployments that
          need user scoping can set ``VALUZ_USER_PROJECT_ROOT`` to a template
          such as ``~/Valuz/{user_id}``.
        """
        if kind == "project":
            if not root_path:
                raise ValueError("project requires an explicit root_path")
            path = Path(root_path).expanduser()
            if not path.is_absolute():
                raise ValueError(f"project root_path must be absolute: {root_path}")
            return path

        path = self.project_root(user_id) / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def project_root(self, user_id: str) -> Path:
        """Return the app-visible root for managed project workspaces.

        ``VALUZ_USER_PROJECT_ROOT`` may contain a ``{user_id}`` placeholder
        when deployments need per-user workspace roots. The placeholder expands
        to the filesystem-safe ``user_dir_name(user_id)`` value.

        This lets cloud deployments express the external mount contract without
        hard-coding deployment-type branches in OSS code:

        - ``valuz-conf/{user_id}/*`` -> ``$HOME/.valuz-dev/{user_id}/*``
        - ``user-project/{user_id}/workspace/*`` -> ``$HOME/Valuz/{user_id}/*``
        """
        path = self._expand_user_template(settings.user_project_root, user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_handle(
        self, user_id: str, project_id: str, kind: ProjectKind, root_path: str | None = None
    ) -> WorkspaceHandle:
        """Return a ``WorkspaceHandle`` for the project's cwd.

        The project-domain seam (see ``ports/workspace.py``): the local
        form hands back a ``LocalWorkspaceHandle`` over the real cwd; a
        future remote form would return a handle backed by the kernel
        file API without changing call sites.
        """
        return LocalWorkspaceHandle(self.project_cwd(user_id, project_id, kind, root_path))

    # ---- FS-4 / FS-5 — doc assets and previews ----

    def doc_asset_dir(self, user_id: str, doc_id: str) -> Path:
        path = self.data_dir(user_id) / "docs" / "assets" / doc_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def doc_preview_path(self, user_id: str, doc_id: str) -> Path:
        parent = self.data_dir(user_id) / "docs" / "preview"
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{doc_id}.md"

    def docs_root(self, user_id: str) -> Path:
        path = self.data_dir(user_id) / "docs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def docs_preview_dir(self, user_id: str) -> Path:
        path = self.docs_root(user_id) / "preview"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def docs_scan_state_dir(self, user_id: str) -> Path:
        path = self.docs_root(user_id) / "scan_state"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-6 — session attachments (V5 UserMessage.attachments source) ----

    def attachment_dir(self, user_id: str, session_id: str) -> Path:
        path = self.data_dir(user_id) / "attachments" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def kb_root(self, user_id: str) -> Path:
        """Return (and create) the knowledge-base root directory.

        ``<data_dir>/kb`` — the single home for KB content, replacing the
        legacy stray ``~/.valuz/kb`` path. Routed through the registry so KB
        writes share the same audit / sandbox boundary as every other host
        write. Created on demand.
        """
        path = self.data_dir(user_id) / "kb"
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

    def project_skill_staging_dir(self, project_cwd: str | Path, slug: str) -> Path:
        path = self.skill_staging_root_for_project(project_cwd) / slug
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- Legacy (pre-2026-05 layout) — read-only fallback for content
    #    staged before the cwd-keyed convention landed. --

    def legacy_skill_staging_root(self, user_id: str) -> Path:
        if settings.user_skill_staging_dir:
            path = self._expand_optional_user_template(settings.user_skill_staging_dir, user_id)
        else:
            path = self.data_dir(user_id) / "skill-creator" / "staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def legacy_skill_staging_session_dir(self, user_id: str, session_id: str) -> Path:
        return self.legacy_skill_staging_root(user_id) / session_id

    # ---- FS-8 — user-scoped permanent skill targets ----

    def user_skill_root(
        self,
        user_id: str,
    ) -> Path:
        """Return the canonical write-target for promoted user skills.

        ``settings.user_skills_dir`` is the single source of truth. It defaults
        to ``~/.agent/skills/`` and may contain ``{user_id}``, matching the
        ``VALUZ_DATA_DIR`` template convention.

        ``source`` is kept for API compatibility but ignored: the host
        manages a single skill catalog that any kernel runtime can
        materialize from. Legacy CLI locations
        (``~/.claude/skills/``, ``~/.codex/skills/``) are still readable
        via ``legacy_user_skill_roots()`` so skills the user authored in
        those CLIs are still discoverable.
        """
        path = self._expand_optional_user_template(settings.user_skills_dir, user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # def user_skill_dir(self, slug: str, source: SkillSource = "claude") -> Path:
    #     return self.user_skill_root(source) / slug

    def official_skill_root(self, *, user_id: str) -> Path:
        """Return the canonical home for bundled / official skills.

        Official skills are host-owned content under ``VALUZ_DATA_DIR``. The
        directory is created lazily by ``sync_bundled_official_skills`` on
        first boot.

        Passing ``user_id`` is required so ``data_dir`` templates naturally
        materialize bundled official skills under the owner data root.
        """
        return self.data_dir(user_id) / "official-skills"

    def legacy_user_skill_roots(self) -> list[Path]:
        """Return the legacy CLI skill locations for read-only discovery.

        Used by ``providers.skills_filesystem`` to surface skills the
        user authored in their Claude Code / Codex CLI before adopting
        Valuz. New promotions never write here — the canonical target
        is ``user_skill_root()`` (``~/.agent/skills/`` by default).
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

    def task_brief_path(self, base_cwd: str | Path, task_id: str, label: str = "goal") -> Path:
        """Return the path for a spilled (over-long) goal/brief doc.

        ``<base_cwd>/tasks/_briefs/<task_id>-<ascii_label>.md``

        Goal mode caps the ``/goal`` payload (bundled Claude CLI: 4000 chars); a
        task goal / subtask brief over the cap is written here and referenced by
        path instead (see ``agent_resolver.spill_goal_brief_if_too_long``).
        ``base_cwd`` is the session's working dir — the project cwd for a lead /
        shared member, or an isolated subrun dir for a repo-worktree member — so
        the doc always sits inside the agent's readable tree, next to the task
        narrative + run dirs. The parent dir is created; the file content is
        written by the caller (the registry never writes content). ``label`` is
        sanitized to ASCII like ``task_path`` so a CJK agent slug never leaks
        into an on-disk path.
        """
        import re

        ascii_label = re.sub(r"[^A-Za-z0-9-]+", "-", label).strip("-") or "goal"
        parent = Path(base_cwd) / "tasks" / "_briefs"
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"{task_id}-{ascii_label}.md"

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
    # ``RapidOcrSetupJob`` writes PP-OCRv6 ONNX files + the Apache 2.0
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
        ``~/.valuz-oss/models/light_local/rapidocr/``). Created on demand.
        """
        if not plugin_id or "/" in plugin_id or ".." in plugin_id:
            raise ValueError(f"invalid plugin_id: {plugin_id!r}")
        if subkind is not None and ("/" in subkind or ".." in subkind):
            raise ValueError(f"invalid subkind: {subkind!r}")
        path = self._shared_root() / "models" / plugin_id
        if subkind:
            path = path / subkind
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-13 — onboarding example project directory ----
    #
    # User-visible directory for the onboarding "示例项目".  Lives under the
    # configured ``project_root(user_id)``. The default root yields
    # ``~/Valuz/示例项目``; a cloud template such as ``~/Valuz/{user_id}`` yields
    # ``~/Valuz/<user_id>/示例项目``.

    def example_project_dir(self, user_id: str) -> Path:
        """Return (and create) the example-project directory.

        ``<project_root(user_id)>/示例项目`` — created on demand.
        Used exclusively by the onboarding ``POST /v1/onboarding/example-project``
        endpoint; the path is then handed to ``ProjectService.create_project``
        as ``root_path``.
        """
        path = self.project_root(user_id) / "示例项目"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- FS-12 — memory store directories (memory-system-design §3) ----
    #
    #   global  → <data_dir>/memories/               (root IS the global namespace)
    #   project → <data_dir>/memories/projects/<id>/ (per-project, keyed by project_id)
    #
    # Centralized under the valuz data dir (never inside a user's bound repo) and
    # keyed by stable ``project_id`` (decoupled from ``project.cwd``). global holds
    # the flat ``USER.md`` + ``MEMORY.md``; each project dir holds ``MEMORY.md``.
    # Returns (and creates) the scope directory.

    def memory_dir(
        self,
        user_id: str,
        scope: Literal["global", "project"],
        *,
        project_id: str | None = None,
    ) -> Path:
        if scope == "global":
            path = self.data_dir(user_id) / "memories"
        elif scope == "project":
            if not project_id:
                raise ValueError("project memory requires project_id")
            if "/" in project_id or ".." in project_id:
                raise ValueError(f"invalid project_id: {project_id!r}")
            path = self.data_dir(user_id) / "memories" / "projects" / project_id
        else:  # pragma: no cover - guarded by Literal
            raise ValueError(f"unknown memory scope: {scope!r}")
        path.mkdir(parents=True, exist_ok=True)
        return path


fs_registry = FsRegistry()

__all__ = ["FsRegistry", "fs_registry"]
