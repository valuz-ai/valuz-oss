"""The server engine's pool bounds are chosen here, not inherited.

Unset, SQLAlchemy supplies 5 + 10 with a 30-second wait. That is a library
default rather than a decision about this deployment, and it is invisible until
a concurrent burst reaches it — at which point every caller stalls the full
half-minute and then fails with a message about QueuePool.
"""

from __future__ import annotations

from valuz_agent.infra.config import settings
from valuz_agent.infra.database import pool_kwargs


def test_should_leave_sqlite_on_the_library_defaults() -> None:
    # One process against a local file; its contention is handled by
    # busy_timeout, not by pool sizing.
    assert pool_kwargs(sqlite=True) == {}


def test_should_state_every_pool_bound_for_a_server_engine() -> None:
    kwargs = pool_kwargs(sqlite=False)

    assert kwargs["pool_size"] == settings.db_pool_size
    assert kwargs["max_overflow"] == settings.db_max_overflow
    assert kwargs["pool_timeout"] == settings.db_pool_timeout_seconds


def test_should_keep_validating_and_recycling_server_connections() -> None:
    # The pre-existing reason this dict exists: idle-timeout middleboxes.
    kwargs = pool_kwargs(sqlite=False)

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800


def test_should_not_make_a_saturated_pool_stall_for_half_a_minute() -> None:
    # A saturated pool is a condition to report and retry. 30s is the library
    # default this exists to replace.
    assert settings.db_pool_timeout_seconds <= 15.0
