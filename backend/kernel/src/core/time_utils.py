"""Time helper — instants are Unix epoch milliseconds (UTC).

Every instant in the harness is a single integer: Unix epoch milliseconds in
UTC. There is no timezone to misinterpret, it is the same ``int`` type across
the domain layer, the database (plain ``BIGINT``), and the API wire, and the
frontend formats it for the viewer's timezone via ``new Date(ms)``.
"""

from __future__ import annotations

import time


def now_ms() -> int:
    """Current instant as Unix epoch milliseconds (UTC)."""
    return time.time_ns() // 1_000_000
