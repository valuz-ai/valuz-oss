/**
 * Card rendered for ADR-013 ``session.requires_action`` events whose
 * subject is one of ``shell_command`` / ``file_change`` /
 * ``mcp_tool_call`` / ``tool_input`` — i.e. anything the runtime parks
 * on that takes the ordinary approve/reject decision pair. The
 * ``clarifying_questions`` subject has its own renderer
 * (``AskUserQuestionCard``) because it takes a structured answer.
 *
 * Pure presentational — the parent wires the approve / reject
 * callbacks to the host's ``submitAction`` endpoint.
 */
import { memo } from "react";
import {
  CheckCircle2,
  FileEdit,
  Loader2,
  Plug,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";

import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type RequiresActionSubjectKind =
  | "shell_command"
  | "file_change"
  | "mcp_tool_call"
  | "tool_input";

interface RequiresActionCardProps {
  pendingId: string;
  subject: RequiresActionSubjectKind;
  /** Subject-specific payload, decoded from the SSE frame. */
  payload: Record<string, unknown>;
  /** Unix epoch ms (UTC) the event was received (muted timestamp line). */
  receivedAt?: number;
  /** While ``submitting``, both buttons spinner + lock. */
  submitting?: boolean;
  /** After a terminal action_resolved lands the card flips to read-only. */
  answered?: boolean;
  /** Last decision the user picked — used for the answered banner. */
  decision?: "approve" | "reject" | "expired" | "interrupted";
  onApprove: () => void;
  onReject: () => void;
}

const SUBJECT_META: Record<
  RequiresActionSubjectKind,
  { icon: typeof Terminal; titleKey: string; tint: string }
> = {
  shell_command: {
    icon: Terminal,
    titleKey: "conversation.requiresShellCommand",
    tint: "text-amber-600 bg-amber-50",
  },
  file_change: {
    icon: FileEdit,
    titleKey: "conversation.requiresFileChange",
    tint: "text-sky-600 bg-sky-50",
  },
  mcp_tool_call: {
    icon: Plug,
    titleKey: "conversation.requiresMcpCall",
    tint: "text-violet-600 bg-violet-50",
  },
  tool_input: {
    icon: Wrench,
    titleKey: "conversation.requiresToolCall",
    tint: "text-slate-600 bg-slate-50",
  },
};

function _str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function _renderPayload(
  subject: RequiresActionSubjectKind,
  payload: Record<string, unknown>,
): React.ReactNode {
  if (subject === "shell_command") {
    const command = _str(payload.command);
    const cwd = _str(payload.cwd);
    return (
      <div className="space-y-1">
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-ink-heading/95 px-3 py-2 font-mono text-[12px] leading-snug text-white">
          {command || "(empty command)"}
        </pre>
        {cwd ? (
          <p className="text-[11px] text-ink-meta">
            <span className="text-ink-meta/70">cwd:</span> {cwd}
          </p>
        ) : null}
      </div>
    );
  }
  if (subject === "file_change") {
    const path = _str(payload.path);
    const kind = _str(payload.change_kind);
    const summary = _str(payload.diff_summary);
    return (
      <div className="space-y-1">
        <p className="font-mono text-[12px] text-ink-body">
          {path || "(unknown path)"}
        </p>
        <p className="text-[11px] text-ink-meta">
          <span
            className={cn(
              "mr-1.5 inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
              kind === "create"
                ? "bg-emerald-50 text-emerald-700"
                : "bg-sky-50 text-sky-700",
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
        <p className="text-[12px] text-ink-body">
          <span className="font-medium">{server || "(unknown-server)"}</span>
          <span className="text-ink-meta"> · </span>
          <span className="font-mono">{toolName || "(unknown-tool)"}</span>
        </p>
        {argsStr ? (
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 px-3 py-2 font-mono text-[11px] leading-snug text-ink-body">
            {argsStr}
          </pre>
        ) : null}
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
      <p className="font-mono text-[12px] text-ink-body">
        {toolName || "(unknown-tool)"}
      </p>
      {inputStr ? (
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 px-3 py-2 font-mono text-[11px] leading-snug text-ink-body">
          {inputStr}
        </pre>
      ) : null}
    </div>
  );
}

export const RequiresActionCard = memo(function RequiresActionCard({
  pendingId,
  subject,
  payload,
  receivedAt,
  submitting,
  answered,
  decision,
  onApprove,
  onReject,
}: RequiresActionCardProps) {
  const { t } = useI18n();
  const meta = SUBJECT_META[subject] ?? SUBJECT_META.tool_input;
  const Icon = meta.icon;
  return (
    <div className="rounded-lg border border-surface-border bg-surface-soft shadow-sm">
      <div className="flex items-center gap-2 px-3 py-2">
        <div
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
            meta.tint,
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </div>
        <span className="flex-1 text-[13px] font-medium text-ink-heading">
          {t(meta.titleKey as Parameters<typeof t>[0])}
        </span>
        {receivedAt ? (
          <span className="text-[11px] text-ink-meta/80">
            {new Date(receivedAt).toLocaleTimeString()}
          </span>
        ) : null}
      </div>
      <div className="px-3 pb-2">{_renderPayload(subject, payload)}</div>
      <div className="flex items-center justify-between border-t border-surface-border px-3 py-2">
        <span className="font-mono text-[10px] text-ink-meta/70">
          {pendingId.slice(0, 8)}…
        </span>
        {answered ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 text-[12px] font-medium",
              decision === "approve" && "text-emerald-700",
              decision === "reject" && "text-rose-700",
              (decision === "expired" || decision === "interrupted") &&
                "text-ink-muted",
            )}
          >
            {decision === "approve" ? (
              <>
                <CheckCircle2 className="h-3.5 w-3.5" />{" "}
                {t("conversation.decisionApproved")}
              </>
            ) : decision === "reject" ? (
              <>
                <XCircle className="h-3.5 w-3.5" />{" "}
                {t("conversation.decisionRejected")}
              </>
            ) : decision === "expired" ? (
              <span>{t("conversation.decisionExpired")}</span>
            ) : decision === "interrupted" ? (
              <span>{t("conversation.decisionInterrupted")}</span>
            ) : (
              <span>{t("conversation.decisionHandled")}</span>
            )}
          </span>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={submitting}
              onClick={onReject}
              className={cn(
                "inline-flex h-7 items-center rounded-md border px-3 text-xs font-medium",
                "border-rose-200 bg-surface text-rose-700 hover:bg-rose-50 dark:border-rose-900/70 dark:text-rose-300 dark:hover:bg-rose-950/30",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <XCircle className="mr-1.5 h-3 w-3" /> {t("permission.deny")}
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={onApprove}
              className={cn(
                "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
                "bg-brand text-white hover:bg-brand/90",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {submitting ? (
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              ) : (
                <CheckCircle2 className="mr-1.5 h-3 w-3" />
              )}
              {t("permission.approve")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
});
