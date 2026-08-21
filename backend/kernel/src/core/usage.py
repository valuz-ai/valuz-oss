"""Per-model token-usage arithmetic shared by runtimes and the orchestrator.

The kernel's durable contract is that a ``Message`` row carries **one turn's
increment** — session, task, monthly-rollup and billing surfaces all sum
message rows. Runtimes whose SDK reports a *cumulative* counter must
therefore difference it before emitting ``usage_update``; the orchestrator,
symmetrically, must be able to merge several increments that land inside one
turn. Both directions operate on the same ``model_usage`` shape (a
``{model: {field: value}}`` map carried verbatim from the SDK), so the
add/subtract rule lives here once instead of being re-derived at each site.

Only *counters* accumulate. A handful of fields describe the model rather
than the spend (``contextWindow``, ``canonicalModel``, …) and must survive
untouched: differencing them yields zero and adding them doubles them.
"""

from __future__ import annotations

import copy
from typing import Any

# Fields inside a ``model_usage`` entry that describe the model or the
# request rather than accumulating with it. Everything else numeric is
# treated as a counter, so a field the SDK adds later is summed/differenced
# by default — the failure mode we cannot afford is silently dropping spend.
NON_ADDITIVE_USAGE_KEYS = frozenset(
    {
        "contextWindow",
        "maxOutputTokens",
        "canonicalModel",
        "provider",
        "service_tier",
        "speed",
        "inference_geo",
        "iterations",
    }
)


def _is_counter(key: str, value: Any) -> bool:
    return (
        key not in NON_ADDITIVE_USAGE_KEYS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def merge_model_usage(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Add ``incoming`` increments onto ``base``, per model.

    Non-counter fields take the newer value; a model that only appears in
    one of the two maps is carried through as-is.
    """
    merged: dict[str, Any] = {
        model: dict(entry) if isinstance(entry, dict) else entry for model, entry in base.items()
    }
    for model, entry in incoming.items():
        if not isinstance(entry, dict):
            continue
        current = merged.get(model)
        if not isinstance(current, dict):
            merged[model] = copy.deepcopy(entry)
            continue
        for key, value in entry.items():
            previous = current.get(key)
            if _is_counter(key, value) and _is_counter(key, previous):
                current[key] = previous + value
            else:
                current[key] = copy.deepcopy(value)
    return merged


def diff_model_usage(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Subtract the previous cumulative snapshot from ``current``, per model.

    Counters are clamped at zero: a counter that went *backwards* means the
    reporting process restarted (or reset its conversation), and the
    caller is expected to have already dropped the stale baseline — the
    clamp is the belt-and-braces half of that.
    """
    delta: dict[str, Any] = {}
    for model, entry in current.items():
        if not isinstance(entry, dict):
            continue
        base = (previous or {}).get(model)
        if not isinstance(base, dict):
            delta[model] = copy.deepcopy(entry)
            continue
        out: dict[str, Any] = {}
        for key, value in entry.items():
            before = base.get(key)
            if _is_counter(key, value) and _is_counter(key, before):
                diff = value - before
                out[key] = diff if diff > 0 else type(diff)(0)
            else:
                out[key] = copy.deepcopy(value)
        delta[model] = out
    return delta
