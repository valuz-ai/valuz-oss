"""Per user x plugin storage and config (``valuz_extension_*``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from tests.modules.third_party.helpers import build_plugin
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.third_party import service as service_mod
from valuz_agent.modules.third_party.errors import (
    InvalidConfig,
    InvalidStorageKey,
    PluginNotFound,
    StorageKeyNotFound,
    StorageQuotaExceeded,
)
from valuz_agent.modules.third_party.models import ExtensionConfigRow, ExtensionStorageRow
from valuz_agent.modules.third_party.service import ThirdPartyService

pytestmark = pytest.mark.usefixtures("db")

U1, U2 = "user-1", "user-2"
PID = "acme.dashboard"
SCHEMA = {
    "type": "object",
    "properties": {
        "region": {"type": "string", "enum": ["cn", "hk"], "default": "cn"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["region"],
    "additionalProperties": False,
}


async def installed(svc: ThirdPartyService, tmp_path: Path, **over: Any) -> None:
    await svc.install(U1, {"source_path": str(build_plugin(tmp_path / "src", **over))})


# ---- storage ---------------------------------------------------------------------------


async def test_storage_round_trip_and_per_user_isolation(
    svc: ThirdPartyService, tmp_path: Path
) -> None:
    await installed(svc, tmp_path)
    assert await svc.storage_put(U1, PID, "a/one", {"n": 1}) == {"key": "a/one", "size": 7}
    await svc.storage_put(U1, PID, "a/two", [1, 2])
    await svc.storage_put(U1, PID, "b", "é")
    await svc.storage_put(U2, PID, "a/one", "other user")

    assert await svc.storage_get(U1, PID, "a/one") == {"key": "a/one", "value": {"n": 1}}
    assert (await svc.storage_get(U2, PID, "a/one"))["value"] == "other user"
    listing = await svc.storage_list(U1, PID)
    assert [i["key"] for i in listing["items"]] == ["a/one", "a/two", "b"]
    assert listing["items"][2]["size"] == len('"é"'.encode())
    assert all(isinstance(i["updated_at"], int) for i in listing["items"])
    assert [i["key"] for i in (await svc.storage_list(U1, PID, "a/"))["items"]] == [
        "a/one",
        "a/two",
    ]
    assert (await svc.storage_list(U1, PID, "%"))["items"] == []  # LIKE wildcards are literal

    await svc.storage_put(U1, PID, "a/one", {"n": 2})  # overwrite
    assert (await svc.storage_get(U1, PID, "a/one"))["value"] == {"n": 2}
    await svc.storage_delete(U1, PID, "a/one")
    await svc.storage_delete(U1, PID, "a/one")  # deleting twice is fine
    with pytest.raises(StorageKeyNotFound):
        await svc.storage_get(U1, PID, "a/one")
    assert (await svc.storage_get(U2, PID, "a/one"))["value"] == "other user"


async def test_storage_limits(svc: ThirdPartyService, tmp_path: Path) -> None:
    await installed(svc, tmp_path)
    for key in ("", "k" * 201, "bad\x00key"):
        with pytest.raises(InvalidStorageKey):
            await svc.storage_put(U1, PID, key, 1)
    await svc.storage_put(U1, PID, "k" * 200, 1)

    with pytest.raises(StorageQuotaExceeded) as raised:
        await svc.storage_put(U1, PID, "big", "x" * (256 * 1024))
    assert raised.value.status_code == 413 and raised.value.code == "storage_quota_exceeded"
    await svc.storage_put(U1, PID, "ok", "x" * (256 * 1024 - 3))  # exactly at the limit

    chunk = "y" * (250 * 1024)
    written = 0
    with pytest.raises(StorageQuotaExceeded):
        for i in range(60):
            await svc.storage_put(U1, PID, f"chunk-{i}", chunk)
            written += 1
    assert 38 <= written <= 42  # ~10 MiB in 250 KiB values
    # rewriting an existing key does not count twice; other users have their own quota
    await svc.storage_put(U1, PID, "chunk-0", "small")
    await svc.storage_put(U2, PID, "chunk-0", chunk)


async def test_storage_needs_an_installed_plugin(svc: ThirdPartyService) -> None:
    with pytest.raises(PluginNotFound):
        await svc.storage_put(U1, "nope.nope", "k", 1)
    with pytest.raises(PluginNotFound):
        await svc.storage_list(U1, "nope.nope")


# ---- config ----------------------------------------------------------------------------


async def test_config_defaults_validation_and_generation(
    svc: ThirdPartyService, tmp_path: Path
) -> None:
    await installed(svc, tmp_path, config=SCHEMA)
    initial = await svc.get_config(U1, PID)
    assert initial == {"values": {"region": "cn"}, "schema": SCHEMA}

    before = svc._store.generation()
    saved = await svc.put_config(U1, PID, {"region": "hk", "limit": 5})
    assert saved == {"values": {"region": "hk", "limit": 5}, "schema": SCHEMA}
    assert svc._store.generation() == before + 1
    assert (await svc.get_config(U1, PID))["values"] == {"region": "hk", "limit": 5}
    assert (await svc.get_config(U2, PID))["values"] == {"region": "cn"}  # per user

    for bad in (
        {"region": "us"},
        {"limit": 3},
        {"region": "cn", "limit": 0},
        {"region": "cn", "x": 1},
    ):
        with pytest.raises(InvalidConfig) as raised:
            await svc.put_config(U1, PID, bad)
        assert raised.value.status_code == 400 and raised.value.code == "invalid_config"
        assert raised.value.errors
    assert (await svc.get_config(U1, PID))["values"] == {"region": "hk", "limit": 5}
    assert svc._store.generation() == before + 1  # a refused write changes nothing


async def test_a_plugin_without_a_config_schema(svc: ThirdPartyService, tmp_path: Path) -> None:
    await installed(svc, tmp_path)
    assert await svc.get_config(U1, PID) == {"values": {}, "schema": None}
    assert (await svc.put_config(U1, PID, {}))["values"] == {}
    with pytest.raises(InvalidConfig):
        await svc.put_config(U1, PID, {"a": 1})
    with pytest.raises(PluginNotFound):
        await svc.get_config(U1, "nope.nope")


async def test_purge_removes_storage_config_and_the_data_directory(
    svc: ThirdPartyService, tmp_path: Path, data_root: Path
) -> None:
    from valuz_agent.infra.fs_registry import fs_registry

    await installed(svc, tmp_path, config=SCHEMA)
    await svc.storage_put(U1, PID, "k", 1)
    await svc.storage_put(U2, PID, "k", 2)
    await svc.put_config(U1, PID, {"region": "hk"})
    (fs_registry.extensions_data_dir(PID) / "f.txt").write_text("x")

    async def counts() -> tuple[int, int]:
        async with async_unit_of_work(commit=False) as db:
            rows = (
                await db.execute(select(func.count()).select_from(ExtensionStorageRow))
            ).scalar_one()
            conf = (
                await db.execute(select(func.count()).select_from(ExtensionConfigRow))
            ).scalar_one()
        return rows, conf

    assert await counts() == (2, 1)
    # a plain uninstall keeps everything
    await svc.uninstall(U1, PID)
    assert await counts() == (2, 1)
    assert (data_root / "extensions-data" / PID / "f.txt").is_file()
    # ... and a reinstall sees the old data
    await installed(svc, tmp_path, config=SCHEMA)
    assert (await svc.storage_get(U1, PID, "k"))["value"] == 1
    await svc.uninstall(U1, PID, purge_data=True)
    assert await counts() == (0, 0)
    assert not (data_root / "extensions-data" / PID).exists()


def test_module_constants_match_the_contract() -> None:
    assert service_mod.MAX_STORAGE_KEY == 200
    assert service_mod.MAX_STORAGE_VALUE_BYTES == 256 * 1024
    assert service_mod.MAX_STORAGE_TOTAL_BYTES == 10 * 1024 * 1024
