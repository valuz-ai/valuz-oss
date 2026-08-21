"""Rotating the DataService credential without restarting the kernel.

The bearer is a short-lived JWT; when it expires the sandbox's dual-write to
the host 401s and, because the local sqlite is the runtime authority, nothing
surfaces to the user while the durable mirror silently stops. Restarting to
pick up a new one takes the in-flight turn and its background tasks with it,
so the credential has to be replaceable in place.

Two halves, pinned here:

1. the store's ``access_token`` hook resolves per REQUEST, so swapping the
   module-level value changes the next call's bearer with no rebuild;
2. ``/internal/credentials/refresh`` re-reads the config file and applies
   **only** the rotatable keys — re-applying the whole file would hand the
   process a fresh ``os.environ`` while every component still holds what it
   captured at startup, which is a half-applied config, not a refresh.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import os
from pathlib import Path

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*

from app import credential_control, dependencies
from app.config import AppConfig


@pytest.fixture(autouse=True)
def _isolate_globals(monkeypatch: pytest.MonkeyPatch):
    """The credential is module-level by design — never leak it between tests."""
    monkeypatch.setattr(dependencies, "_data_api_token", "", raising=False)
    yield
    dependencies.set_data_api_token("")


def _remote_config(**over: object) -> AppConfig:
    return AppConfig(
        kernel_store="remote",
        data_api_url="http://data.invalid",
        data_api_token="TOKEN-OLD",
        **over,  # type: ignore[arg-type]
    )


# ── the hook resolves per call ───────────────────────────────────────────────


async def test_store_hook_returns_the_rotated_token_without_a_rebuild() -> None:
    """The whole mechanism: ONE store object, a new bearer on the next call.

    ``RemoteStoreHttp`` asks the hook on every request (``_bearer``), so a
    rotation is a pointer swap — in-flight requests keep the token they already
    read, the next one picks up the new value.
    """
    store = dependencies._build_durable_store(_remote_config())
    assert store is not None
    before = await store._bearer()  # the hook, exactly as _request calls it

    dependencies.set_data_api_token("TOKEN-NEW")

    assert (before, await store._bearer()) == ("TOKEN-OLD", "TOKEN-NEW")


async def test_building_the_store_seeds_the_credential_from_config() -> None:
    """Startup path: the frozen AppConfig snapshot still supplies the first one."""
    dependencies._build_durable_store(_remote_config())
    assert dependencies._data_api_token == "TOKEN-OLD"


# ── the refresh endpoint ─────────────────────────────────────────────────────


def _write_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    path = tmp_path / "env"
    path.write_text(body)
    monkeypatch.setenv("KERNEL_CONFIG_FILE", str(path))
    return path


async def test_refresh_rotates_the_live_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = dependencies._build_durable_store(_remote_config())
    assert store is not None
    _write_env(tmp_path, monkeypatch, "VALUZ_DATA_API_TOKEN=TOKEN-NEW\n")

    result = await credential_control.refresh()

    assert result == {"applied": ["VALUZ_DATA_API_TOKEN"], "rotated": True}
    assert await store._bearer() == "TOKEN-NEW"


async def test_refresh_ignores_every_key_outside_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety property. A blanket ``os.environ.update`` would leave the
    process with a fresh environ and stale captured config everywhere else."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///original.db")
    _write_env(
        tmp_path,
        monkeypatch,
        "VALUZ_DATA_API_TOKEN=TOKEN-NEW\nDATABASE_URL=sqlite+aiosqlite:///hijacked.db\n",
    )

    result = await credential_control.refresh()

    assert result["applied"] == ["VALUZ_DATA_API_TOKEN"]
    assert os.environ["DATABASE_URL"] == "sqlite+aiosqlite:///original.db"


async def test_refresh_reports_an_unchanged_credential_as_not_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host retries; a no-op refresh must be distinguishable from a real one."""
    monkeypatch.setenv("VALUZ_DATA_API_TOKEN", "TOKEN-SAME")
    _write_env(tmp_path, monkeypatch, "VALUZ_DATA_API_TOKEN=TOKEN-SAME\n")

    assert (await credential_control.refresh())["rotated"] is False


async def test_refresh_404s_when_the_file_carries_no_rotatable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    _write_env(tmp_path, monkeypatch, "SOMETHING_ELSE=1\n")

    with pytest.raises(HTTPException) as caught:
        await credential_control.refresh()
    assert caught.value.status_code == 404


async def test_refresh_404s_when_the_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file is the same answer as an empty one — never a 500 on a
    path the host retries."""
    from fastapi import HTTPException

    monkeypatch.setenv("KERNEL_CONFIG_FILE", str(tmp_path / "absent"))

    with pytest.raises(HTTPException) as caught:
        await credential_control.refresh()
    assert caught.value.status_code == 404


# ── mounting ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("wait", "mounted"),
    [("1", True), ("0", False), (None, False)],
)
def test_mounted_only_when_a_host_manages_this_kernels_env(
    wait: str | None, mounted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A standalone kernel owns its own env, so it must never expose this."""
    if wait is None:
        monkeypatch.delenv("KERNEL_CONFIG_WAIT", raising=False)
    else:
        monkeypatch.setenv("KERNEL_CONFIG_WAIT", wait)
    assert credential_control.should_mount() is mounted
