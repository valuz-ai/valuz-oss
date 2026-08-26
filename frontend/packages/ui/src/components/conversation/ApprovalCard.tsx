/**
 * V5+d008b53 — full approval card for the cross-runtime approval
 * contract v2 (``approve`` / ``approve_with_changes`` /
 * ``approve_for_session`` / ``reject``).
 *
 * Functional successor to ``RequiresActionCard.tsx`` (v1). Built for
 * valuz-native UX:
 *
 *   1. Primary row keeps to two buttons (``Approve`` / ``Reject``);
 *      ``Edit & Approve`` and ``Always for this session`` live in
 *      an overflow ``⋯`` dropdown so the row doesn't become a button
 *      wall.
 *   2. Reject expands inline into a textarea below the row instead of
 *      opening a modal — keeps the user's eyes on the parked tool
 *      call while they compose the redirect.
 *   3. Edit & Approve opens a JSON modal seeded from
 *      ``original_input``.
 *   4. Subject-specific payload rendering: shell command + cwd,
 *      file change with kind badge, MCP server + tool name + args,
 *      generic tool input fallback. ``clarifying_questions`` keeps
 *      its own renderer (``AskUserQuestionCard``) — this card is
 *      only used for the four tool-approval subjects.
 *   5. ``available_decisions`` drives which overflow items are
 *      enabled; disabled items show a hover tooltip explaining why
 *      (``clarifying_questions`` doesn't support ``approve_for_session``
 *      by design — spec §1 "What does not ship").
 *
 * Pure presentational — the parent passes structured callbacks that
 * map to ``POST /v1/sessions/{id}/actions`` with the appropriate
 * decision verb.
 */
