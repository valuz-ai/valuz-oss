import { memo, useState } from "react";
import {
  AlertTriangle,
  Archive,
  BookOpenText,
  Check,
  CircleCheck,
  Maximize2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

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
  metadata: Pencil,
  status: CircleCheck,
  retire: Archive,
  delete: Trash2,
} as const;

export const PlaybookOperationCard = memo(function PlaybookOperationCard({
  operation,
  busy,
  onConfirm,
  onCancel,
  onOpenPlaybook,
}: PlaybookOperationCardProps) {
  const { t } = useI18n();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const change = String(operation.preview.change ?? "update") as
    | "create"
    | "update"
    | "metadata"
    | "status"
    | "retire"
    | "delete";
  const name = String(operation.preview.name ?? t("playbook.title"));
  const content =
    typeof operation.preview.content === "string"
      ? operation.preview.content
      : null;
  const Icon = changeIcon[change] ?? BookOpenText;
  const nextStatus =
    typeof operation.preview.status === "string"
      ? operation.preview.status
      : null;
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

  return (
    <>
      <div
        data-slot="playbook-operation-card"
        className={cn(
          "rounded-lg border bg-surface-soft transition-colors",
          operation.state === "succeeded" && "border-success/40 bg-success/5",
          operation.state === "cancelled" &&
            "border-surface-border bg-surface-2 opacity-80",
          ["failed", "stale"].includes(operation.state) &&
            "border-error/40 bg-error-light/40",
          !terminal && operation.state !== "failed" && "border-surface-border",
        )}
      >
        <div className="flex items-start gap-3 px-4 py-3">
          <div
            className={cn(
              "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand",
              change === "delete" && "bg-error-light text-error-text",
            )}
          >
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
              {nextStatus ? (
                <span className="rounded-full bg-surface-muted px-1.5 text-2xs text-ink-label">
                  {t(`playbook.status.${nextStatus}`)}
                </span>
              ) : null}
            </div>
            {content ? (
              <div
                data-slot="playbook-prompt-preview"
                className="relative mt-2 pr-9"
              >
                <div className="line-clamp-4 whitespace-pre-wrap break-words text-xs leading-snug text-ink-body">
                  {content}
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => setDetailsOpen(true)}
                  className="absolute right-0 top-0"
                  title={t("playbook.promptLabel")}
                  aria-label={t("playbook.promptLabel")}
                >
                  <Maximize2 />
                </Button>
              </div>
            ) : null}
            {operation.state === "succeeded" && change !== "delete" ? (
              <button
                type="button"
                disabled={!definitionId || !onOpenPlaybook}
                onClick={() => definitionId && onOpenPlaybook?.(definitionId)}
                className="mt-2 text-xs font-medium text-success hover:underline disabled:no-underline"
              >
                {t("playbook.operation.succeeded")}
              </button>
            ) : null}
            {operation.state === "succeeded" && change === "delete" ? (
              <p className="mt-2 text-xs font-medium text-success">
                {t("playbook.operation.deleted")}
              </p>
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
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={Boolean(busy)}
                loading={busy === "cancel"}
                onClick={onCancel}
              >
                {t("common.cancel")}
              </Button>
            ) : null}
            <Button
              type="button"
              variant={change === "delete" ? "destructive" : "default"}
              size="sm"
              disabled={!canConfirm || Boolean(busy)}
              loading={confirming}
              onClick={onConfirm}
            >
              {operation.state === "failed"
                ? t("common.retry")
                : t("common.confirm")}
            </Button>
          </div>
        ) : null}
      </div>

      {content ? (
        <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
          <DialogContent className="flex max-h-[88vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl">
            <DialogHeader className="border-b border-surface-border px-5 pb-4 pt-5 pr-12">
              <DialogTitle>{name}</DialogTitle>
              <DialogDescription>{t("playbook.promptLabel")}</DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
              <MarkdownContent
                content={content}
                showCitationSources={false}
                className="text-sm leading-6 text-ink-body [&_h1]:mb-3 [&_h1]:mt-0 [&_h1]:text-xl [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-lg [&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:text-base [&_li]:my-1 [&_ol]:my-3 [&_p]:my-2 [&_table]:text-xs [&_ul]:my-3"
              />
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
});
