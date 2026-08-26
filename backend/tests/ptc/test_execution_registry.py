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


def test_missing_python3_falls_back_to_own_interpreter(monkeypatch):
    import sys

    interpreter.reset_probe_cache_for_tests()
    monkeypatch.setattr(interpreter.shutil, "which", lambda _name: None)
    monkeypatch.delenv(interpreter.PTC_PYTHON_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert interpreter.interpreter_argv() == (sys.executable,)
    assert interpreter.interpreter_unavailable_reason() is None
    interpreter.reset_probe_cache_for_tests()


def test_frozen_fallback_uses_ptc_exec(monkeypatch):
    import sys

    interpreter.reset_probe_cache_for_tests()
    monkeypatch.setattr(interpreter.shutil, "which", lambda _name: None)
    monkeypatch.delenv(interpreter.PTC_PYTHON_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert interpreter.interpreter_argv() == (sys.executable, "--ptc-exec")
    interpreter.reset_probe_cache_for_tests()


def test_broken_override_fails_loudly_without_fallback(monkeypatch):
    interpreter.reset_probe_cache_for_tests()
    monkeypatch.setenv(interpreter.PTC_PYTHON_ENV, "/no/such/python")
    assert interpreter.interpreter_argv() is None
    assert "is not executable" in (interpreter.interpreter_unavailable_reason() or "")
    interpreter.reset_probe_cache_for_tests()


def test_probe_result_is_cached(monkeypatch):
    interpreter.reset_probe_cache_for_tests()
    calls = {"n": 0}
    real_probe = interpreter._probe

    def _counting_probe():
        calls["n"] += 1
        return real_probe()

    monkeypatch.setattr(interpreter, "_probe", _counting_probe)
    interpreter.interpreter_argv()
    interpreter.interpreter_unavailable_reason()
    interpreter.interpreter_argv()
    assert calls["n"] == 1
    interpreter.reset_probe_cache_for_tests()


def test_probe_finds_a_real_interpreter():
    interpreter.reset_probe_cache_for_tests()
    # Some interpreter always resolves (host python3 or our own runtime).
    assert interpreter.interpreter_unavailable_reason() is None
    assert interpreter.interpreter_argv()
    interpreter.reset_probe_cache_for_tests()
