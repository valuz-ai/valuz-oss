"""uvicorn access-log poll-noise filter.

``uvicorn.access`` prints one INFO line per request, parallel to valuz's own
structured access log. High-frequency UI polls (``/v1/runs``,
``/v1/system/status``, kernel ``/internal/mcp``) would flood the console / log
file, so ``_AccessLogPathFilter`` drops them. These tests pin which paths are
silenced and that the install is idempotent. See ``infra/logging.py``.
"""

from __future__ import annotations

import logging

import pytest

from valuz_agent.infra.logging import (
    _AccessLogPathFilter,
    _install_access_log_filter,
)


def _access_record(request_target: str) -> logging.LogRecord:
    """Build a record shaped like uvicorn's access log."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1:61113", "GET", request_target, "1.1", 200),
        exc_info=None,
    )


@pytest.mark.parametrize(
    "target",
    [
        "/v1/runs",
        "/v1/runs?status=running",  # query string ignored
        "/v1/runs/123",  # sub-path
        "/v1/system/status",
        "/internal/mcp/foo",
    ],
)
def test_silenced_paths_are_dropped(target: str) -> None:
    assert _AccessLogPathFilter().filter(_access_record(target)) is False


@pytest.mark.parametrize(
    "target",
    [
        "/v1/sessions",
        "/v1/projects",
        "/v1/runsfoo",  # prefix-only match must NOT be silenced
    ],
)
def test_other_paths_pass_through(target: str) -> None:
    assert _AccessLogPathFilter().filter(_access_record(target)) is True


def test_non_access_shaped_record_passes_through() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="something else",
        args=None,
        exc_info=None,
    )
    assert _AccessLogPathFilter().filter(record) is True


def test_install_is_idempotent() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    before = [f for f in access_logger.filters if isinstance(f, _AccessLogPathFilter)]
    for f in before:
        access_logger.removeFilter(f)

    _install_access_log_filter()
    _install_access_log_filter()

    installed = [f for f in access_logger.filters if isinstance(f, _AccessLogPathFilter)]
    assert len(installed) == 1
