"""Codex runtime setup job — wheel table integrity, install flow, activation.

The download itself is never exercised against the network: ``_download_wheel``
(the single network seam) is patched with a fake that materializes a locally
crafted wheel. The wheel TABLE, however, is verified against ``backend/uv.lock``
for real — that test is the drift alarm for SDK pin bumps.
"""

from __future__ import annotations

import io
import os
import threading
import tomllib
import zipfile
from pathlib import Path

import pytest

from valuz_agent.modules.runtimes import setup_job
from valuz_agent.modules.runtimes.setup_job import (
    CODEX_BIN_OVERRIDE_ENV,
    CODEX_CLI_BIN_VERSION,
    CodexRuntimeSetupJob,
    activate_codex_override,
    codex_setup_requirement,
    installed_codex_path,
)

_UV_LOCK = Path(__file__).resolve().parents[3] / "uv.lock"


# -- wheel table ↔ uv.lock drift alarm --------------------------------------


def _locked_codex_wheels() -> tuple[str, dict[str, dict]]:
    """(version, {filename: {url, sha256, size}}) for openai-codex-cli-bin."""
    lock = tomllib.loads(_UV_LOCK.read_text(encoding="utf-8"))
    for pkg in lock["package"]:
        if pkg["name"] == "openai-codex-cli-bin":
            wheels = {}
            for wheel in pkg["wheels"]:
                filename = wheel["url"].rsplit("/", 1)[1]
                wheels[filename] = {
                    "url": wheel["url"],
                    "sha256": wheel["hash"].removeprefix("sha256:"),
                    "size": wheel["size"],
                }
            return pkg["version"], wheels
    raise AssertionError("openai-codex-cli-bin not found in uv.lock")


def test_wheel_table_matches_uv_lock() -> None:
    """The static ``_WHEELS`` table must mirror uv.lock exactly.

    Fails after an SDK pin bump (``uv lock`` moved openai-codex-cli-bin):
    update ``CODEX_CLI_BIN_VERSION`` + ``_WHEELS`` in
    ``modules/runtimes/setup_job.py`` to the new lock entries.
    """
    locked_version, locked = _locked_codex_wheels()
    assert locked_version == CODEX_CLI_BIN_VERSION, (
        f"uv.lock pins openai-codex-cli-bin {locked_version} but setup_job "
        f"declares {CODEX_CLI_BIN_VERSION} — update the _WHEELS table"
    )

    for wheel in setup_job._WHEELS:
        filename = wheel.path.rsplit("/", 1)[1]
        assert filename in locked, f"{filename} not in uv.lock"
        entry = locked[filename]
        assert f"https://files.pythonhosted.org/{wheel.path}" == entry["url"]
        assert wheel.sha256 == entry["sha256"]
        assert wheel.size_bytes == entry["size"]

    # Every desktop platform must be covered (musl deliberately excluded).
    table_tags = {w.platform_tag for w in setup_job._WHEELS}
    assert table_tags == {
        "macosx_10_9_x86_64",
        "macosx_11_0_arm64",
        "manylinux_2_17_aarch64",
        "manylinux_2_17_x86_64",
        "win_amd64",
        "win_arm64",
    }


def test_wheel_selection_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        ("darwin", "arm64", "macosx_11_0_arm64"),
        ("darwin", "x86_64", "macosx_10_9_x86_64"),
        ("linux", "aarch64", "manylinux_2_17_aarch64"),
        ("linux", "x86_64", "manylinux_2_17_x86_64"),
    ]
    for sys_platform, machine, expected_tag in cases:
        monkeypatch.setattr(setup_job.sys, "platform", sys_platform)
        monkeypatch.setattr(setup_job.platform, "machine", lambda m=machine: m)
        assert setup_job._wheel_for_current_platform().platform_tag == expected_tag


def test_requirement_carries_download_metadata() -> None:
    req = codex_setup_requirement()
    assert req.id == "codex_runtime"
    assert req.kind == "binary_download"
    assert req.license_name == "Apache-2.0"
    # size is the network transfer (current platform's wheel), not the
    # extracted ~217MB binary
    assert req.size_bytes is not None and 50_000_000 < req.size_bytes < 120_000_000


# -- install flow ------------------------------------------------------------


class _FakeFs:
    def __init__(self, root: Path) -> None:
        self.root = root

    def runtime_bin_dir(self, runtime_id: str, version: str) -> Path:
        path = self.root / "runtimes" / runtime_id / version
        path.mkdir(parents=True, exist_ok=True)
        return path


_FAKE_BINARY = b"#!/bin/sh\necho fake-codex\n"


def _make_fake_wheel() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("codex_cli_bin/bin/codex", _FAKE_BINARY)
        zf.writestr("codex_cli_bin/__init__.py", "")
    return buf.getvalue()


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeFs:
    fs = _FakeFs(tmp_path)
    monkeypatch.setattr(setup_job, "fs_registry", fs)
    monkeypatch.delenv(CODEX_BIN_OVERRIDE_ENV, raising=False)

    wheel_bytes = _make_fake_wheel()

    def fake_download(*, wheel, dest, cancel_event, progress_cb):  # type: ignore[no-untyped-def]
        dest.write_bytes(wheel_bytes)
        progress_cb(len(wheel_bytes), len(wheel_bytes))
        return len(wheel_bytes)

    monkeypatch.setattr(setup_job, "_download_wheel", fake_download)
    return fs


