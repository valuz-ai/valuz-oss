"""Tests for the local asset store — the unified host-domain storage substrate.

Object store with a file view (key -> bytes + zero-copy local fetch). The
on-disk layout maps key 1:1 to a relative path under ``base`` so a desktop
upgrade keeps reading existing data (no migration).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.infra.asset_store import LocalAssetStore


class TestLocalAssetStore:
    def test_put_get_roundtrip(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        s.put("u", "docs/1/a.txt", b"hello")
        assert s.get("u", "docs/1/a.txt") == b"hello"
        assert s.exists("u", "docs/1/a.txt")

    def test_get_missing_is_none(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        assert s.get("u", "nope") is None
        assert not s.exists("u", "nope")

    def test_delete_is_idempotent(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        s.put("u", "k", b"v")
        s.delete("u", "k")
        assert s.get("u", "k") is None
        s.delete("u", "k")  # no error second time

    def test_layout_maps_key_to_relative_path(self, tmp_path: Path) -> None:
        """key 1:1 → relative path under base (the zero-migration property)."""
        s = LocalAssetStore(tmp_path)
        s.put("u", "secrets/channel__abc", b"sk-1")
        assert (tmp_path / "secrets" / "channel__abc").read_bytes() == b"sk-1"

    def test_list_returns_keys_under_prefix(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        s.put("u", "skills/x/SKILL.md", b"a")
        s.put("u", "skills/x/run.py", b"b")
        s.put("u", "docs/y", b"c")
        assert s.list("u", "skills/x") == ["skills/x/SKILL.md", "skills/x/run.py"]

    def test_fetch_is_zero_copy_local_path(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        s.put("u", "a/b.txt", b"x")
        p = s.fetch("u", "a/b.txt")
        assert p == tmp_path / "a" / "b.txt"
        assert p is not None and p.read_bytes() == b"x"

    def test_fetch_missing_is_none(self, tmp_path: Path) -> None:
        assert LocalAssetStore(tmp_path).fetch("u", "nope") is None

    def test_fetch_group_returns_local_dir(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        s.put("u", "skills/x/SKILL.md", b"a")
        d = s.fetch_group("u", "skills/x")
        assert d == tmp_path / "skills" / "x"
        assert (d / "SKILL.md").read_bytes() == b"a"

    def test_traversal_is_rejected(self, tmp_path: Path) -> None:
        s = LocalAssetStore(tmp_path)
        with pytest.raises(ValueError):
            s.put("u", "../escape", b"x")


class TestResolveAssetPath:
    def test_relative_key_is_fetched(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from valuz_agent.infra.asset_store import resolve_asset_path
        from valuz_agent.ports.extensions import ext

        store = LocalAssetStore(tmp_path)
        store.put("u", "docs/preview/d1.md", b"# preview")
        monkeypatch.setattr(ext, "asset_store", store)
        assert resolve_asset_path("u", "docs/preview/d1.md") == str(
            tmp_path / "docs" / "preview" / "d1.md"
        )

    def test_absolute_path_used_as_is(self) -> None:
        from valuz_agent.infra.asset_store import resolve_asset_path

        # Legacy / external (kb_doc source) absolute paths pass through unchanged.
        assert resolve_asset_path("u", "/abs/legacy.md") == "/abs/legacy.md"

    def test_none_is_none(self) -> None:
        from valuz_agent.infra.asset_store import resolve_asset_path

        assert resolve_asset_path("u", None) is None
