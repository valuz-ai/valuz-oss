import { memo, useCallback, useState } from "react";
import {
  AlertTriangle,
  Archive,
  BookOpenText,
  Check,
  Loader2,
  Pencil,
  Plus,
  X,
} from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";

export interface PlaybookOperationView {
  state: string;
  preview: Record<string, unknown>;
  result_payload: Record<string, unknown>;
  error_message: string | null;
}

export interface PlaybookOperationCardProps {
  operation: PlaybookOperationView;
  busy?: "confirm" | "cancel" | null;
  onConfirm: () => void;
  onCancel: () => void;
  onOpenPlaybook?: (definitionId: string) => void;
}

const changeIcon = {
  create: Plus,
  update: Pencil,
  retire: Archive,
} as const;

export const PlaybookOperationCard = memo(function PlaybookOperationCard({
  operation,
  busy,
  onConfirm,
  onCancel,
  onOpenPlaybook,
}: PlaybookOperationCardProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const change = String(operation.preview.change ?? "update") as
    | "create"
    | "update"
    | "retire";
  const name = String(operation.preview.name ?? t("playbook.title"));
  const content =
    typeof operation.preview.content === "string"
      ? operation.preview.content
      : null;
  const Icon = changeIcon[change] ?? BookOpenText;
  const terminal = [
    "succeeded",
    "cancelled",
    "expired",
    "stale",
    "superseded",
  ].includes(operation.state);
  const confirming = busy === "confirm" || operation.state === "executing";
  const canConfirm =
    operation.state === "proposed" ||
    operation.state === "awaiting_confirmation" ||
    operation.state === "failed";
  const canCancel =
    operation.state === "proposed" ||
    operation.state === "awaiting_confirmation";
  const definitionId =
    typeof operation.result_payload.definition_id === "string"
      ? operation.result_payload.definition_id
      : null;
  const measure = useCallback(
    (node: HTMLDivElement | null) => {
      if (node && content) setOverflowing(node.scrollHeight > node.clientHeight + 1);
    },
    [content],
  );

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface-soft",
        operation.state === "succeeded" && "border-success/40 bg-success/5",
        operation.state === "cancelled" && "border-surface-border opacity-80",
        ["failed", "stale"].includes(operation.state) &&
          "border-error/40 bg-error-light/40",
        !terminal && operation.state !== "failed" && "border-surface-border",
      )}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
          {operation.state === "succeeded" ? (
            <Check className="h-4 w-4" />
          ) : operation.state === "cancelled" ? (
            <X className="h-4 w-4 text-ink-muted" />
          ) : ["failed", "stale"].includes(operation.state) ? (
            <AlertTriangle className="h-4 w-4 text-error" />
          ) : (
            <Icon className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="truncate text-sm font-medium text-ink-heading">
              {name}
            </span>
            <span className="text-2xs uppercase tracking-wider text-ink-label">
              {t(`playbook.operation.${change}`)}
            </span>
            {typeof operation.preview.next_version === "number" ? (
              <span className="rounded-full bg-surface-muted px-1.5 text-2xs text-ink-label">
                v{operation.preview.next_version}
              </span>
            ) : null}
          </div>
          {content ? (
            <div className="relative mt-2">
              <div
                ref={measure}
                className={cn(
                  "whitespace-pre-wrap break-words font-mono text-xs leading-snug text-ink-body",
                  expanded ? "max-h-48 overflow-y-auto" : "line-clamp-4",
                )}
              >
                {content}
              </div>
              {overflowing || expanded ? (
                <button
                  type="button"
                  onClick={() => setExpanded((value) => !value)}
                  className="mt-1 text-2xs font-medium text-brand hover:underline"
                >
                  {expanded
                    ? t("playbook.collapseEditor")
                    : t("playbook.expandEditor")}
                </button>
              ) : null}
            </div>
          ) : null}
          {operation.state === "succeeded" ? (
            <button
              type="button"
              disabled={!definitionId || !onOpenPlaybook}
              onClick={() => definitionId && onOpenPlaybook?.(definitionId)}
              className="mt-2 text-xs font-medium text-success hover:underline disabled:no-underline"
            >
              {t("playbook.operation.succeeded")}
            </button>
          ) : null}
          {operation.state === "cancelled" ? (
            <p className="mt-2 text-xs text-ink-meta">
              {t("playbook.operation.cancelled")}
            </p>
          ) : null}
          {["failed", "stale"].includes(operation.state) ? (
            <p className="mt-2 text-xs text-error">
              {operation.error_message ?? t("playbook.operation.failed")}
            </p>
          ) : null}
        </div>
      </div>
      {!terminal ? (
        <div className="flex items-center justify-end gap-2 border-t border-surface-border px-4 py-2">
          {canCancel ? (
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={onCancel}
              className="inline-flex h-7 items-center rounded-md border border-surface-border px-3 text-xs font-medium text-ink-body hover:bg-surface-2 disabled:opacity-50"
            >
              {busy === "cancel" ? (
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              ) : null}
              {t("common.cancel")}
            </button>
          ) : null}
          <button
            type="button"
            disabled={!canConfirm || Boolean(busy)}
            onClick={onConfirm}
            className="inline-flex h-7 items-center rounded-md bg-brand px-3 text-xs font-medium text-white hover:bg-brand-hover disabled:opacity-50"
          >
            {confirming ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {operation.state === "failed"
              ? t("common.retry")
              : t("common.confirm")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
