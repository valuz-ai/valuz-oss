/**
 * V5+d008b53 — strip rendered after the kernel's
 * ``SessionApprovalCache`` short-circuits a tool call with
 * ``decision="auto_approved"``.
 *
 * The kernel always emits the pair (``requires_action`` →
 * ``action_resolved(auto_approved)``) for cache hits, so the host
 * captures the originating subject + payload off the requires_action
 * row in ``pendingApprovals`` and hands them down here. The strip
 * then shows a one-line summary of WHAT was auto-approved (e.g.
 * ``reportify-stock.stock_quote(symbol=000858)``) plus the rule that
 * fired, so the user can audit at a glance instead of guessing.
 *
 * Lifecycle is symmetric to ``ApprovalResolvedStrip``: the caller
 * adds the notice on the resolved event and schedules a setTimeout
 * removal so the tray doesn't accumulate forever. Hide window is
 * longer (5s vs 2s for user-resolved) because the user didn't
 * initiate the action and needs a moment to notice it.
 */
import { memo } from "react";
import { Sparkles } from "lucide-react";

import { cn } from "../../lib/utils";
import { useI18n } from "../../hooks/use-i18n";

import type { ApprovalCardSubject } from "./ApprovalCard";

interface AutoApprovedStripProps {
  /**
   * Subject of the paired ``requires_action`` event. Drives the
   * one-line payload summary rendered first on the strip. Omit when
   * the originating requires_action couldn't be matched (rare —
   * happens only on reconnects where the requires_action was
   * dropped from the tray before action_resolved arrived).
   */
  subject?: ApprovalCardSubject;
  /** Subject-specific payload, decoded from the SSE frame. Same
   *  shape that ``ApprovalCard`` receives. */
  payload?: Record<string, unknown>;
  /**
   * Display string of the rule that fired (e.g. ``Bash(npm test:*)``).
   * Surfaced as a muted suffix after the payload summary so users can
   * see WHICH rule triggered, not just WHAT was approved. The kernel
   * ships ``auto_resolved_by_rule_id`` on the event but not the
   * preview text — the host derives it by matching the rule_id back
   * to the originating ``approve_for_session`` event in the same
   * session. Falls back to a generic label when the lookup misses.
   */
  rulePreviewDisplay: string | null;
  resolvedAtLabel?: string;
}

function _str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/**
 * Build a single-line summary describing WHAT was auto-approved.
 * Mirrors the rich rendering ``ApprovalCard._renderPayload`` does but
 * collapsed to a single span — the strip is a glance-level notice,
 * not the full approval card.
 *
 * Returns ``null`` if the subject is unknown or the payload is too
 * sparse to summarise; the caller falls back to the generic label.
 */
function _subjectSummary(
  subject: ApprovalCardSubject | undefined,
  payload: Record<string, unknown> | undefined,
): string | null {
  if (!subject || !payload) return null;
  if (subject === "shell_command") {
    const command = _str(payload.command).trim();
    if (!command) return null;
    // Collapse newlines / trim to the first line so the strip stays
    // single-row. The full command lives on the (now-gone)
    // ApprovalCard if the user really needs it; this is a glance.
    const firstLine = command.split("\n", 1)[0] ?? command;
    return `$ ${firstLine}`;
  }
  if (subject === "file_change") {
    const path = _str(payload.path) || _str(payload.file_path);
    const kind = _str(payload.change_kind).toUpperCase();
    if (!path) return null;
    return kind ? `[${kind}] ${path}` : path;
  }
  if (subject === "mcp_tool_call") {
    const server = _str(payload.server);
    const toolName = _str(payload.tool_name);
    const args = payload.args;
    const target =
      server && toolName ? `${server}.${toolName}` : toolName || server;
    if (!target) return null;
    // Inline-stringify args for a one-liner. ``JSON.stringify``
    // without spaces keeps it dense; long args get truncated by the
    // CSS ``truncate`` class so we don't need to slice here.
    let argsStr = "";
    if (args && typeof args === "object") {
      try {
        argsStr = JSON.stringify(args);
      } catch {
        argsStr = "";
      }
    }
    return argsStr ? `${target}(${argsStr})` : target;
  }
  // tool_input — generic catch-all for non-classified tools.
  const toolName = _str(payload.tool_name);
  const input = payload.input;
  if (!toolName && !input) return null;
  let inputStr = "";
  if (input && typeof input === "object") {
    try {
      inputStr = JSON.stringify(input);
    } catch {
      inputStr = "";
    }
  }
  return inputStr ? `${toolName || "(tool)"}(${inputStr})` : toolName || null;
}

export const AutoApprovedStrip = memo(function AutoApprovedStrip({
  subject,
  payload,
  rulePreviewDisplay,
  resolvedAtLabel,
}: AutoApprovedStripProps) {
  const { t } = useI18n();
  const summary = _subjectSummary(subject, payload);
  // Main line: prefer the concrete payload summary; fall back to the
  // rule display; finally the generic "auto-approved" label so the
  // strip always renders SOMETHING informative.
  const mainLabel =
    summary ??
    (rulePreviewDisplay
      ? t("conversation.approvalAutoApprovedByRule", {
          rule: rulePreviewDisplay,
        })
      : t("conversation.approvalDecisionAutoApproved"));
  // Secondary: when we showed a payload summary on the main line, the
  // rule still belongs on the strip — render it small + muted so the
  // user can see WHICH rule fired without losing the WHAT.
  const ruleSuffix =
    summary && rulePreviewDisplay
      ? t("conversation.approvalAutoApprovedRuleSuffix", {
          rule: rulePreviewDisplay,
        })
      : null;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border border-dashed border-slate-300 bg-slate-50/40 px-2.5 py-1.5",
      )}
    >
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5 text-[12px] text-ink-muted">
        <span className="truncate font-mono">{mainLabel}</span>
        {ruleSuffix ? (
          <span className="shrink-0 text-[11px] text-ink-meta/70">
            {ruleSuffix}
          </span>
        ) : null}
      </span>
      {resolvedAtLabel ? (
        <span className="shrink-0 text-[11px] text-ink-meta/80">
          {resolvedAtLabel}
        </span>
      ) : null}
    </div>
  );
});
