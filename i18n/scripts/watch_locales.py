"""Watch i18n locale files and auto-regenerate type definitions.

Standalone watcher for backend-only development (no Vite running).
Uses polling to avoid platform-specific filesystem event dependencies.

Usage:
    cd backend && uv run python ../i18n/scripts/watch_locales.py
    # or via Makefile:
    make i18n-watch
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "Makefile").is_file() and (p / "frontend").is_dir():
            return p
        p = p.parent
    raise RuntimeError("Cannot find repo root")


def _file_mtimes(locales_dir: Path) -> dict[str, float]:
    result = {}
    for f in locales_dir.glob("*.json"):
        result[str(f)] = f.stat().st_mtime
    return result


def main() -> int:
    repo = _repo_root()
    locales_dir = repo / "i18n" / "locales"
    backend_dir = repo / "backend"
    gen_script = repo / "i18n" / "scripts" / "gen_types.py"

    if not locales_dir.is_dir():
        print(f"ERROR: locales dir not found: {locales_dir}", file=sys.stderr)
        return 1

    print(f"[i18n-watch] Watching {locales_dir} for changes...")
    print(f"[i18n-watch] Press Ctrl+C to stop.\n")

    prev_mtimes = _file_mtimes(locales_dir)

    try:
        while True:
            time.sleep(1)
            curr_mtimes = _file_mtimes(locales_dir)
            if curr_mtimes != prev_mtimes:
                changed = [
                    os.path.basename(f)
                    for f in curr_mtimes
                    if curr_mtimes.get(f) != prev_mtimes.get(f)
                ]
                print(f"[i18n-watch] Changed: {', '.join(changed)} — regenerating types...")
                result = subprocess.run(
                    [sys.executable, str(gen_script)],
                    cwd=str(backend_dir),
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines():
                        print(f"  {line}")
                else:
                    print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
                prev_mtimes = curr_mtimes
    except KeyboardInterrupt:
        print("\n[i18n-watch] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
