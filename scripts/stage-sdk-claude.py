#!/usr/bin/env python3
"""Restore the selected environment's original SDK CLI after Linux freezing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path


def sha256(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed_cli() -> tuple[str, Path]:
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or spec.origin is None:
        raise RuntimeError("claude-agent-sdk is missing from the selected uv project")
    version = importlib.metadata.version("claude-agent-sdk")
    source = Path(spec.origin).parent / "_bundled" / "claude"
    if not source.is_file():
        raise RuntimeError(
            f"selected SDK {version} has no native bundled CLI: {source}"
        )
    return version, source


def startup_version(executable: Path, timeout: float) -> str:
    process = subprocess.Popen(
        [str(executable), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate(timeout=2)
        raise RuntimeError(
            f"SDK native CLI --version timed out after {timeout:g}s"
        ) from None
    if process.returncode != 0:
        raise RuntimeError(
            f"SDK native CLI --version failed (exit {process.returncode})"
        )
    version = stdout.strip()
    if not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?(?: \(Claude Code\))?", version
    ):
        raise RuntimeError("SDK native CLI --version returned an invalid version")
    return version


def stage(destination: Path, *, timeout: float) -> None:
    sdk_version, source = installed_cli()
    if not destination.is_file():
        raise RuntimeError(f"PyInstaller-staged SDK CLI is missing: {destination}")
    if destination.resolve() == source.resolve():
        raise RuntimeError("SDK source and staged destination must be distinct")
    original_hash = sha256(source)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".sdk-claude-", dir=destination.parent
    )
    os.close(descriptor)
    candidate = Path(temporary)
    try:
        shutil.copyfile(source, candidate)
        shutil.copymode(source, candidate)
        candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
        if sha256(candidate) != original_hash:
            raise RuntimeError(
                "SDK source changed during staging; original bytes were not preserved"
            )
        os.replace(candidate, destination)
    finally:
        candidate.unlink(missing_ok=True)
    if sha256(destination) != original_hash:
        raise RuntimeError("staged SDK CLI hash differs from the installed original")
    cli_version = startup_version(destination, timeout)
    if sha256(destination) != original_hash or sha256(source) != original_hash:
        raise RuntimeError(
            "SDK original/staged CLI bytes changed during the startup probe"
        )
    print(f"SDK {sdk_version} original native CLI restored: {cli_version}")
    print(f"source: {source}")
    print(f"staged: {destination}")
    print(f"sha256 source=staged: {original_hash}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 30:
        parser.error("--timeout-seconds must be greater than zero and at most 30")
    try:
        stage(args.destination.absolute(), timeout=args.timeout_seconds)
    except (OSError, RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
