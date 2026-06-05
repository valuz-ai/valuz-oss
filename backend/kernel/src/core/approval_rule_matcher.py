"""Per-runtime approval-rule matcher protocol + default exact-args matcher.

The harness's session-scoped approval cache (``SessionApprovalCache``) is
kernel-owned, but the *rule grammar* — what counts as a "match" for a
previously-approved tool call — is per-runtime. Each runtime adapter
ships its own matcher so it can leverage SDK-native pattern primitives
where available (Claude's ``PermissionUpdate.suggestions``, codex's
canonicalized argv, etc.) without flattening to a lowest-common-
denominator key shape.

The default ``ExactArgsRuleMatcher`` matches ``(tool_name, canonical
JSON of args)`` exactly. Runtimes with no richer grammar use it as-is
(DeepAgents, codex v2); runtimes that *do* have a richer grammar still
fall back to it when the SDK provides no pattern for the current call
(Claude with empty ``suggestions``).

The kernel never inspects ``rule_data``; only the originating runtime's
matcher does. This means rules from Claude sessions are not meaningful
to codex sessions and vice versa — fine because
``Session.runtime_provider`` is immutable for a session's lifetime, so
a rule never has to be interpretable by anything other than the runtime
that created it.

See ``docs/design/approve-for-session.md`` §4.2 for the contract.
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any, NamedTuple, Protocol


class RuleDerivation(NamedTuple):
    """A matcher's proposal for the rule that would be recorded if the user
    picks ``approve_for_session``.

    Wire shape on ``requires_action.data.session_rule_preview``:

    * ``kind`` — coarse UI category (``exact`` / ``command_prefix`` /
      ``file_glob`` / ``mcp_exact``). Frontend keys per-category styling
      off it; unknown values render with the ``exact`` fallback.
    * ``runtime_kind`` — fine-grained per-runtime tag (e.g.
      ``claude_permission_update``). The kernel never inspects it; only
      the originating runtime's matcher does, via ``match``.
    * ``display`` — user-facing label, rendered verbatim on the
      "Always for this session" button and on the resolved card.
    * ``rule_data`` — opaque, JSON-serializable, runtime-interpreted.
    """

    kind: str
    runtime_kind: str
    display: str
    rule_data: dict[str, Any]


class RuntimeApprovalRuleMatcher(Protocol):
    """Per-runtime rule derivation + matching for the session approval cache.

    Every runtime adapter exposes one of these via
    ``RuntimePort.approval_rule_matcher``. The kernel calls ``derive_rule``
    when emitting a ``requires_action`` (to populate
    ``session_rule_preview`` on the pending payload) and ``match`` when
    checking whether a new approval request is covered by a stored rule.

    Both methods are pure: no side effects, no I/O, safe to call from
    any thread / event-loop context. The returned ``rule_data`` is
    opaque to the kernel — only the matcher that produced it is
    expected to interpret it.
    """

    def derive_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> RuleDerivation:
        """Return the rule shape that would be recorded for this call.

        Always returns a usable derivation. If the runtime has no smart
        pattern for this call, it returns the exact-match shape
        (delegating to ``ExactArgsRuleMatcher``). See the
        :class:`RuleDerivation` docstring for field semantics.
        """
        ...

    def match(
        self,
        rule_runtime_kind: str,
        rule_data: dict[str, Any],
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> bool:
        """Return ``True`` iff this stored rule covers the given call.

        Receives the rule's ``runtime_kind`` and ``rule_data`` separately
        rather than the full ``SessionRule`` to keep this protocol free
        of a circular import on ``session_approval_cache``.
        """
        ...


class ExactArgsRuleMatcher:
    """Default matcher — ``(tool_name, canonical args JSON)`` exact match.

    Used directly by runtimes whose SDK exposes no pattern grammar (codex,
    DeepAgents), and as the fallback path for runtimes that *do* have
    one (Claude) when the SDK can't propose a pattern for the current
    call.

    Canonicalization is ``json.dumps(args, sort_keys=True, default=str)``
    — so reordering a dict's keys won't break the match, but a tiny
    difference like ``"npm test"`` vs ``"npm test "`` will. This matches
    the "exact" semantics in the spec doc §5; broader matching (argv
    canonicalization, command prefix) is v3+ deferred per
    ``docs/design/approve-for-session.md`` §11.

    Stateless — safe to instantiate once per runtime instance and reuse.
    """

    RUNTIME_KIND = "exact"

    @staticmethod
    def _canonicalize(args: dict[str, Any]) -> str:
        return json.dumps(args, sort_keys=True, default=str)

    @staticmethod
    def _display_label(tool_name: str) -> str:
        # Kept short on purpose — the frontend renders this verbatim in the
        # "Always for this session" button. Users without SDK-supplied
        # pattern grammar see "this exact <tool>" so the scope is honest.
        return f"this exact {tool_name} call"

    def derive_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> RuleDerivation:
        # ``subject`` and ``runtime_extras`` are unused by this matcher
        # but appear in the signature so per-runtime matchers can use them
        # uniformly (matches RuntimeApprovalRuleMatcher protocol shape).
        _ = subject, runtime_extras
        canonical = self._canonicalize(args)
        return RuleDerivation(
            kind="exact",
            runtime_kind=self.RUNTIME_KIND,
            display=self._display_label(tool_name),
            rule_data={"tool_name": tool_name, "args_canonical": canonical},
        )

    def match(
        self,
        rule_runtime_kind: str,
        rule_data: dict[str, Any],
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> bool:
        _ = subject, runtime_extras
        if rule_runtime_kind != self.RUNTIME_KIND:
            return False
        if rule_data.get("tool_name") != tool_name:
            return False
        return rule_data.get("args_canonical") == self._canonicalize(args)


class ClaudePermissionUpdateRuleMatcher:
    """Claude-specific matcher that leverages the SDK's
    ``PermissionUpdate.suggestions`` (delivered to ``can_use_tool``
    via ``ToolPermissionContext.suggestions``) to derive pattern-grammar
    rules for free: Claude proposes which tool + which command-prefix
    / glob / mcp-name pattern is reasonable for this call, the harness
    just stores and replays it.

    Grammar implemented (matches Claude's 4-pattern permission rule
    syntax):

    * ``Bash(<prefix>:*)`` — command starting with the prefix (e.g.
      ``Bash(npm test:*)`` matches ``npm test``, ``npm test foo``,
      ``npm test --watch``; does NOT match ``npm install``).
    * ``Bash(<exact-command>)`` — exact equality (rule_content does
      not end in ``:*``).
    * ``Edit(<glob>)`` / ``Read(<glob>)`` / ``Write(<glob>)`` /
      ``Glob(<glob>)`` / ``Grep(<glob>)`` — ``fnmatch`` against the
      tool's ``file_path`` arg.
    * ``mcp__<server>__<tool>`` — exact tool-name match (rule_content
      is typically ``None``; tool-name equality is the rule).

    Falls back to ``ExactArgsRuleMatcher`` when the SDK provides no
    usable ``addRules`` suggestion for the current call — e.g. a
    custom user tool the SDK has no pattern intuition for. Same
    fallback applies on the match path: a stored rule with
    ``runtime_kind == "exact"`` is delegated to the exact-args matcher
    so a session can carry a mix of pattern and exact rules without
    the kernel cache caring which is which.

    The matcher reads ``runtime_extras['claude_permission_updates']``
    on ``derive_rule`` — that's the bridge's contract for plumbing the
    SDK's ``context.suggestions`` through to the matcher without the
    kernel knowing about SDK types. Each entry is a dict matching
    ``PermissionUpdate.to_dict()`` (snake or camel case both
    tolerated, since suggestions arrive both from the dataclass and
    from CLI-side TypedDict shapes).
    """

    RUNTIME_KIND = "claude_permission_update"

    # Tools whose pattern grammar uses ``fnmatch`` against the
    # ``file_path`` arg. Kept narrow on purpose — anything outside this
    # set with a non-None ``rule_content`` falls back to the "no smart
    # match" path (tool-name equality only).
    _FILE_GLOB_TOOLS: frozenset[str] = frozenset({"Edit", "Read", "Write", "Glob", "Grep"})

    def __init__(self) -> None:
        # Composition over inheritance — the fallback path delegates here.
        self._fallback = ExactArgsRuleMatcher()

    def derive_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> RuleDerivation:
        suggestions = runtime_extras.get("claude_permission_updates") or []
        chosen = self._pick_first_addrules(suggestions)
        if chosen is None:
            # SDK had no pattern proposal for this call — fall through to
            # exact-match so the user still gets a rule, just narrower.
            return self._fallback.derive_rule(subject, tool_name, args, runtime_extras)

        rule_tool_name = chosen["tool_name"]
        rule_content = chosen["rule_content"]
        kind, display = self._categorize(rule_tool_name, rule_content)
        return RuleDerivation(
            kind=kind,
            runtime_kind=self.RUNTIME_KIND,
            display=display,
            rule_data={
                "tool_name": rule_tool_name,
                "rule_content": rule_content,
            },
        )

    def match(
        self,
        rule_runtime_kind: str,
        rule_data: dict[str, Any],
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> bool:
        if rule_runtime_kind == ExactArgsRuleMatcher.RUNTIME_KIND:
            return self._fallback.match(
                rule_runtime_kind, rule_data, subject, tool_name, args, runtime_extras
            )
        if rule_runtime_kind != self.RUNTIME_KIND:
            return False

        rule_tool_name = str(rule_data.get("tool_name") or "")
        rule_content = rule_data.get("rule_content")
        if rule_tool_name != tool_name:
            return False
        if rule_content is None:
            # Bare rule (e.g. ``mcp__server__tool`` with no content) —
            # tool-name equality is the full match.
            return True
        rule_content = str(rule_content)
        if tool_name == "Bash":
            return self._match_bash(args, rule_content)
        if tool_name in self._FILE_GLOB_TOOLS:
            return self._match_file_glob(args, rule_content)
        # Unknown tool + rule_content: be conservative — no match. A
        # future SDK could ship a new rule kind we don't understand; we
        # don't want to silently auto-approve.
        return False

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _pick_first_addrules(suggestions: Any) -> dict[str, Any] | None:
        """Return the first ``{tool_name, rule_content}`` extracted from
        an ``addRules`` suggestion with ``behavior == "allow"``. Tolerates
        both ``PermissionUpdate`` dataclass instances and dict-shaped
        suggestions (snake_case from Python SDK, camelCase from CLI).
        """
        if not isinstance(suggestions, (list, tuple)):
            return None
        for update in suggestions:
            data = _permission_update_to_dict(update)
            if data is None:
                continue
            if data.get("type") != "addRules":
                continue
            # behavior is optional in the type but Claude's allow-suggestions
            # always carry it. Be defensive — only auto-approve rules
            # propose behavior="allow"; deny/ask are out of scope here.
            behavior = data.get("behavior")
            if behavior is not None and behavior != "allow":
                continue
            rules = data.get("rules") or []
            if not isinstance(rules, (list, tuple)) or not rules:
                continue
            first = rules[0]
            if isinstance(first, dict):
                tname = first.get("tool_name") or first.get("toolName")
                rcontent = first.get("rule_content")
                if rcontent is None:
                    rcontent = first.get("ruleContent")
            else:
                tname = getattr(first, "tool_name", None)
                rcontent = getattr(first, "rule_content", None)
            if not tname:
                continue
            return {"tool_name": str(tname), "rule_content": rcontent}
        return None

    @staticmethod
    def _match_bash(args: dict[str, Any], rule_content: str) -> bool:
        command = str(args.get("command") or "")
        if rule_content.endswith(":*"):
            prefix = rule_content[:-2]
            if not prefix:
                # ``Bash(:*)`` would match everything — refuse rather
                # than auto-approve every shell command.
                return False
            # Matches ``<prefix>`` exactly, or ``<prefix> ...`` (anything
            # following a space). Does NOT match ``<prefix>foo`` (no
            # space).
            return command == prefix or command.startswith(prefix + " ")
        return command == rule_content

    @staticmethod
    def _match_file_glob(args: dict[str, Any], rule_content: str) -> bool:
        # Most file-mutation tools use ``file_path``; Glob uses ``pattern``.
        # Try ``file_path`` first since it's the dominant case.
        path = args.get("file_path") or args.get("path") or args.get("pattern")
        if not isinstance(path, str):
            return False
        return fnmatch.fnmatch(path, rule_content)

    @classmethod
    def _categorize(cls, rule_tool_name: str, rule_content: Any) -> tuple[str, str]:
        """Return ``(kind, display_label)`` for the wire's
        ``session_rule_preview.kind`` field + the user-facing button
        label."""
        if rule_tool_name == "Bash":
            if isinstance(rule_content, str) and rule_content.endswith(":*"):
                return ("command_prefix", f"Bash({rule_content})")
            content = "" if rule_content is None else str(rule_content)
            return ("exact", f"Bash({content})")
        if rule_tool_name in cls._FILE_GLOB_TOOLS and rule_content is not None:
            return ("file_glob", f"{rule_tool_name}({rule_content})")
        if rule_tool_name.startswith("mcp__"):
            # MCP tool patterns are exact name matches (no rule_content
            # for the canonical case); show the bare name.
            return ("mcp_exact", rule_tool_name)
        # Unknown tool — fall through with a conservative display.
        content_str = f"({rule_content})" if rule_content else ""
        return ("exact", f"{rule_tool_name}{content_str}")


def _permission_update_to_dict(update: Any) -> dict[str, Any] | None:
    """Normalize a ``PermissionUpdate`` (dataclass or dict, snake or camel)
    to a plain dict the matcher can read uniformly. Returns None if the
    shape is unrecognized so callers can skip it."""
    if isinstance(update, dict):
        return update
    to_dict = getattr(update, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            return None
    # Defensive — try attribute-access on the dataclass directly.
    typ = getattr(update, "type", None)
    rules = getattr(update, "rules", None)
    if typ is None:
        return None
    out: dict[str, Any] = {"type": typ}
    if rules is not None:
        normalized_rules: list[Any] = []
        for r in rules:
            if isinstance(r, dict):
                normalized_rules.append(r)
            else:
                normalized_rules.append(
                    {
                        "tool_name": getattr(r, "tool_name", None),
                        "rule_content": getattr(r, "rule_content", None),
                    }
                )
        out["rules"] = normalized_rules
    behavior = getattr(update, "behavior", None)
    if behavior is not None:
        out["behavior"] = behavior
    return out