import { memo, useState } from "react";
import {
  CheckCircle2,
  ClipboardList,
  FileEdit,
  FilePenLine,
  Loader2,
  MoreHorizontal,
  Plug,
  Sparkles,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";

import { cn } from "../../lib/utils";
import { useI18n } from "../../hooks/use-i18n";
import { Button } from "../ui/button";
import { MarkdownContent } from "./MarkdownContent";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { ApprovalEditModal } from "./ApprovalEditModal";
import { ApprovalRejectInline } from "./ApprovalRejectInline";

export type ApprovalCardSubject =
  | "shell_command"
  | "file_change"
  | "mcp_tool_call"
  | "exit_plan_mode"
  | "tool_input";

export interface ApprovalCardProps {
  pendingId: string;
  subject: ApprovalCardSubject;
  /** Subject-specific payload, decoded from the SSE frame. */
  payload: Record<string, unknown>;
  /**
   * Decisions advertised by the runtime. Drives which overflow menu
   * items are enabled. Always at least ``["approve","reject"]``.
   */
  availableDecisions: string[];
  /**
   * Structured rule preview from the kernel; only present when the
   * runtime advertises ``approve_for_session``. Renders the rule
   * description under the payload + as the suffix on the menu item.
   */
  sessionRulePreviewDisplay: string | null;
  /**
   * Original tool args; seeds the Edit & Approve modal. Required
   * when ``available_decisions`` includes ``approve_with_changes``;
   * the menu item is disabled when this is ``null`` (the runtime
   * advertised the verb but didn't supply the seed — defensive).
   */
  originalInput: Record<string, unknown> | null;
  /** Wall-clock the event was received (for the muted timestamp). */
  receivedAtLabel?: string;
  /** While ``submitting``, all action buttons spinner + lock. */
  submitting?: boolean;
  onApprove: () => void;
  onReject: (reason: string) => void;
  onApproveWithChanges: (modifiedInput: Record<string, unknown>) => void;
  onApproveForSession: () => void;
}

const _SUBJECT_META: Record<
  ApprovalCardSubject,
  { icon: typeof Terminal; titleKey: string; tint: string }
> = {
  shell_command: {
    icon: Terminal,
    titleKey: "conversation.requiresShellCommand",
    tint: "text-warning-text bg-warning-light",
  },
  file_change: {
    icon: FileEdit,
    titleKey: "conversation.requiresFileChange",
    tint:
      "text-[color-mix(in_oklab,var(--accent-sky)_62%,var(--foreground))] bg-[color-mix(in_oklab,var(--accent-sky)_14%,var(--background))]",
  },
  mcp_tool_call: {
    icon: Plug,
    titleKey: "conversation.requiresMcpCall",
    tint: "text-info-text bg-info-light",
  },
  exit_plan_mode: {
    icon: ClipboardList,
    titleKey: "conversation.requiresPlanApproval",
    tint: "text-brand bg-brand-light",
  },
  tool_input: {
    icon: Wrench,
    titleKey: "conversation.requiresToolCall",
    tint: "text-ink-body bg-surface-2",
  },
};

function _str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function _renderPayload(
  subject: ApprovalCardSubject,
  payload: Record<string, unknown>,
): React.ReactNode {
  if (subject === "shell_command") {
    const command = _str(payload.command);
    const cwd = _str(payload.cwd);
    return (
      <div className="space-y-1">
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-ink-heading/95 px-3 py-2 font-mono text-xs leading-snug text-white">
          {command || "(empty command)"}
        </pre>
        {cwd ? (
          <p className="text-2xs text-ink-meta">
            <span className="text-ink-meta/70">cwd:</span> {cwd}
          </p>
        ) : null}
      </div>
    );
  }
  if (subject === "file_change") {
    const path = _str(payload.path) || _str(payload.file_path);
    const kind = _str(payload.change_kind);
    const summary = _str(payload.diff_summary);
    return (
      <div className="space-y-1">
        <p className="font-mono text-xs text-ink-body">
          {path || "(unknown path)"}
        </p>
        <p className="text-2xs text-ink-meta">
          <span
            className={cn(
              "mr-1.5 inline-flex rounded px-1.5 py-0.5 text-micro font-medium uppercase",
              kind === "create"
                ? "bg-success-light text-success-text"
                : "bg-[color-mix(in_oklab,var(--accent-sky)_14%,var(--background))] text-[color-mix(in_oklab,var(--accent-sky)_62%,var(--foreground))]",
            )}
          >
            {kind || "edit"}
          </span>
          {summary || "(no summary)"}
        </p>
      </div>
    );
  }
  if (subject === "mcp_tool_call") {
    const server = _str(payload.server);
    const toolName = _str(payload.tool_name);
    const args = payload.args;
    let argsStr = "";
    try {
      argsStr =
        args && typeof args === "object" ? JSON.stringify(args, null, 2) : "";
    } catch {
      argsStr = "";
    }
    return (
      <div className="space-y-1">
        <p className="text-xs text-ink-body">
          <span className="font-medium">{server || "(unknown-server)"}</span>
          <span className="text-ink-meta"> · </span>
          <span className="font-mono">{toolName || "(unknown-tool)"}</span>
        </p>
        {argsStr ? (
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 px-3 py-2 font-mono text-2xs leading-snug text-ink-body">
            {argsStr}
          </pre>
        ) : null}
      </div>
    );
  }
  if (subject === "exit_plan_mode") {
    // Payload is ``{plan: "<markdown>"}`` (approval_bridge.py). Render the
    // proposed plan so the user can approve/reject with the plan in view,
    // rather than the generic tool_name fallback (which would be empty →
    // "(unknown-tool)"). Bounded + scrollable so a long plan doesn't push
    // the decision buttons off-screen.
    const plan = _str(payload.plan);
    return (
      <div className="max-h-64 overflow-auto rounded-md bg-surface-2 px-3 py-2">
        <MarkdownContent content={plan || "(empty plan)"} />
      </div>
    );
  }
  // tool_input — generic catch-all
  const toolName = _str(payload.tool_name);
  const input = payload.input;
  let inputStr = "";
  try {
    inputStr =
      input && typeof input === "object" ? JSON.stringify(input, null, 2) : "";
  } catch {
    inputStr = "";
  }
  return (
    <div className="space-y-1">
      <p className="font-mono text-xs text-ink-body">
        {toolName || "(unknown-tool)"}
      </p>
      {inputStr ? (
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 px-3 py-2 font-mono text-2xs leading-snug text-ink-body">
          {inputStr}
        </pre>
      ) : null}
    </div>
  );
}

export const ApprovalCard = memo(function ApprovalCard({
  pendingId,
  subject,
  payload,
  availableDecisions,
  sessionRulePreviewDisplay,
  originalInput,
  receivedAtLabel,
  submitting,
  onApprove,
  onReject,
  onApproveWithChanges,
  onApproveForSession,
}: ApprovalCardProps) {
  const { t } = useI18n();
  const meta = _SUBJECT_META[subject] ?? _SUBJECT_META.tool_input;
  const Icon = meta.icon;

  // Plan review reads better with plan-specific verbs (product decision:
  // one "approve & start executing" button across runtimes; "reject"
  // becomes "keep planning" — the feedback text goes back to the model,
  // which refines inside plan mode).
  const approveLabelKey =
    subject === "exit_plan_mode"
      ? "conversation.planApproveAndRun"
      : "conversation.approvalApprove";
  const rejectLabelKey =
    subject === "exit_plan_mode"
      ? "conversation.planKeepPlanning"
      : "conversation.approvalReject";

  const canEdit =
    availableDecisions.includes("approve_with_changes") &&
    originalInput !== null;
  const canApproveForSession =
    availableDecisions.includes("approve_for_session") &&
    sessionRulePreviewDisplay !== null;

  // Reject flow expands inline; Edit flow opens a modal. Both are
  // mutually exclusive — opening one closes the other.
  const [mode, setMode] = useState<"idle" | "rejecting" | "editing">("idle");

  return (
    <div className="rounded-lg border-l-2 border-warning bg-surface-soft shadow-sm">
      <div className="flex items-center gap-2 px-3 py-2">
        <div
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
            meta.tint,
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </div>
        <span className="flex-1 text-sm font-medium text-ink-heading">
          {t(meta.titleKey as Parameters<typeof t>[0])}
        </span>
        {receivedAtLabel ? (
          <span className="text-2xs text-ink-meta/80">
            {receivedAtLabel}
          </span>
        ) : null}
      </div>
      <div className="px-3 pb-2">{_renderPayload(subject, payload)}</div>

      {/* Rule preview line — only shown when the runtime advertises
          ``approve_for_session``. The same string seeds the overflow
          menu item so the user knows what rule they're about to
          commit before they click. */}
      {sessionRulePreviewDisplay ? (
        <div className="flex items-center gap-1.5 px-3 pb-2 text-2xs text-sky-700/90">
          <Sparkles className="h-3 w-3 shrink-0" />
          <span className="truncate">
            {t("conversation.approvalRulePreviewPrefix")}
            <span className="font-mono">{sessionRulePreviewDisplay}</span>
          </span>
        </div>
      ) : null}

      <div className="border-t border-surface-border px-3 py-2">
        {mode === "rejecting" ? (
          <ApprovalRejectInline
            submitting={submitting}
            onSubmit={(reason) => {
              onReject(reason);
              // Keep the inline open while submitting — the parent
              // flips the card into the resolved strip when
              // ``action_resolved`` lands, replacing this whole
              // component.
            }}
            onCancel={() => setMode("idle")}
          />
        ) : (
          <div className="flex items-center justify-between">
            <span className="font-mono text-micro text-ink-meta/70">
              {pendingId.slice(0, 8)}…
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setMode("rejecting")}
                disabled={submitting}
                className="border-rose-200 text-rose-700 hover:bg-rose-50"
              >
                <XCircle className="mr-1.5 h-3 w-3" />
                {t(rejectLabelKey as Parameters<typeof t>[0])}
              </Button>
              <Button
                size="sm"
                onClick={onApprove}
                disabled={submitting}
                className="bg-brand text-white hover:bg-brand-hover"
              >
                {submitting ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : (
                  <CheckCircle2 className="mr-1.5 h-3 w-3" />
                )}
                {t(approveLabelKey as Parameters<typeof t>[0])}
              </Button>
              {(canEdit || canApproveForSession) && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={submitting}
                      aria-label={t("conversation.approvalMoreActions")}
                      className="h-7 w-7 p-0"
                    >
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="min-w-[220px]">
                    <DropdownMenuItem
                      disabled={!canEdit}
                      onSelect={(e) => {
                        e.preventDefault();
                        setMode("editing");
                      }}
                    >
                      <FilePenLine className="mr-2 h-3.5 w-3.5" />
                      <span>
                        {t("conversation.approvalApproveWithChanges")}
                      </span>
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={!canApproveForSession}
                      title={
                        canApproveForSession
                          ? undefined
                          : t("conversation.approvalApproveForSessionDisabled")
                      }
                      onSelect={(e) => {
                        e.preventDefault();
                        onApproveForSession();
                      }}
                      className={cn(canApproveForSession && "text-sky-700")}
                    >
                      <Sparkles className="mr-2 h-3.5 w-3.5" />
                      <span className="flex-1 truncate">
                        {t("conversation.approvalApproveForSession")}
                        {sessionRulePreviewDisplay ? (
                          <span className="ml-1 text-2xs text-ink-meta/80">
                            ({sessionRulePreviewDisplay})
                          </span>
                        ) : null}
                      </span>
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Edit & Approve modal — rendered outside the row so the
          dropdown can close cleanly before the dialog opens. */}
      <ApprovalEditModal
        open={mode === "editing"}
        originalInput={originalInput}
        submitting={submitting}
        onSubmit={(modifiedInput) => {
          onApproveWithChanges(modifiedInput);
          setMode("idle");
        }}
        onCancel={() => setMode("idle")}
      />
    </div>
  );
});
