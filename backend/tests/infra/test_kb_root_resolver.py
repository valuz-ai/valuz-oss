"""KB root resolver extension point (valuz-oss#839).

OSS keeps one KB root (``<data_dir>/kb``) and one KB class (``"normal"``).
A host that manages several classes of knowledge base can install a resolver
so each class lands in its own directory — without OSS growing any opinion
about what those classes mean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.infra.fs_registry import KB_KIND_DEFAULT, FsRegistry


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    reg = FsRegistry()
    monkeypatch.setattr(reg, "data_dir", lambda user_id: tmp_path / user_id)
    (tmp_path / "u1").mkdir(parents=True, exist_ok=True)
    return reg


def test_default_is_a_single_root_for_every_kind(registry, tmp_path):
    assert registry.kb_root("u1") == tmp_path / "u1" / "kb"
    # An unknown kind changes nothing until a resolver is installed.
    assert registry.kb_root("u1", "anything") == tmp_path / "u1" / "kb"


def test_resolver_routes_per_kind_and_directory_is_created(registry, tmp_path):
    def resolver(user_id: str, kind: str) -> Path:
        return tmp_path / user_id / f"kb-{kind}"

    registry.set_kb_root_resolver(resolver)

    normal = registry.kb_root("u1", KB_KIND_DEFAULT)
    other = registry.kb_root("u1", "conversation")

    assert normal == tmp_path / "u1" / "kb-normal"
    assert other == tmp_path / "u1" / "kb-conversation"
    assert normal.is_dir() and other.is_dir()
    assert normal != other


def test_resolver_may_return_a_string(registry, tmp_path):
    registry.set_kb_root_resolver(lambda user_id, kind: str(tmp_path / "flat" / kind))
    assert registry.kb_root("u1", "shared") == tmp_path / "flat" / "shared"


def test_clearing_resolver_restores_default(registry, tmp_path):
    registry.set_kb_root_resolver(lambda user_id, kind: tmp_path / "elsewhere")
    assert registry.kb_root("u1") == tmp_path / "elsewhere"

    registry.set_kb_root_resolver(None)
    assert registry.kb_root("u1") == tmp_path / "u1" / "kb"


def test_global_registry_has_no_resolver_by_default():
    from valuz_agent.infra.fs_registry import fs_registry

    assert fs_registry._kb_root_resolver is None
