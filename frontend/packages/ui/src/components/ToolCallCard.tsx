import { memo, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import type { PrototypeToolCall, PrototypeToolCallStatus } from "@valuz/shared";
import { useI18n } from "../hooks/use-i18n";
import { MarkdownContent } from "./conversation/MarkdownContent";

const STATUS_KEYS: Record<PrototypeToolCallStatus, string> = {
  success: "toolCall.complete",
  running: "toolCall.running",
  cached: "toolCall.cache",
  error: "toolCall.failed",
};

// Spec 5.4 Tool Call Card
// - 容器 bg #F7F8FA, 外框 1px solid #F3F4F6, radius 8
// - Header padding 9px 12px
// - Tool name 12px / mono / 500
// - Tool label 12px / #6E7481
// - Status tag h-[17px] / px-2 / radius 4 / 11px
const STATUS_CLASSES: Record<PrototypeToolCallStatus, string> = {
  // done: 文字 #131313, bg rgba(83,188,118,0.15), border rgba(83,188,118,0.5)
  success:
    "bg-success-light border border-success-border text-success-text",
  // running: 文字 #131313, bg rgba(114,92,249,0.08), border #D9D9DD
  running:
    "bg-info-light border border-info-border text-info-text",
  // queued: 文字 text-3, bg surface-2
  cached: "bg-surface-2 text-ink-muted",
  error: "bg-error-light border border-error-border text-error-text",
};

// Plan-mode tools carried by the harness / Claude Agent SDK. ``ExitPlanMode``'s
// input is ``{"plan": "<markdown>"}`` — the model's proposed plan — and
// ``EnterPlanMode`` is the plan-mode entry signal. Both would otherwise render
// through the generic card as escaped JSON / raw name (an "unknown tool" look),
// so we special-case them: render the plan as markdown and give each a friendly
// subtitle. Detection is by the stable tool NAME (``tc.title``), which the turn
// builder keeps verbatim.
const EXIT_PLAN_TOOL = "ExitPlanMode";
const ENTER_PLAN_TOOL = "EnterPlanMode";

/** Pull the ``plan`` markdown out of an ExitPlanMode call's input JSON.
 *  Returns null for partial/streaming input (not yet valid JSON) or any
 *  shape without a string ``plan`` — the caller then falls back to the
 *  generic input/output rendering. */
const parsePlan = (input: string | undefined): string | null => {
  if (!input) return null;
  try {
    const parsed = JSON.parse(input) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as { plan?: unknown }).plan === "string"
    ) {
      return (parsed as { plan: string }).plan;
    }
  } catch {
    // Partial JSON while the tool input is still streaming — fall through.
  }
  return null;
};

/** The plan's first non-empty line, stripped of a leading markdown heading
 *  marker, used as a one-glance subtitle in the collapsed header. */
const planHeadline = (plan: string): string => {
  for (const raw of plan.split("\n")) {
    const line = raw.trim();
    if (line) return line.replace(/^#{1,6}\s*/, "");
  }
  return "";
};

export const ToolCallCard = memo(
  function ToolCallCard({
    tc,
    defaultOpen = false,
  }: {
    tc: PrototypeToolCall;
    defaultOpen?: boolean;
  }) {
    const { t } = useI18n();

    // Plan-tool specialization. ``plan`` is non-null only once the full
    // ExitPlanMode input has arrived (parsePlan rejects partial JSON), so a
    // streaming plan shows the generic view until it completes, then flips to
    // the rendered markdown.
    const plan = tc.title === EXIT_PLAN_TOOL ? parsePlan(tc.input) : null;
    const subtitleOverride =
      plan !== null
        ? planHeadline(plan) ||
          t("conversation.planLabel" as Parameters<typeof t>[0])
        : tc.title === ENTER_PLAN_TOOL
          ? t("conversation.enterPlanMode" as Parameters<typeof t>[0])
          : undefined;
    const subtitle = subtitleOverride ?? tc.subtitle;

    // The plan is the artifact the user wants to see — open it by default
    // instead of hiding the proposal behind a fold.
    const [open, setOpen] = useState(defaultOpen || plan !== null);

    return (
      <div className="overflow-hidden rounded-lg border border-surface-border bg-surface-soft">
        <button
          type="button"
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 py-[9px] text-left transition-colors duration-[120ms] hover:bg-[rgba(0,0,0,0.02)]"
          onClick={() => setOpen((current) => !current)}
        >
          <ChevronRight
            className={cn(
              "h-3 w-3 shrink-0 text-[#94A3B8] transition-transform duration-[150ms]",
              open && "rotate-90",
            )}
          />
          <span className="shrink-0 font-mono text-xs font-medium text-ink-heading">
            {tc.title}
          </span>
          {subtitle ? (
            <span className="min-w-0 flex-1 truncate text-xs text-ink-body">
              {subtitle}
            </span>
          ) : (
            <div className="flex-1" />
          )}
          {tc.status === "running" ? (
            <Loader2 className="h-[14px] w-[14px] shrink-0 animate-spin text-brand" />
          ) : null}
          <span
            className={cn(
              "shrink-0 inline-flex h-[17px] items-center rounded-sm px-2 text-2xs",
              STATUS_CLASSES[tc.status],
            )}
          >
            {t(STATUS_KEYS[tc.status] as Parameters<typeof t>[0])}
          </span>
        </button>

        {open ? (
          plan !== null ? (
            // ExitPlanMode: render the proposed plan as markdown rather than a
            // block of escaped JSON.
            <div className="border-t border-surface-border px-3 py-3 pl-8">
              <MarkdownContent content={plan} />
            </div>
          ) : (
            <div className="space-y-2 border-t border-surface-border px-3 pt-2 pb-3 pl-8 font-mono text-2xs leading-[1.6]">
              {tc.input ? (
                <div>
                  <div className="label-mono mb-1">Input</div>
                  <pre className="max-h-40 overflow-auto rounded border border-surface-border bg-surface p-2.5 text-ink-label">
                    {tc.input}
                  </pre>
                </div>
              ) : null}
              {tc.output ? (
                <div>
                  <div className="label-mono mb-1">Output</div>
                  <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded border border-surface-border bg-surface p-2.5 text-ink-label">
                    {tc.output}
                  </pre>
                </div>
              ) : null}
            </div>
          )
        ) : null}
      </div>
    );
  },
  (prev, next) =>
    prev.tc.id === next.tc.id &&
    prev.tc.status === next.tc.status &&
    prev.tc.output === next.tc.output &&
    prev.tc.input === next.tc.input &&
    prev.tc.title === next.tc.title &&
    prev.tc.subtitle === next.tc.subtitle &&
    (prev.defaultOpen ?? false) === (next.defaultOpen ?? false),
);
