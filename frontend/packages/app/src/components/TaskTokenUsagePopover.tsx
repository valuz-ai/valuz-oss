import { Gauge } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@valuz/ui";
import {
  useTranslation,
  type TaskRunTokenUsage,
  type TaskTokenUsage,
} from "@valuz/core";

const formatTokens = (value: number): string =>
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);

const compactTokens = (value: number): string =>
  new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);

function runLabel(run: TaskRunTokenUsage): string {
  return run.label?.trim() || run.agent_slug;
}

export function TaskTokenUsagePopover({ usage }: { usage: TaskTokenUsage }) {
  const { t } = useTranslation();
  const inputSide =
    usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens;
  const cacheHitRate = inputSide > 0 ? usage.cache_read_tokens / inputSide : null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t(
            "task.tokenUsage.showDetails" as Parameters<typeof t>[0],
            { count: formatTokens(usage.total_tokens) },
          )}
          className="inline-flex items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
        >
          <Gauge className="h-3.5 w-3.5" aria-hidden />
          <span className="tabular-nums">
            {compactTokens(usage.total_tokens)} Tokens
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" side="bottom" className="w-80 p-3">
        <div className="space-y-3 text-xs">
          <div className="flex items-center justify-between gap-4 font-medium text-ink-heading">
            <span>
              {t("task.tokenUsage.title" as Parameters<typeof t>[0])}
            </span>
            <span className="tabular-nums">
              {formatTokens(usage.total_tokens)}
            </span>
          </div>
          <div className="space-y-1 text-ink-body">
            {[
              [
                t(
                  "conversation.tokenUsage.inputUncached" as Parameters<
                    typeof t
                  >[0],
                ),
                usage.input_tokens,
              ],
              [
                t(
                  "conversation.tokenUsage.outputWithReasoning" as Parameters<
                    typeof t
                  >[0],
                ),
                usage.output_tokens,
              ],
              [
                t(
                  "conversation.tokenUsage.cacheRead" as Parameters<
                    typeof t
                  >[0],
                ),
                usage.cache_read_tokens,
              ],
              [
                t(
                  "conversation.tokenUsage.cacheWrite" as Parameters<
                    typeof t
                  >[0],
                ),
                usage.cache_write_tokens,
              ],
            ].map(([label, value]) => (
              <div
                key={String(label)}
                className="flex items-center justify-between gap-4"
              >
                <span>{label}</span>
                <span className="tabular-nums">
                  {formatTokens(value as number)}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-4">
              <span>
                {t(
                  "conversation.tokenUsage.cacheHitRate" as Parameters<
                    typeof t
                  >[0],
                )}
              </span>
              <span className="tabular-nums">
                {cacheHitRate == null
                  ? "—"
                  : `${(cacheHitRate * 100).toFixed(1)}%`}
              </span>
            </div>
          </div>
          <div className="border-t border-surface-border pt-2">
            <div className="mb-1.5 font-medium text-ink-heading">
              {t("task.tokenUsage.byRun" as Parameters<typeof t>[0])}
            </div>
            <div className="space-y-1.5">
              {usage.runs.map((run) => (
                <div
                  key={run.session_id}
                  className="flex min-w-0 items-center justify-between gap-3"
                >
                  <span className="min-w-0 truncate text-ink-body">
                    {runLabel(run)}
                    <span className="ml-1 text-ink-muted">
                      · {run.kind === "lead" ? "Lead" : run.agent_slug}
                    </span>
                  </span>
                  <span className="shrink-0 tabular-nums text-ink-muted">
                    {formatTokens(run.total_tokens)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
