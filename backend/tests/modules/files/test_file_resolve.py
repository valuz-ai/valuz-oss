"""Unit tests for file address resolution (URI, owner bounds, descriptor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.api.routes.files import _resolve_one
from valuz_agent.modules.files.service import assert_owned, stat_meta
from valuz_agent.modules.files.uri import build_valuz_file_uri, parse_valuz_file_uri
from valuz_agent.ports.file_address import (
    LocalFileAddressResolver,
    ResolvedAddress,
    set_file_address_resolver,
)


class TestUri:
    # Cross-layer contract: this fixture list is mirrored 1:1 in the frontend's
    # packages/shared/src/utils/file-uri.test.ts (CONTRACT_PATHS). Both codecs
    # must round-trip every path identically. Add a nasty path here → add it
    # there too. The CJK entry is the real "读取失败：HTTP 404" case.
    @pytest.mark.parametrize(
        "path",
        [
            "/data/valuz_data/workspace/u/proj/a.md",
            "/Users/u/My Proj/r.pdf",  # space
            "/tmp/name+with&chars.txt",  # + and &
            "/tmp/a#b.txt",  # fragment delimiter if not encoded
            "/tmp/a%b.txt",  # literal percent
            "/Users/river/Valuz/示例项目/晶合集成_688249_财务预测模型.xlsx",  # CJK
            "C:/Users/u/x.txt",  # windows drive
        ],
    )
    def test_roundtrip(self, path: str) -> None:
        assert parse_valuz_file_uri(build_valuz_file_uri(path)) == path

    def test_canonical_three_slash(self) -> None:
        assert build_valuz_file_uri("/a/b.md") == "valuz-file:///a/b.md"

    def test_tolerates_two_slash(self) -> None:
        # A producer that dropped the authority separator (two slashes) folds the
        # mis-parsed host back onto the path — same result as the canonical form.
        assert (
            parse_valuz_file_uri("valuz-file://Users/river/x.md")
            == parse_valuz_file_uri("valuz-file:///Users/river/x.md")
            == "/Users/river/x.md"
        )
        assert (
            parse_valuz_file_uri("valuz-file://Users/river/untitled%20folder/x.md")
            == "/Users/river/untitled folder/x.md"
        )

    @pytest.mark.parametrize("bad", ["http://x/y", "valuz-file://", "/a/b.md", ""])
    def test_rejects(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_valuz_file_uri(bad)


class TestOwnerBounds:
    def test_within(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "a.md"
        assert assert_owned(f, [tmp_path.resolve()]) == f.resolve()

    def test_escape(self, tmp_path: Path) -> None:
        with pytest.raises(PermissionError):
            assert_owned(Path("/etc/passwd"), [tmp_path.resolve()])

    def test_traversal_escape(self, tmp_path: Path) -> None:
        with pytest.raises(PermissionError):
            assert_owned(tmp_path / ".." / "elsewhere" / "a.md", [tmp_path.resolve()])

    def test_symlink_escape(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        (outside / "secret.md").write_text("x")
        link = tmp_path / "link"
        link.symlink_to(outside)
        # A symlink inside the owned root pointing outside must not grant access.
        with pytest.raises(PermissionError):
            assert_owned(link / "secret.md", [tmp_path.resolve()])


class TestStatMeta:
    def test_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "r.md"
        f.write_text("# hi")
        m = stat_meta(f)
        assert m.exists and m.size == 4 and m.preview_kind == "markdown"

    def test_missing(self, tmp_path: Path) -> None:
        m = stat_meta(tmp_path / "missing.png")
        assert not m.exists and m.size is None and m.preview_kind == "image"

    def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        d = tmp_path / "sub"
        d.mkdir()
        m = stat_meta(d)
        assert not m.exists and m.size is None

    def test_vanishing_file_degrades_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A file deleted mid-call (agent overwrite, cleanup job) must come back
        # as exists=False — raising here failed the whole resolve batch.
        f = tmp_path / "gone.md"
        f.write_text("x")

        def boom(self: Path, *a: object, **kw: object) -> None:
            raise FileNotFoundError(str(self))

        monkeypatch.setattr(Path, "stat", boom)
        m = stat_meta(f)
        assert not m.exists and m.size is None and m.preview_kind == "markdown"


class _RemoteResolver:
    async def to_address(self, *, owner_user_id: str, abs_path: Path) -> ResolvedAddress:
        return ResolvedAddress(
            kind="remote", url=f"https://cos/{abs_path.name}?sig", expires_at=999
        )


class _ForbiddenResolver:
    async def to_address(self, *, owner_user_id: str, abs_path: Path) -> ResolvedAddress:
        raise PermissionError(str(abs_path))


class TestResolveOne:
    def teardown_method(self) -> None:
        set_file_address_resolver(LocalFileAddressResolver())

    async def test_local_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "a.md"
        f.write_text("# x")
        set_file_address_resolver(LocalFileAddressResolver())
        d = await _resolve_one(build_valuz_file_uri(str(f)), "u", [tmp_path.resolve()])
        assert d.kind == "local"
        assert d.abs_path == str(f.resolve()) and d.url is None
        assert d.exists and d.error is None
        assert d.capabilities.can_preview and d.capabilities.can_open_external

    async def test_remote(self, tmp_path: Path) -> None:
        f = tmp_path / "a.pdf"
        f.write_text("x")
        set_file_address_resolver(_RemoteResolver())
        d = await _resolve_one(build_valuz_file_uri(str(f)), "u", [tmp_path.resolve()])
        assert d.kind == "remote"
        assert d.url and d.url.endswith("?sig") and d.abs_path is None
        assert d.expires_at == 999
        # open-external is local-only even though the file exists.
        assert not d.capabilities.can_open_external
        assert d.capabilities.can_download

    async def test_forbidden_path(self, tmp_path: Path) -> None:
        d = await _resolve_one("valuz-file:///etc/passwd", "u", [tmp_path.resolve()])
        assert d.kind == "" and d.error == "forbidden"
        assert d.name == "" and not d.capabilities.can_preview

    async def test_forbidden_by_resolver(self, tmp_path: Path) -> None:
        f = tmp_path / "a.md"
        f.write_text("x")
        set_file_address_resolver(_ForbiddenResolver())
        d = await _resolve_one(build_valuz_file_uri(str(f)), "u", [tmp_path.resolve()])
        assert d.error == "forbidden"

    async def test_invalid_ref(self, tmp_path: Path) -> None:
        d = await _resolve_one("http://evil/x", "u", [tmp_path.resolve()])
        assert d.error == "invalid_ref"

    async def test_not_found(self, tmp_path: Path) -> None:
        set_file_address_resolver(LocalFileAddressResolver())
        ref = build_valuz_file_uri(str(tmp_path / "missing.md"))
        d = await _resolve_one(ref, "u", [tmp_path.resolve()])
        assert d.error == "not_found" and not d.exists
        assert not d.capabilities.can_download
