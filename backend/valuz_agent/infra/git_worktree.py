"""Git worktree primitives for project worktree isolation.

Synchronous ``subprocess`` wrappers around the ``git worktree`` porcelain —
callers on the event loop MUST go through ``asyncio.to_thread``. Higher-level
policy (slug generation, sidecar metadata, locking, cleanup rules) lives in
``modules/worktrees/service.py``; this module knows only git.

Design notes (docs/design/project-worktree-design.md):

- Layout: ``<git_root>/.valuz/worktrees/<flattened-slug>`` with branch
  ``valuz/<origin>-<flattened-slug>``. Slugs are validated against a strict
  allowlist so path / branch / slug stay an injective mapping and a slug can
  never escape the worktrees directory.
- No network: the base ref defaults to the main workspace ``HEAD``; we never
  fetch. All git calls run with prompts disabled so a credential-hungry
  repo can't hang a headless backend.
- Deletion decisions are fail-closed: ``has_changes`` treats *any* git
  failure as "has changes" so automation never removes work it can't verify.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

BRANCH_NAMESPACE = "valuz"
MAX_SLUG_LENGTH = 64
WORKTREES_SUBDIR = PurePosixPath(".valuz/worktrees")

_SLUG_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")

# Prevent git/SSH from prompting for credentials (which would hang the
# backend): GIT_TERMINAL_PROMPT=0 blocks /dev/tty prompts, empty GIT_ASKPASS
# disables askpass GUIs, and stdin is closed on every call below.
_GIT_NO_PROMPT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}


class GitWorktreeError(RuntimeError):
    """A git invocation failed in a way the caller should surface."""


class InvalidWorktreeSlugError(ValueError):
    """The slug fails validation (length / charset / traversal)."""


def validate_slug(slug: str) -> None:
    """Reject slugs that could escape the worktrees dir or break git refs.

    The slug is joined into ``.valuz/worktrees/<slug>`` and into a branch
    name; ``..`` segments, absolute paths, or charset surprises in either
    place are unacceptable. Forward slashes are allowed for readability and
    flattened to ``+`` (see ``flatten_slug``) before hitting git or the fs.
    """
    if not slug:
        raise InvalidWorktreeSlugError("worktree name must not be empty")
    if len(slug) > MAX_SLUG_LENGTH:
        raise InvalidWorktreeSlugError(
            f"worktree name must be {MAX_SLUG_LENGTH} characters or fewer (got {len(slug)})"
        )
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise InvalidWorktreeSlugError(
                f"worktree name {slug!r} must not contain '.' or '..' path segments"
            )
        if not _SLUG_SEGMENT.match(segment):
            raise InvalidWorktreeSlugError(
                f"worktree name {slug!r}: each '/'-separated segment must be non-empty "
                "and contain only letters, digits, dots, underscores, and dashes"
            )


def flatten_slug(slug: str) -> str:
    """Flatten nested slugs (``user/feature`` → ``user+feature``).

    Nesting is unsafe in both homes of the slug: git refs hit D/F conflicts
    (``valuz/u-a`` file vs ``valuz/u-a/b`` dir) and a nested worktree
    directory would live *inside* its parent worktree, so removing the parent
    deletes the child. ``+`` is a valid branch/path character but is NOT in
    the slug allowlist, so the mapping is injective.
    """
    return slug.replace("/", "+")


def branch_name(origin: str, slug: str) -> str:
    """``valuz/<origin>-<flattened-slug>`` — namespace + cleanup fail-safe.

    ``origin`` ∈ {"u", "task"}: user-named worktrees are never auto-swept;
    the prefix lets the sweeper double-check eligibility even if the sidecar
    metadata is lost.
    """
    return f"{BRANCH_NAMESPACE}/{origin}-{flatten_slug(slug)}"


def worktrees_dir(git_root: Path) -> Path:
    return git_root / WORKTREES_SUBDIR


def worktree_path(git_root: Path, slug: str) -> Path:
    return worktrees_dir(git_root) / flatten_slug(slug)


@dataclass(frozen=True)
class GitInfo:
    """Result of ``detect_git``: where the repo lives relative to a path."""

    git_root: Path
    # Absolute common .git dir (shared across worktrees). For a session
    # running inside a linked worktree this still points at the main repo.
    common_dir: Path


@dataclass(frozen=True)
class WorktreeInfo:
    slug: str
    path: Path
    branch: str
    # The commit the worktree was created from (creation) or its current
    # HEAD (resume). Callers persist the creation value as the change-
    # detection anchor.
    head_sha: str
    created: bool


@dataclass(frozen=True)
class WorktreeStatus:
    """Cheap counts for confirm dialogs / the worktrees panel."""

    dirty_files: int
    ahead_commits: int


def _run_git(
    args: list[str],
    cwd: Path | str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env={**os.environ, **_GIT_NO_PROMPT_ENV},
    )


def git_available() -> bool:
    """True when a usable git binary is on PATH.

    macOS gotcha: ``/usr/bin/git`` is an Xcode CLT shim — *executing* it
    without the CLT installed pops a GUI install dialog, which a headless
    backend must never trigger. ``xcode-select -p`` answers "is the CLT
    installed" without any GUI side effect, so probe that first.
    """
    git = shutil.which("git")
    if git is None:
        return False
    if git.startswith("/usr/bin/") and shutil.which("xcode-select"):
        try:
            probe = subprocess.run(
                ["xcode-select", "-p"],
                capture_output=True,
                timeout=5,
            )
        except Exception:  # noqa: BLE001 — treat any probe failure as unavailable
            return False
        return probe.returncode == 0
    return True


def detect_git(path: Path | str) -> GitInfo | None:
    """Return repo info for *path*, or ``None`` when it isn't in a git repo.

    Callers must gate on ``git_available()`` first (see the macOS note
    there); this function assumes git can be executed safely.
    """
    try:
        result = _run_git(
            ["rev-parse", "--show-toplevel", "--git-common-dir"],
            cwd=path,
            timeout=10,
        )
    except Exception:  # noqa: BLE001 — missing binary / timeout → not a repo
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    if len(lines) != 2:
        return None
    git_root = Path(lines[0]).resolve()
    common_dir = Path(lines[1])
    if not common_dir.is_absolute():
        common_dir = (Path(path) / common_dir).resolve()
    return GitInfo(git_root=git_root, common_dir=common_dir.resolve())


def get_or_create(
    git_root: Path,
    slug: str,
    origin: str,
    base_ref: str = "HEAD",
) -> WorktreeInfo:
    """Create the worktree for *slug*, or resume it if it already exists.

    Fast-resume: an existing worktree (its ``.git`` pointer file present) is
    reused as-is — no fetch, no branch reset — so resuming a session never
    touches the network or clobbers in-progress work.
    """
    validate_slug(slug)
    path = worktree_path(git_root, slug)
    branch = branch_name(origin, slug)

    if (path / ".git").exists():
        head = _run_git(["rev-parse", "HEAD"], cwd=path, timeout=10)
        if head.returncode != 0:
            raise GitWorktreeError(
                f"worktree at {path} exists but is broken: {head.stderr.strip()}"
            )
        return WorktreeInfo(
            slug=slug,
            path=path,
            branch=branch,
            head_sha=head.stdout.strip(),
            created=False,
        )

    base = _run_git(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=git_root)
    if base.returncode != 0:
        raise GitWorktreeError(
            f"cannot resolve base ref {base_ref!r}: {base.stderr.strip() or 'rev-parse failed'}"
        )
    base_sha = base.stdout.strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    # -B (not -b): reset an orphan branch left behind by a manually deleted
    # worktree directory instead of failing the create.
    result = _run_git(
        ["worktree", "add", "-B", branch, str(path), base_sha],
        cwd=git_root,
        timeout=120,
    )
    if result.returncode != 0:
        raise GitWorktreeError(f"failed to create worktree: {result.stderr.strip()}")

    return WorktreeInfo(slug=slug, path=path, branch=branch, head_sha=base_sha, created=True)


def has_changes(path: Path, base_sha: str) -> bool:
    """True when the worktree holds work worth keeping. FAIL-CLOSED.

    Dirty working tree, commits past *base_sha*, or ANY git failure all
    return True — callers use this to decide whether a worktree may be
    removed, so uncertainty must block removal.
    """
    try:
        status = _run_git(["status", "--porcelain"], cwd=path)
    except Exception:  # noqa: BLE001
        return True
    if status.returncode != 0 or status.stdout.strip():
        return True

    try:
        rev_list = _run_git(["rev-list", "--count", f"{base_sha}..HEAD"], cwd=path)
    except Exception:  # noqa: BLE001
        return True
    if rev_list.returncode != 0:
        return True
    try:
        return int(rev_list.stdout.strip()) > 0
    except ValueError:
        return True


def status_counts(path: Path, base_sha: str) -> WorktreeStatus | None:
    """Dirty-file / ahead-commit counts, or ``None`` when unverifiable."""
    try:
        status = _run_git(["status", "--porcelain"], cwd=path)
        rev_list = _run_git(["rev-list", "--count", f"{base_sha}..HEAD"], cwd=path)
    except Exception:  # noqa: BLE001
        return None
    if status.returncode != 0 or rev_list.returncode != 0:
        return None
    dirty = len([line for line in status.stdout.splitlines() if line.strip()])
    try:
        ahead = int(rev_list.stdout.strip())
    except ValueError:
        return None
    return WorktreeStatus(dirty_files=dirty, ahead_commits=ahead)


def remove(git_root: Path, path: Path, branch: str | None) -> None:
    """Remove a worktree and (best-effort) its branch.

    Runs from *git_root*, never from the doomed path. Branch deletion is
    best-effort: ``-D`` fails when the branch is checked out elsewhere,
    which is not worth failing the removal over.
    """
    result = _run_git(
        ["worktree", "remove", "--force", str(path)],
        cwd=git_root,
        timeout=60,
    )
    if result.returncode != 0:
        raise GitWorktreeError(f"failed to remove worktree: {result.stderr.strip()}")

    if branch:
        branch_result = _run_git(["branch", "-D", branch], cwd=git_root)
        if branch_result.returncode != 0:
            logger.warning(
                "git_worktree: could not delete branch %s: %s",
                branch,
                branch_result.stderr.strip(),
            )


@dataclass(frozen=True)
class ListedWorktree:
    path: Path
    head_sha: str
    branch: str | None


def list_worktrees(git_root: Path) -> list[ListedWorktree]:
    """Worktrees registered under ``<git_root>/.valuz/worktrees/`` only.

    Parses ``git worktree list --porcelain`` (stanzas separated by blank
    lines: ``worktree <path>`` / ``HEAD <sha>`` / ``branch <ref>``); the
    main checkout and foreign worktrees are filtered out.
    """
    result = _run_git(["worktree", "list", "--porcelain"], cwd=git_root, timeout=30)
    if result.returncode != 0:
        raise GitWorktreeError(f"git worktree list failed: {result.stderr.strip()}")

    managed_root = worktrees_dir(git_root).resolve()
    items: list[ListedWorktree] = []
    path: Path | None = None
    head = ""
    branch: str | None = None

    def flush() -> None:
        nonlocal path, head, branch
        if path is not None and managed_root in path.parents:
            items.append(ListedWorktree(path=path, head_sha=head, branch=branch))
        path, head, branch = None, "", None

    for line in result.stdout.splitlines():
        if not line.strip():
            flush()
            continue
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :]).resolve()
        elif line.startswith("HEAD "):
            head = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            branch = ref.removeprefix("refs/heads/")
    flush()
    return items


# Host bookkeeping that lives inside the working tree and must never read as
# the user's own uncommitted work. ``.valuz/`` holds the managed worktrees;
# ``.artifact/`` holds the immutable snapshot of every delivered version.
#
# Excluding the artifact store is not cosmetic. ``git status --porcelain``
# reports untracked files, so without this ANY delivery would make a worktree
# permanently "dirty" — which silently disables clean-teardown and makes
# ``discard`` refuse with "has work worth keeping" about files the user never
# wrote. Whether a worktree holding deliverables may be removed is a decision
# worth making explicitly (see ``worktree_service.cleanup_if_clean``), not one
# to inherit from what git happens to consider noise.
_EXCLUDED_DIRS = (
    (".valuz/", "# valuz project worktrees"),
    (".artifact/", "# valuz delivered-artifact snapshots"),
)


def ensure_info_exclude(common_dir: Path) -> None:
    """Idempotently exclude the host's in-tree bookkeeping from git status.

    Written to ``<common_dir>/info/exclude`` (repo-local, shared by all
    worktrees, never a tracked file). Each marker is checked separately so a
    repo excluded before a marker was added picks up the new one.
    """
    exclude = common_dir / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        present = {line.strip() for line in existing.splitlines()}
        missing = [(m, c) for m, c in _EXCLUDED_DIRS if m not in present]
        if not missing:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(prefix + "".join(f"{comment}\n{marker}\n" for marker, comment in missing))
    except OSError as exc:
        logger.warning("git_worktree: could not update info/exclude: %s", exc)


def init_submodules(path: Path) -> bool:
    """Best-effort submodule init for a fresh worktree (design D6).

    ``git worktree add`` leaves submodule checkouts empty. Returns True on
    success (or no submodules), False when init failed — callers surface
    the failure to the agent prompt instead of blocking creation.
    """
    if not (path / ".gitmodules").exists():
        return True
    try:
        result = _run_git(
            ["submodule", "update", "--init", "--recursive"],
            cwd=path,
            timeout=300,
        )
    except Exception:  # noqa: BLE001
        return False
    if result.returncode != 0:
        logger.warning(
            "git_worktree: submodule init failed in %s: %s",
            path,
            result.stderr.strip(),
        )
        return False
    return True
