from pathlib import Path

from valuz_agent.infra.config import Settings


def test_log_file_path_is_independent_from_templated_data_dir() -> None:
    settings = Settings(
        data_dir=Path("~/.valuz-dev/{user_id}"),
        log_file_path=Path("~/.valuz-dev/logs/backend.log"),
    )

    assert settings.log_file_path == Path("~/.valuz-dev/logs/backend.log")
    assert "{user_id}" not in str(settings.log_file_path)


def test_log_file_path_defaults_under_data_dir(monkeypatch, tmp_path: Path) -> None:
    """Pointing VALUZ_DATA_DIR elsewhere moves the logs with it — a dev/test
    backend must not write into the packaged app's logs by omission."""
    monkeypatch.delenv("VALUZ_LOG_FILE_PATH", raising=False)
    settings = Settings(data_dir=tmp_path / "root")

    assert settings.log_file_path == tmp_path / "root" / "logs" / "backend.log"


def test_log_file_path_default_strips_user_template(monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_LOG_FILE_PATH", raising=False)
    settings = Settings(data_dir=Path("/data/valuz/{user_id}"))

    assert settings.log_file_path == Path("/data/valuz/logs/backend.log")
    assert "{user_id}" not in str(settings.log_file_path)


def test_log_file_path_reads_prefixed_env(monkeypatch, tmp_path: Path) -> None:
    expected = tmp_path / "app.log"
    monkeypatch.setenv("VALUZ_LOG_FILE_PATH", str(expected))

    assert Settings().log_file_path == expected


def test_legacy_log_env_parts_are_ignored(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VALUZ_LOG_FILE_PATH", raising=False)
    monkeypatch.setenv("VALUZ_LOG_DIR", str(tmp_path / "legacy"))
    monkeypatch.setenv("VALUZ_LOG_FILENAME", "legacy.log")

    settings = Settings(data_dir=tmp_path / "root")
    assert settings.log_file_path == tmp_path / "root" / "logs" / "backend.log"


def test_user_skill_staging_dir_env_alias(monkeypatch, tmp_path) -> None:
    staging_dir = tmp_path / "{user_id}" / "skill-staging"

    monkeypatch.setenv("VALUZ_USER_SKILL_STAGING_DIR", str(staging_dir))

    assert Settings().user_skill_staging_dir == staging_dir


def test_user_temp_dir_env_alias(monkeypatch, tmp_path) -> None:
    temp_dir = tmp_path / "{user_id}" / "tmp"

    monkeypatch.setenv("VALUZ_USER_TEMP_DIR", str(temp_dir))

    assert Settings().user_temp_dir == temp_dir
