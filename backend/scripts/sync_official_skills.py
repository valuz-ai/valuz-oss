"""Refresh vendored official skills from upstream.

Pulls Anthropic's skill-creator (and any other configured skills) from
https://github.com/anthropics/skills into
backend/valuz_agent/resources/official_skills/, preserving directory layout.

Run manually when upstream changes need to ship with the next release:

    cd backend && uv run python scripts/sync_official_skills.py
"""

from __future__ import annotations

import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_REPO = "anthropics/skills"
GITHUB_BRANCH = "main"
TREES_API = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"

# Each entry: (upstream_subpath_under_skills/, local_dirname_under_resources/official_skills/)
BUNDLED_SKILLS: list[tuple[str, str]] = [
    ("skill-creator", "skill-creator"),
]


@dataclass
class FileEntry:
    upstream_path: str  # e.g. "skills/skill-creator/SKILL.md"
    local_relpath: str  # e.g. "skill-creator/SKILL.md"


def _fetch_json(url: str) -> dict:
    import json

    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _list_tree() -> list[FileEntry]:
    data = _fetch_json(TREES_API)
    if data.get("truncated"):
        print(
            "WARNING: GitHub tree API truncated; fetched listing may be incomplete.",
            file=sys.stderr,
        )
    entries: list[FileEntry] = []
    for item in data["tree"]:
        if item["type"] != "blob":
            continue
        path: str = item["path"]
        for upstream_sub, local_dir in BUNDLED_SKILLS:
            prefix = f"skills/{upstream_sub}/"
            if path.startswith(prefix):
                rel = path[len(prefix) :]
                entries.append(FileEntry(upstream_path=path, local_relpath=f"{local_dir}/{rel}"))
    return entries


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    dest_root = repo_root / "valuz_agent" / "resources" / "official_skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    # Ensure package marker exists.
    (dest_root / "__init__.py").touch(exist_ok=True)
    (dest_root.parent / "__init__.py").touch(exist_ok=True)

    print(f"→ Listing upstream tree from {GITHUB_REPO}@{GITHUB_BRANCH}…")
    entries = _list_tree()
    if not entries:
        print("No matching files found upstream — aborting.", file=sys.stderr)
        return 1

    # Wipe each target skill dir, then re-fetch, so removed-upstream files don't linger.
    for _, local_dir in BUNDLED_SKILLS:
        target = dest_root / local_dir
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

    written = 0
    for entry in entries:
        url = f"{RAW_BASE}/{entry.upstream_path}"
        try:
            content = _fetch_bytes(url)
        except urllib.error.HTTPError as exc:
            print(f"  ! {entry.upstream_path}: {exc}", file=sys.stderr)
            return 2

        out_path = dest_root / entry.local_relpath
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
        written += 1
        print(f"  ✓ {entry.local_relpath} ({len(content):,} bytes)")

    print(f"\nDone. {written} files written under {dest_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
