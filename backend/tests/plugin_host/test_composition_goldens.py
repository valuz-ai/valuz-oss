"""Bare-OSS composition goldens: the safety net for the backend pluginization.

The OSS features (routes, boot steps, always-on MCP servers) are composed by the
plugin host. Moving them there must not change what the bare app does, so three
facts are pinned against fixtures recorded from the hard-coded composition that
existed before the move (``snapshots/``):

* the full route table, **in order**, plain and under a prefix pair
* the startup / shutdown step sequence of the lifespan
* the always-on MCP specs, what a session is handed, and the order the
  in-process MCP session managers start in

Each scenario builds the real app in a fresh interpreter (see
``snapshot_collector``). An intentional composition change updates the goldens in
the same commit::

    UPDATE_OSS_GOLDENS=1 uv run pytest tests/plugin_host/test_composition_goldens.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = Path(__file__).parent / "snapshots"
COLLECTOR = Path(__file__).parent / "snapshot_collector.py"
_MARKER = "<<SNAPSHOT>>\n"
PREFIXES = ",/valuz-backend"

#: Boot steps that used to be one aggregate step in the hard-coded lifespan and are
#: now one step per owning feature. ``collapse_boot`` folds each group back to the
#: aggregate so the *order* can still be compared with the pre-refactor fixture.
SPLIT_GROUPS: dict[str, tuple[str, ...]] = {
    "start_host_background_services(app)": (
        "start_task_health_monitor(app)",
        "start_docs_auto_discovery(app)",
        "start_skill_auto_scan(app)",
        "start_backup_scheduler(app)",
    ),
    "stop_host_background_services(app)": (
        "stop_task_health_monitor(app)",
        "stop_docs_auto_discovery(app)",
        "stop_skill_auto_scan(app)",
        "stop_backup_scheduler(app)",
        "stop_agent_channels(app)",
        "stop_skill_watcher(app)",
    ),
    "init_kernel(app)": ("init_kernel(app)", "wire_memory_triggers()"),
}

#: Steps added after the refactor — new behaviour, not a reordering of the frozen
#: lifespan — are dropped before comparing with ``boot.pre-refactor.json``. Each
#: one still appears in ``boot.json``, whose golden pins its exact position.
ADDED_AFTER_REFACTOR: frozenset[str] = frozenset(
    {
        "install_session_tools()",  # bundled session commands on the agent PATH
        "start_ui_push_transport()",  # UI bus pushes from other backend processes
        "stop_ui_push_transport()",
        "warn_unreachable_plugin_hooks()",  # plugin hooks a remote kernel can't see
        "cleanup_superseded_extension_versions()",  # third-party plugin dirs (ADR-034)
    }
)


def collect(kind: str, prefix: str | None = None) -> dict[str, Any]:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND_ROOT)}
    if "SYSTEMROOT" in os.environ:  # Windows needs it to start Python at all
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    cmd = [sys.executable, str(COLLECTOR), "--kind", kind]
    if prefix is not None:
        cmd += ["--prefix", prefix]
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=300, cwd=str(BACKEND_ROOT)
    )
    if proc.returncode != 0 or _MARKER not in proc.stdout:
        pytest.fail(f"collector failed for {kind} (exit {proc.returncode}):\n{proc.stderr[-4000:]}")
    payload: dict[str, Any] = json.loads(proc.stdout.split(_MARKER, 1)[1])
    return payload


def collapse_boot(steps: list[str]) -> list[str]:
    """Fold the per-feature steps of ``SPLIT_GROUPS`` back to the old aggregate names.

    A group is folded only where it appears as one consecutive run in the group's
    own order, so a reordering inside or around a group still shows up as a diff.
    """
    out: list[str] = []
    i = 0
    while i < len(steps):
        for aggregate, group in SPLIT_GROUPS.items():
            if len(group) > 1 and tuple(steps[i : i + len(group)]) == group:
                out.append(aggregate)
                i += len(group)
                break
        else:
            out.append(steps[i])
            i += 1
    return out


def _check(name: str, actual: dict[str, Any]) -> None:
    golden = SNAPSHOTS / f"{name}.json"
    if os.environ.get("UPDATE_OSS_GOLDENS") == "1":
        golden.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert golden.is_file(), f"missing golden {golden}; run with UPDATE_OSS_GOLDENS=1"
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected, _describe(expected, actual)


def _describe(expected: Any, actual: Any) -> str:
    lines: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        want, got = expected.get(key), actual.get(key)
        if want == got:
            continue
        if isinstance(want, list) and isinstance(got, list):
            lines += [f"[{key}] - missing {x!r}" for x in want if x not in got][:30]
            lines += [f"[{key}] + extra   {x!r}" for x in got if x not in want][:30]
            if not lines:
                lines.append(f"[{key}] same members, different order")
        else:
            lines.append(f"[{key}] {want!r} -> {got!r}")
    return "bare OSS composition changed:\n" + "\n".join(lines[:80])


@pytest.fixture(scope="module")
def built() -> dict[str, dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    jobs = {
        "routes": ("routes", None),
        "routes-prefixed": ("routes", PREFIXES),
        "boot": ("boot", None),
        "mcp": ("mcp", None),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(collect, *args) for name, args in jobs.items()}
        return {name: future.result() for name, future in futures.items()}


def test_route_table_is_unchanged_and_in_order(built: dict[str, dict[str, Any]]) -> None:
    _check("routes", built["routes"])


def test_route_table_under_a_prefix_pair_is_unchanged(built: dict[str, dict[str, Any]]) -> None:
    _check("routes-prefixed", built["routes-prefixed"])


def test_boot_sequence_matches_its_golden(built: dict[str, dict[str, Any]]) -> None:
    _check("boot", built["boot"])


def test_boot_order_is_the_pre_refactor_order(built: dict[str, dict[str, Any]]) -> None:
    """The hard-coded lifespan's order, recorded before the refactor, still holds.

    ``boot.pre-refactor.json`` is frozen. The only differences allowed are the
    deliberate split of aggregate steps into per-feature steps (``SPLIT_GROUPS``)
    and steps added since (``ADDED_AFTER_REFACTOR``).
    """
    frozen = json.loads((SNAPSHOTS / "boot.pre-refactor.json").read_text(encoding="utf-8"))
    actual = built["boot"]
    for phase in ("startup", "shutdown"):
        steps = [s for s in actual[phase] if s not in ADDED_AFTER_REFACTOR]
        assert collapse_boot(steps) == frozen[phase]


def test_always_on_mcp_is_unchanged(built: dict[str, dict[str, Any]]) -> None:
    _check("mcp", built["mcp"])


def test_collapse_boot_folds_only_exact_runs() -> None:
    group = SPLIT_GROUPS["start_host_background_services(app)"]
    assert collapse_boot(["a()", *group, "b()"]) == [
        "a()",
        "start_host_background_services(app)",
        "b()",
    ]
    swapped = (group[1], group[0], *group[2:])
    assert collapse_boot(list(swapped)) == list(swapped)  # reordered group is not folded
