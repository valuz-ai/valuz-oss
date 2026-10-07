"""Safe unpack, deterministic pack, download limits."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from tests.modules.app_plugins.helpers import build_plugin, zip_dir
from valuz_agent.modules.app_plugins import archive
from valuz_agent.modules.app_plugins.errors import InvalidManifest, InvalidSource, SourceTooLarge


def _zip(path: Path, members: dict[str, bytes], *, attr: dict[str, int] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (attr or {}).get(name, 0o644 << 16)
            z.writestr(info, data)
    return path


def test_extract_a_plain_package(tmp_path: Path) -> None:
    src = build_plugin(tmp_path / "src")
    root = archive.extract_zip(zip_dir(src, tmp_path / "p.zip"), tmp_path / "out")
    assert (root / "valuz-plugin.json").is_file()
    assert (root / "frontend" / "index.js").is_file()


def test_a_single_wrapping_directory_is_tolerated(tmp_path: Path) -> None:
    src = build_plugin(tmp_path / "src")
    root = archive.extract_zip(zip_dir(src, tmp_path / "p.zip", wrapper="acme"), tmp_path / "out")
    assert root.name == "acme"
    assert (root / "valuz-plugin.json").is_file()


@pytest.mark.parametrize("name", ["/etc/passwd", "../evil.txt", "a/../../evil.txt", "C:/evil.txt"])
def test_unsafe_paths_are_refused(tmp_path: Path, name: str) -> None:
    bad = _zip(tmp_path / "bad.zip", {name: b"x", "valuz-plugin.json": b"{}"})
    with pytest.raises(InvalidSource):
        archive.extract_zip(bad, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_symlinks_are_refused(tmp_path: Path) -> None:
    link = _zip(
        tmp_path / "bad.zip",
        {"valuz-plugin.json": b"{}", "link": b"/etc/passwd"},
        attr={"link": (stat.S_IFLNK | 0o777) << 16},
    )
    with pytest.raises(InvalidSource, match="symbolic"):
        archive.extract_zip(link, tmp_path / "out")


def test_too_many_files_and_too_much_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    many = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(5)})
    monkeypatch.setattr(archive, "MAX_FILES", 4)
    with pytest.raises(InvalidSource, match="more than"):
        archive.extract_zip(many, tmp_path / "o1")
    monkeypatch.setattr(archive, "MAX_FILES", 2000)
    monkeypatch.setattr(archive, "MAX_UNCOMPRESSED_BYTES", 10)
    big = _zip(tmp_path / "big.zip", {"a.bin": b"x" * 64})
    with pytest.raises(SourceTooLarge):
        archive.extract_zip(big, tmp_path / "o2")


def test_not_a_zip(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(InvalidSource, match="not a readable zip"):
        archive.extract_zip(junk, tmp_path / "out")


def test_pack_is_deterministic_and_filters_the_file_set(tmp_path: Path) -> None:
    src = build_plugin(
        tmp_path / "src",
        icon="icon.svg",
        files={
            "icon.svg": "<svg/>",
            "LICENSE": "MIT",
            "LICENSE.txt": "MIT",
            "license": "lower case is not a LICENSE file",
            "frontend/src/app.tsx": "src",
            "frontend/.env": "SECRET=1",
            "frontend/.cache/x.js": "hidden dir",
            "node_modules/x/index.js": "dep",
            ".git/config": "git",
            "automations/a.py": "def run(ctx): pass",
            "automations/lib/src/b.py": "skipped like any src dir",
            "automations/lib/c.py": "kept",
            "notes.txt": "not packed",
            "dist/old.zip": "not packed",
            ".DS_Store": "junk",
        },
    )
    (src / "automations" / "a.py").chmod(0o755)
    manifest = json.loads((src / "valuz-plugin.json").read_text())
    names: list[str] = []
    sha1, size1 = archive.pack_directory(src, tmp_path / "a" / "p.zip", manifest, files=names)
    sha2, _ = archive.pack_directory(src, tmp_path / "b" / "p.zip", manifest)
    assert sha1 == sha2 and size1 > 0
    packed = zipfile.ZipFile(tmp_path / "a" / "p.zip")
    assert packed.namelist() == sorted(packed.namelist()) == names
    assert names == [
        "LICENSE",
        "LICENSE.txt",
        "README.md",
        "automations/a.py",
        "automations/lib/c.py",
        "frontend/index.css",
        "frontend/index.js",
        "icon.svg",
        "locales/en-US.json",
        "locales/zh-CN.json",
        "valuz-plugin.json",
    ]
    for info in packed.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.external_attr >> 16 == stat.S_IFREG | 0o644  # no exec bit, ever
        assert not info.is_dir()


def test_the_output_zip_is_never_packed(tmp_path: Path) -> None:
    src = build_plugin(tmp_path / "src")
    manifest = json.loads((src / "valuz-plugin.json").read_text())
    out = src / "frontend" / "out.zip"
    archive.pack_directory(src, out, manifest)
    assert "frontend/out.zip" not in zipfile.ZipFile(out).namelist()


def test_symlinks_in_a_pack_source(tmp_path: Path) -> None:
    src = build_plugin(tmp_path / "src")
    manifest = json.loads((src / "valuz-plugin.json").read_text())
    (src / "frontend" / "alias.js").symlink_to(src / "frontend" / "index.js")
    names = [n for n, _ in archive.collect_pack_files(src, manifest)]
    assert "frontend/alias.js" in names  # a file symlink that stays inside is followed

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (src / "frontend" / "leak.txt").symlink_to(outside)
    with pytest.raises(InvalidManifest, match="outside the plugin directory"):
        archive.collect_pack_files(src, manifest)
    (src / "frontend" / "leak.txt").unlink()

    (src / "frontend" / "dirlink").symlink_to(src / "locales")
    with pytest.raises(InvalidManifest, match="symlinked directories"):
        archive.collect_pack_files(src, manifest)


async def test_download_is_capped_and_https_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing.zip":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 100)

    monkeypatch.setattr(
        archive,
        "make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await archive.download("https://example.com/p.zip", tmp_path / "p.zip") == 100
    with pytest.raises(SourceTooLarge):
        await archive.download("https://example.com/p.zip", tmp_path / "q.zip", max_bytes=50)
    with pytest.raises(InvalidSource, match="HTTP 404"):
        await archive.download("https://example.com/missing.zip", tmp_path / "r.zip")
    with pytest.raises(InvalidSource, match="https"):
        await archive.download("http://example.com/p.zip", tmp_path / "s.zip")
    assert await archive.download("http://127.0.0.1/p.zip", tmp_path / "t.zip") == 100
