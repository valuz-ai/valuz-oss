from __future__ import annotations

import pytest

from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService


class FakeProjectDatastore:
    def __init__(self, row: ProjectRow | None) -> None:
        self.row = row

    async def get_by_id(self, user_id: str, project_id: str) -> ProjectRow | None:
        if self.row and self.row.id == project_id:
            return self.row
        return None


def _service(root_path: str) -> ProjectService:
    row = ProjectRow(id="proj_1", name="Demo", kind="project", root_path=root_path)
    return ProjectService(datastore=FakeProjectDatastore(row), event_bus=None)  # type: ignore[arg-type]


async def test_read_markdown_file_returns_text_artifact(tmp_path) -> None:
    (tmp_path / "report.md").write_text("# Report\n\nhello", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.md")

    assert result.artifact.kind == "project_file"
    assert result.artifact.preview_kind == "markdown"
    assert result.artifact.path == "report.md"
    assert result.artifact.capabilities.can_copy_content is True
    assert result.content.kind == "text"
    assert "# Report" in result.content.content
    assert result.content.truncated is False


async def test_read_xlsx_file_returns_spreadsheet_artifact(tmp_path) -> None:
    (tmp_path / "model.xlsx").write_bytes(b"not-a-real-xlsx")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "model.xlsx")

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.artifact.capabilities.can_preview is True
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/model.xlsx"


async def test_read_large_xlsx_file_within_spreadsheet_limit_returns_raw_url(
    tmp_path,
) -> None:
    path = tmp_path / "large.xlsx"
    path.write_bytes(b"PK\x03\x04")
    with path.open("ab") as fh:
        fh.truncate(21 * 1024 * 1024)

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", path.name)

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/large.xlsx"


async def test_read_huge_xlsx_file_returns_external_artifact(tmp_path) -> None:
    path = tmp_path / "huge.xlsx"
    path.write_bytes(b"PK\x03\x04")
    with path.open("ab") as fh:
        fh.truncate(101 * 1024 * 1024)

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", path.name)

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.content.kind == "external"
    assert "parsing limit" in result.content.reason


async def test_read_csv_file_returns_spreadsheet_artifact(tmp_path) -> None:
    (tmp_path / "data.csv").write_text("ticker,value\nAAPL,1", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "data.csv")

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.artifact.capabilities.can_preview is True
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/data.csv"


async def test_read_html_file_returns_html_artifact(tmp_path) -> None:
    (tmp_path / "report.html").write_text(
        "<!doctype html><title>Report</title><h1>Report</h1>",
        encoding="utf-8",
    )

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.html")

    assert result.artifact.preview_kind == "html"
    assert result.artifact.capabilities.can_preview is True
    assert result.artifact.capabilities.can_copy_content is True
    assert result.content.kind == "text"
    assert "<h1>Report</h1>" in result.content.content


async def test_read_docx_file_returns_docx_artifact(tmp_path) -> None:
    (tmp_path / "memo.docx").write_bytes(b"PK\x03\x04 demo docx")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "memo.docx")

    assert result.artifact.preview_kind == "docx"
    assert result.artifact.capabilities.can_preview is True
    assert result.artifact.capabilities.can_copy_content is False
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/memo.docx"


async def test_read_legacy_doc_file_is_not_docx_preview(tmp_path) -> None:
    (tmp_path / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0 demo doc")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "legacy.doc")

    assert result.artifact.preview_kind == "unsupported"
    assert result.artifact.capabilities.can_preview is False
    assert result.content.kind == "external"


async def test_read_image_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "chart.png")

    assert result.artifact.preview_kind == "image"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "image/png"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/chart.png"


async def test_read_large_image_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + (b"0" * (6 * 1024 * 1024)))

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "large.png")

    assert result.artifact.preview_kind == "image"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "image/png"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/large.png"


async def test_read_media_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "clip.mp4")

    assert result.artifact.preview_kind == "media"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "video/mp4"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/clip.mp4"


async def test_read_large_media_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "large.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42" + (b"0" * (6 * 1024 * 1024))
    )

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "large.mp4")

    assert result.artifact.preview_kind == "media"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "video/mp4"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/large.mp4"


