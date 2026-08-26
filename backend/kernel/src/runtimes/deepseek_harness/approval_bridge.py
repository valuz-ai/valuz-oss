"""Pure helpers for the dsh user-questions approval bridge.

Same layout rationale as the claude / codex siblings: stateless
classifiers and payload/answer builders that don't need the runtime
instance, unit-testable without a subprocess.

The wire here is the ``valuz-dsh-kernel-bridge`` plugin's HTTP forward of
``ctx.userQuestions.ask()`` — a batch of dsh ``AskUserQuestionRequest``
questions ``{id, question, detail?, header?, options?: [{label,
description?}], multiSelect?, intent?}``. Two callers exist upstream:

* ``dsh-plan-mode``'s ``exit_plan_mode`` tool sends exactly one question
  with ``intent = {kind: "plan-review", approve: <label>}`` and the plan
  markdown in ``detail`` → surfaced as the ``exit_plan_mode`` approval
  subject (same card slice 1 built for Claude's ExitPlanMode).
* ``dsh-tool-ask-user``'s ``ask_user_question`` sends generic question
  batches → surfaced as ``clarifying_questions`` (same card as Claude's
  AskUserQuestion / codex's request_user_input).

The answer travelling back is dsh's ``AskUserQuestionAnswer``
``{"answers": [{id, selected: [label...], custom?}]}`` — built by
``build_ask_answer_envelope`` from the host's decision verb. Label
semantics (single-select): ``custom`` overrides ``selected``; plan-mode
reads keep-planning feedback from ``custom``.
"""

from __future__ import annotations

from typing import Any, Literal

# dsh-plan-mode's review constants (verified against 0.1.0-rc.6 ==
# 0.1.1-rc.2 sources). Used as fallbacks only — the live labels are read
# from the question's own options / intent so an upstream rename keeps
# working.
_PLAN_REVIEW_INTENT_KIND = "plan-review"
_APPROVE_LABEL_FALLBACK = "Approve"
_KEEP_PLANNING_LABEL_FALLBACK = "Keep planning"


def _plan_review_question(questions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single plan-review question, or None for a generic batch.

    Defensive: a batch mixing plan-review with other questions is treated
    as generic clarifying (upstream never sends that shape).
    """
    if len(questions) != 1:
        return None
    question = questions[0]
    if not isinstance(question, dict):
        return None
    intent = question.get("intent")
    if isinstance(intent, dict) and intent.get("kind") == _PLAN_REVIEW_INTENT_KIND:
        return question
    return None


def classify_dsh_subject(
    questions: list[dict[str, Any]],
) -> Literal["exit_plan_mode", "clarifying_questions"]:
    return (
        "exit_plan_mode" if _plan_review_question(questions) is not None else "clarifying_questions"
    )


def build_dsh_pending_payload(subject: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Subject-specific payload for the approval card.

    ``exit_plan_mode`` ships the plan markdown (the review question's
    ``detail``) under ``plan`` — the exact shape the slice-1 Claude card
    renders. ``clarifying_questions`` maps the batch onto the shared card
    questions shape (the codex bridge's contract).
    """
    if subject == "exit_plan_mode":
        review = _plan_review_question(questions)
        detail = review.get("detail") if review is not None else None
        return {"plan": detail if isinstance(detail, str) else ""}
    mapped: list[dict[str, Any]] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        raw_options = q.get("options")
        options = [
            {
                "label": str(o.get("label") or ""),
                "description": str(o.get("description") or ""),
            }
            for o in (raw_options if isinstance(raw_options, list) else [])
            if isinstance(o, dict)
        ]
        text = str(q.get("question") or "")
        detail = q.get("detail")
        if isinstance(detail, str) and detail.strip():
            # The shared card has no detail slot; keep the supporting text
            # with the question rather than dropping it.
            text = f"{text}\n\n{detail}" if text else detail
        mapped.append(
            {
                "id": str(q.get("id") or ""),
                "question": text,
                "header": str(q.get("header") or ""),
                "options": options,
                "multiSelect": bool(q.get("multiSelect") or False),
                "isOther": False,
                "isSecret": False,
            }
        )
    return {"questions": mapped}


def _plan_review_labels(question: dict[str, Any]) -> tuple[str, str]:
    """(approve_label, keep_planning_label) read from the live question."""
    intent = question.get("intent")
    approve = ""
    if isinstance(intent, dict) and isinstance(intent.get("approve"), str):
        approve = intent["approve"]
    approve = approve or _APPROVE_LABEL_FALLBACK
    keep = ""
    options = question.get("options")
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict):
            label = str(option.get("label") or "")
            if label and label != approve:
                keep = label
                break
    return approve, keep or _KEEP_PLANNING_LABEL_FALLBACK


def build_ask_answer_envelope(
    subject: str,
    questions: list[dict[str, Any]],
    decision: Literal["approve", "reject", "answer"],
    message: str | None,
    answers: dict[str, Any] | None,
) -> dict[str, Any]:
    """dsh ``AskUserQuestionAnswer`` for a decided pending.

    * ``exit_plan_mode`` + ``approve`` → select the intent's approve label
      (the tool returns ``{approved: true}`` and execution continues in
      the SAME turn).
    * ``exit_plan_mode`` + ``reject`` → keep planning; the user's feedback
      rides ``custom`` (single-select semantics: custom overrides
      selected), which plan-mode folds into "their feedback: ...".
    * ``clarifying_questions`` + ``answer`` → remap the card's
      question-text-keyed (or id-keyed, defensively) values onto each
      question: values matching an option label land in ``selected``,
      anything else becomes ``custom`` free text. Missing → skipped
      (``selected: []`` — the documented skip shape).
    * ``clarifying_questions`` + ``reject`` → every question skipped.
    """
    if subject == "exit_plan_mode":
        review = _plan_review_question(questions) or {}
        qid = str(review.get("id") or "plan-review")
        approve_label, keep_label = _plan_review_labels(review)
        if decision == "approve":
            return {"answers": [{"id": qid, "selected": [approve_label]}]}
        feedback = (message or "").strip()
        if feedback:
            return {"answers": [{"id": qid, "selected": [], "custom": feedback}]}
        return {"answers": [{"id": qid, "selected": [keep_label]}]}

    envelope: list[dict[str, Any]] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "")
        if not qid:
            continue
        if decision != "answer" or answers is None:
            envelope.append({"id": qid, "selected": []})
            continue
        value = answers.get(str(q.get("question") or ""))
        if value is None:
            value = answers.get(qid)
        raw_values: list[str]
        if value is None:
            raw_values = []
        elif isinstance(value, list):
            raw_values = [str(v) for v in value]
        else:
            raw_values = [str(value)]
        labels = {
            str(o.get("label") or "")
            for o in (q.get("options") if isinstance(q.get("options"), list) else [])
            if isinstance(o, dict)
        }
        selected = [v for v in raw_values if v in labels]
        custom_parts = [v for v in raw_values if v not in labels and v.strip()]
        entry: dict[str, Any] = {"id": qid, "selected": selected}
        if custom_parts:
            entry["custom"] = "\n".join(custom_parts)
        envelope.append(entry)
    return {"answers": envelope}
