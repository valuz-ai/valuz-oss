"""Unit coverage for the session-lever coercion helpers added in the
kernel V5+bba3014 upgrade — ``_coerce_session_permission_mode`` and the
new ``_coerce_session_effort``.

These helpers gate the route layer (PATCH ``/permission-mode`` and
``/effort``) and the ``create_session`` path. The route surfaces a
``ValueError`` from ``_coerce_session_effort`` as HTTP 400, so the
validation rule "exactly the 5 allowed values or ``None``" is what the
tests pin.
"""

from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.modules.sessions.mappers import (
    _coerce_session_effort,
    _coerce_session_permission_mode,
)

# ---------------------------------------------------------------------------
# _coerce_session_permission_mode
# ---------------------------------------------------------------------------


def test_coerce_permission_mode_preserves_default() -> None:
    assert _coerce_session_permission_mode("default") == "default"


def test_coerce_permission_mode_preserves_auto_review() -> None:
    assert _coerce_session_permission_mode("auto_review") == "auto_review"


def test_coerce_permission_mode_preserves_full_access() -> None:
    assert _coerce_session_permission_mode("full_access") == "full_access"


def test_coerce_permission_mode_none_defaults_to_full_access() -> None:
    """``None`` falls through to the kernel default (full_access)."""
    assert _coerce_session_permission_mode(None) == "full_access"


def test_coerce_permission_mode_unknown_defaults_to_full_access() -> None:
    """Legacy enum values (e.g. ``bypass`` from the pre-3-value era)
    coerce to ``full_access`` so an existing dev DB doesn't break."""
    assert _coerce_session_permission_mode("bypass") == "full_access"
    assert _coerce_session_permission_mode("garbage") == "full_access"


# ---------------------------------------------------------------------------
# _coerce_session_effort
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["low", "medium", "high", "xhigh", "max"],
)
def test_coerce_effort_preserves_valid_values(value: str) -> None:
    assert _coerce_session_effort(value) == value


def test_coerce_effort_none_returns_none() -> None:
    """``None`` = let the runtime pick its SDK default."""
    assert _coerce_session_effort(None) is None


def test_coerce_effort_empty_string_returns_none() -> None:
    """Empty string normalizes to ``None`` so the PATCH ``effort=""``
    wire shape behaves the same as ``effort=null``."""
    assert _coerce_session_effort("") is None


def test_coerce_effort_unknown_value_raises_value_error() -> None:
    """Unknown values 400 — they're surfaced verbatim to the user so
    operators see exactly which value was rejected and what's allowed."""
    with pytest.raises(ValueError) as exc:
        _coerce_session_effort("extreme")
    assert "extreme" in str(exc.value)


def test_coerce_effort_uppercase_is_rejected() -> None:
    """Case-sensitive — the kernel enum is lowercase. We don't auto-
    normalize because it'd hide typos from API consumers."""
    with pytest.raises(ValueError):
        _coerce_session_effort("LOW")