async def test_read_pdf_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%demo\n")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.pdf")

    assert result.artifact.preview_kind == "pdf"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "application/pdf"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/report.pdf"


async def test_read_large_pdf_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "large.pdf").write_bytes(b"%PDF-1.4\n" + (b"0" * (21 * 1024 * 1024)))

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "large.pdf")

    assert result.artifact.preview_kind == "pdf"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "application/pdf"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/large.pdf"


async def test_read_large_docx_file_returns_external_artifact(tmp_path) -> None:
    (tmp_path / "large.docx").write_bytes(b"PK\x03\x04" + (b"0" * (21 * 1024 * 1024)))

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "large.docx")

    assert result.artifact.preview_kind == "docx"
    assert result.content.kind == "external"
    assert "parsing limit" in result.content.reason


async def test_resolve_file_resource_returns_safe_raw_file_metadata(tmp_path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%demo\n")

    resource = await _service(str(tmp_path)).resolve_file_resource(
        "user-1",
        "proj_1",
        "report.pdf",
    )

    assert resource.path == tmp_path / "report.pdf"
    assert resource.rel_path == "report.pdf"
    assert resource.name == "report.pdf"
    assert resource.mime_type == "application/pdf"
    assert resource.size == 15


async def test_artifact_response_serializes_frontend_aliases(tmp_path) -> None:
    (tmp_path / "report.md").write_text("# Report", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.md")
    payload = result.model_dump(by_alias=True)

    assert payload["artifact"]["projectId"] == "proj_1"
    assert payload["artifact"]["previewKind"] == "markdown"
    assert payload["artifact"]["capabilities"]["canCopyContent"] is True
    assert payload["content"]["modifiedAt"]


async def test_read_file_rejects_traversal(tmp_path) -> None:
    (tmp_path / "safe.md").write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError):
        await _service(str(tmp_path)).read_file("user-1", "proj_1", "../safe.md")


async def test_read_file_rejects_hidden_files(tmp_path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    with pytest.raises(PermissionError):
        await _service(str(tmp_path)).read_file("user-1", "proj_1", ".env")


# ── write_file (multipart upload target) ────────────────────────────


async def test_write_file_writes_relative_path(tmp_path) -> None:
    rel = await _service(str(tmp_path)).write_file("user-1", "proj_1", "doc.txt", b"hello")
    assert rel == "doc.txt"
    assert (tmp_path / "doc.txt").read_bytes() == b"hello"


async def test_write_file_creates_parent_dirs(tmp_path) -> None:
    rel = await _service(str(tmp_path)).write_file("user-1", "proj_1", "sub/deep.txt", b"x")
    assert rel == "sub/deep.txt"
    assert (tmp_path / "sub" / "deep.txt").read_bytes() == b"x"


async def test_write_file_rejects_traversal(tmp_path) -> None:
    svc = _service(str(tmp_path))
    with pytest.raises(ValueError):
        await svc.write_file("user-1", "proj_1", "../escape.txt", b"x")
    with pytest.raises(ValueError):
        await svc.write_file("user-1", "proj_1", "/abs.txt", b"x")


async def test_create_project_without_root_allocates_managed_cwd(
    tmp_path, monkeypatch
) -> None:
    """create_project(root_path=None) allocates a managed cwd — the
    cloud/headless path (mirrors create_project_from_pack's managed branch)."""
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from valuz_agent.infra.database import Base
    from valuz_agent.infra.eventbus import EventBus
    from valuz_agent.modules.projects import service as project_service
    from valuz_agent.modules.projects.datastore import ProjectDatastore

    monkeypatch.setattr(project_service.fs_registry, "data_dir", lambda user_id: tmp_path)
    monkeypatch.setattr(
        project_service.fs_registry, "project_root", lambda user_id: tmp_path / "Valuz"
    )

    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=[ProjectRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sm = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    async with sm() as db:
        svc = ProjectService(datastore=ProjectDatastore(db), event_bus=EventBus())
        detail = await svc.create_project("user-1", name="Managed")
        assert detail.root_path is not None
        assert detail.cwd == str(tmp_path / "Valuz" / detail.root_path)
        assert "/" not in detail.root_path
        assert (tmp_path / "Valuz" / detail.root_path / ".valuz" / "root").is_file()
