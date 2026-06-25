"""Tests for the secret-store extension point.

Verifies that:
1. OSS default → ``ext.secret_store`` is the local ``FileSecretStore``.
2. ``deps._secret_store()`` resolves ``ext.secret_store`` per call, so a
   commercial overlay that swaps in a shared store (e.g. Postgres-backed) is
   honoured everywhere the host reads/writes secrets — including a binding that
   happens after this module was first imported (no caching strands the old
   store on a replica).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.infra.asset_store import LocalAssetStore
from valuz_agent.infra.secret_store import (
    AssetBackedSecretStore,
    FileSecretStore,
    InMemorySecretStore,
)


class _StubStore:
    """Minimal ``SecretStorePort`` impl standing in for an overlay's store."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, ref: str) -> str | None:
        return self._d.get((user_id, ref))

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._d[(user_id, ref)] = value

    def delete(self, user_id: str, ref: str) -> None:
        self._d.pop((user_id, ref), None)


class TestSecretStoreSwapPoint:
    def test_oss_default_is_asset_backed_local(self) -> None:
        """OSS default: a local asset store, with secrets built on top of it."""
        from valuz_agent.ports.extensions import ext

        assert isinstance(ext.asset_store, LocalAssetStore)
        assert isinstance(ext.secret_store, AssetBackedSecretStore)

    def test_accessor_returns_bound_extension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_secret_store()`` reflects a late overlay binding on ``ext`` — the
        seam the shared multi-replica backend uses so BYOK creds aren't stranded
        on whichever process wrote them."""
        from valuz_agent.api import deps
        from valuz_agent.ports.extensions import ext

        stub = _StubStore()
        monkeypatch.setattr(ext, "secret_store", stub)

        assert deps._secret_store() is stub


class TestOwnerScoping:
    def test_in_memory_isolates_by_user(self) -> None:
        """The owner-scoped contract: one owner's ref is invisible to another."""
        store = InMemorySecretStore()
        store.put("user-A", "channel/x", "key-A")

        assert store.get("user-A", "channel/x") == "key-A"
        assert store.get("user-B", "channel/x") is None  # no cross-owner read

    def test_file_store_is_single_user(self, tmp_path: Path) -> None:
        """The desktop file store ignores ``user_id`` (one user per install) —
        owner isolation is the shared-backend overlay store's job, not this one's."""
        store = FileSecretStore(tmp_path)
        store.put("whoever", "channel/x", "k")

        # Same flat key regardless of user_id (documented single-user behaviour).
        assert store.get("someone-else", "channel/x") == "k"
