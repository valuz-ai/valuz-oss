import { memo, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import type { PrototypeToolCall, PrototypeToolCallStatus } from "@valuz/shared";
import { useI18n } from "../hooks/use-i18n";

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
    "bg-[rgba(83,188,118,0.15)] border border-[rgba(83,188,118,0.5)] text-ink-heading",
  // running: 文字 #131313, bg rgba(114,92,249,0.08), border #D9D9DD
  running:
    "bg-[rgba(114,92,249,0.08)] border border-[#D9D9DD] text-ink-heading",
  // queued: 文字 text-3, bg surface-2
  cached: "bg-surface-2 text-ink-muted",
  error: "bg-error-light border border-error/30 text-error-text",
};

export const ToolCallCard = memo(
  function ToolCallCard({
    tc,
    defaultOpen = false,
  }: {
    tc: PrototypeToolCall;
    defaultOpen?: boolean;
  }) {
    const [open, setOpen] = useState(defaultOpen);
    const { t } = useI18n();

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
          {tc.subtitle ? (
            <span className="min-w-0 flex-1 truncate text-xs text-ink-body">
              {tc.subtitle}
            </span>
          ) : (
            <div className="flex-1" />
          )}
          {tc.status === "running" ? (
            <Loader2 className="h-[14px] w-[14px] shrink-0 animate-spin text-brand" />
          ) : null}
          <span
            className={cn(
              "shrink-0 inline-flex h-[17px] items-center rounded-sm px-2 text-[11px]",
              STATUS_CLASSES[tc.status],
            )}
          >
            {t(STATUS_KEYS[tc.status] as Parameters<typeof t>[0])}
          </span>
        </button>

        {open ? (
          <div className="space-y-2 border-t border-surface-border px-3 pt-2 pb-3 pl-8 font-mono text-[11.5px] leading-[1.6]">
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
        ) : null}
      </div>
    );
  },
  (prev, next) =>
    prev.tc.id === next.tc.id &&
    prev.tc.status === next.tc.status &&
    prev.tc.output === next.tc.output &&
    prev.tc.input === next.tc.input &&
    (prev.defaultOpen ?? false) === (next.defaultOpen ?? false),
);
