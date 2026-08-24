"""Execution-token registry + interpreter probe unit tests."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.ptc import interpreter
from src.ptc.execution_registry import (
    get_execution,
    register_execution,
    reset_registry_for_tests,
    revoke_execution,
    take_sub_call_slot,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_token_lifecycle():
    record = register_execution(
        session_id="s1", user_id="u1", cwd="/tmp/x", servers={}, max_sub_calls=2
    )
    assert len(record.token) > 30
    assert get_execution(record.token) is record
    assert revoke_execution(record.token) is record
    assert get_execution(record.token) is None
    assert revoke_execution(record.token) is None  # idempotent


def test_sub_call_budget_is_enforced():
    record = register_execution(
        session_id="s1", user_id="u1", cwd="/tmp/x", servers={}, max_sub_calls=2
    )
    assert take_sub_call_slot(record) is True
    assert take_sub_call_slot(record) is True
    assert take_sub_call_slot(record) is False
    assert record.sub_calls == 2


def test_tokens_are_unique_per_execution():
    a = register_execution(session_id="s", user_id="u", cwd="/", servers={})
    b = register_execution(session_id="s", user_id="u", cwd="/", servers={})
    assert a.token != b.token


# -- interpreter probe ------------------------------------------------------


def test_probe_missing_python3(monkeypatch):
    interpreter.reset_probe_cache_for_tests()
    monkeypatch.setattr(interpreter.shutil, "which", lambda _name: None)
    monkeypatch.delenv(interpreter.PTC_PYTHON_ENV, raising=False)
    assert interpreter.python3_path() is None
    assert "not found" in (interpreter.python3_unavailable_reason() or "")
    interpreter.reset_probe_cache_for_tests()


def test_probe_result_is_cached(monkeypatch):
    interpreter.reset_probe_cache_for_tests()
    calls = {"n": 0}
    real_probe = interpreter._probe

    def _counting_probe():
        calls["n"] += 1
        return real_probe()

    monkeypatch.setattr(interpreter, "_probe", _counting_probe)
    interpreter.python3_path()
    interpreter.python3_unavailable_reason()
    interpreter.python3_path()
    assert calls["n"] == 1
    interpreter.reset_probe_cache_for_tests()


def test_probe_finds_a_real_interpreter():
    interpreter.reset_probe_cache_for_tests()
    # The dev/CI host running this suite has a python3 (we are python).
    assert interpreter.python3_unavailable_reason() is None
    assert interpreter.python3_path()
    interpreter.reset_probe_cache_for_tests()
