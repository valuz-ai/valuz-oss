"""Failure — the module's typed "this did not work, here is why".

Replaces the ``T | str`` idiom (success and failure told apart by RUNTIME
type, a forgotten ``isinstance`` flowing the error onward as a result). Pure
domain; ``Failure.reason`` carries exactly the string the old form returned.

Scope — deliberately NOT the module's only error form: service functions
backing an MCP tool return ``{"error", "hint", ...}`` dicts because that dict
IS the tool's wire payload (structured guidance the model acts on), and
violated invariants still raise (``PlanError`` / ``TaskStateError``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Failure:
    """An expected failure with a human-readable reason.

    ``reason`` is surfaced verbatim — to the model as a tool error, to the user
    as an HTTP detail, or into a task event payload — so write it for whoever
    ends up reading it, not for a log line.
    """

    reason: str

    def __str__(self) -> str:  # so f"{failure}" reads naturally at call sites
        return self.reason


__all__ = ["Failure"]