def test_run_installs_and_activates(fake_env: _FakeFs) -> None:
    job = CodexRuntimeSetupJob()
    assert job.is_complete() is False

    progress: list[tuple[int, int | None]] = []
    job.run(progress_cb=lambda d, t: progress.append((d, t)), cancel_event=threading.Event())

    binary = installed_codex_path()
    assert binary is not None
    assert binary.read_bytes() == _FAKE_BINARY
    if os.name != "nt":
        assert binary.stat().st_mode & 0o111  # executable
    assert job.is_complete() is True
    # activation happened as part of the successful run
    assert os.environ[CODEX_BIN_OVERRIDE_ENV] == str(binary)
    # the downloaded wheel does not linger
    assert not list(binary.parent.glob("*.partial"))
    assert not (binary.parent / "wheel.zip.partial").exists()
    assert (binary.parent / "NOTICE").exists()
    assert progress and progress[0] != progress[-1]


def test_run_prunes_stale_version_dirs(fake_env: _FakeFs) -> None:
    stale = fake_env.runtime_bin_dir("codex", "0.0.0-old")
    (stale / "codex").write_bytes(b"old")

    CodexRuntimeSetupJob().run(progress_cb=lambda d, t: None, cancel_event=threading.Event())

    assert not stale.exists()
    assert installed_codex_path() is not None


def test_cancelled_run_does_not_look_installed(
    fake_env: _FakeFs, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel_event = threading.Event()

    def cancelling_download(*, wheel, dest, cancel_event, progress_cb):  # type: ignore[no-untyped-def]
        dest.write_bytes(b"partial garbage")
        cancel_event.set()
        return 15

    monkeypatch.setattr(setup_job, "_download_wheel", cancelling_download)
    job = CodexRuntimeSetupJob()
    job.run(progress_cb=lambda d, t: None, cancel_event=cancel_event)

    assert job.is_complete() is False
    assert installed_codex_path() is None
    assert not list(job.install_dir().glob("*.partial"))
    assert CODEX_BIN_OVERRIDE_ENV not in os.environ


def test_version_mismatched_install_reads_as_not_installed(fake_env: _FakeFs) -> None:
    job = CodexRuntimeSetupJob()
    job.run(progress_cb=lambda d, t: None, cancel_event=threading.Event())
    marker = job.install_dir() / "READY"
    marker.write_text("timestamp=x\nversion=0.0.0-other\n", encoding="utf-8")

    assert installed_codex_path() is None
    assert job.is_complete() is False


def test_extract_rejects_wheel_without_binary(fake_env: _FakeFs, tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.whl"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("codex_cli_bin/__init__.py", "")
    bogus.write_bytes(buf.getvalue())

    with pytest.raises(RuntimeError, match="layout changed"):
        setup_job._extract_codex_binary(bogus, tmp_path)


# -- source fallback ---------------------------------------------------------


def test_download_falls_through_sources_on_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts: list[str] = []

    def fake_stream(*, url, dest, expected_sha256, total_bytes, cancel_event, progress_cb):  # type: ignore[no-untyped-def]
        attempts.append(url)
        if len(attempts) == 1:
            raise setup_job._DigestMismatchError(f"sha256 mismatch for {url}")
        dest.write_bytes(b"ok")
        return 2

    monkeypatch.setattr(setup_job, "_stream_one", fake_stream)
    wheel = setup_job._WHEELS[0]
    got = setup_job._download_wheel(
        wheel=wheel,
        dest=tmp_path / "w.whl.partial",
        cancel_event=threading.Event(),
        progress_cb=lambda d, t: None,
    )

    assert got == 2
    assert len(attempts) == 2
    assert attempts[0].startswith("https://files.pythonhosted.org/")
    assert attempts[1].startswith("https://pypi.tuna.tsinghua.edu.cn/")


def test_download_raises_when_all_sources_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def always_mismatch(*, url, dest, expected_sha256, total_bytes, cancel_event, progress_cb):  # type: ignore[no-untyped-def]
        raise setup_job._DigestMismatchError(f"sha256 mismatch for {url}")

    monkeypatch.setattr(setup_job, "_stream_one", always_mismatch)
    with pytest.raises(setup_job._DigestMismatchError):
        setup_job._download_wheel(
            wheel=setup_job._WHEELS[0],
            dest=tmp_path / "w.whl.partial",
            cancel_event=threading.Event(),
            progress_cb=lambda d, t: None,
        )


# -- activation semantics ----------------------------------------------------


def test_activate_respects_existing_user_override(
    fake_env: _FakeFs, monkeypatch: pytest.MonkeyPatch
) -> None:
    CodexRuntimeSetupJob().run(progress_cb=lambda d, t: None, cancel_event=threading.Event())
    monkeypatch.setenv(CODEX_BIN_OVERRIDE_ENV, "/opt/my-own-codex")

    assert activate_codex_override() is False
    assert os.environ[CODEX_BIN_OVERRIDE_ENV] == "/opt/my-own-codex"


def test_activate_noop_without_install(fake_env: _FakeFs) -> None:
    assert activate_codex_override() is False
    assert CODEX_BIN_OVERRIDE_ENV not in os.environ
