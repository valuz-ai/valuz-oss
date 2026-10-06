import { memo, type ReactNode } from "react";
import { AlertTriangle, Check, Puzzle, X } from "lucide-react";
import { Badge, Button, cn } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import {
  extensionOperationView,
  type ExtensionAction,
  type ExtensionOperationLike,
} from "./extension-operation-view";

export interface ExtensionOperationCardProps {
  operation: ExtensionOperationLike & {
    state: string;
    error_message?: string | null;
  };
  /** The tool envelope's ``action`` — the fallback when the record has none. */
  action?: string | null;
  busy?: "confirm" | "cancel" | "request_changes" | null;
  onConfirm: () => void;
  onCancel: () => void;
}

const DISMISSED_STATES = ["cancelled", "expired", "superseded"];
const SCOPES = ["personal", "org", "global"];
const TERMINAL_STATES = [...DISMISSED_STATES, "succeeded", "stale"];

const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <>
    <dt className="text-ink-meta">{label}</dt>
    <dd className="min-w-0 text-ink-body">{children}</dd>
  </>
);

/**
 * The confirmation card of an ``extension_manager`` operation (``dev_link`` /
 * ``install`` / ``uninstall`` / ``publish``). Same shell and state handling as
 * the playbook / skill cards; the body shows what the user is approving: the
 * plugin's identity, the permissions it asks for (newly added ones
 * highlighted), its requirements (unmet ones highlighted) and, for a publish,
 * where it goes.
 */
export const ExtensionOperationCard = memo(function ExtensionOperationCard({
  operation,
  action: toolAction,
  busy,
  onConfirm,
  onCancel,
}: ExtensionOperationCardProps) {
  const { t } = useTranslation();
  const view = extensionOperationView(operation, toolAction);
  const action: ExtensionAction = view.action;
  const added = new Set(view.addedPermissions);
  const unmet = new Set(view.unmetRequires);

  const terminal = TERMINAL_STATES.includes(operation.state);
  const dismissed = DISMISSED_STATES.includes(operation.state);
  const failed = ["failed", "stale"].includes(operation.state);
  const confirming = busy === "confirm" || operation.state === "executing";
  const canCancel =
    operation.state === "proposed" ||
    operation.state === "awaiting_confirmation";
  const canConfirm = canCancel || operation.state === "failed";
  const destructive = action === "uninstall";

  const stateKey = (state: string) =>
    `toolCall.extensionManager.state.${state}` as Parameters<typeof t>[0];

  return (
    <div
      data-slot="extension-operation-card"
      data-action={action}
      className={cn(
        "rounded-lg border bg-surface-soft transition-colors",
        operation.state === "succeeded" && "border-success/40 bg-success/5",
        dismissed && "border-surface-border bg-surface-2 opacity-80",
        failed && "border-error/40 bg-error-light/40",
        !terminal && !failed && "border-surface-border",
      )}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <div
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand",
            destructive && "bg-error-light text-error-text",
          )}
        >
          {operation.state === "succeeded" ? (
            <Check className="h-4 w-4" />
          ) : dismissed ? (
            <X className="h-4 w-4 text-ink-muted" />
          ) : failed ? (
            <AlertTriangle className="h-4 w-4 text-error" />
          ) : (
            <Puzzle className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-medium text-ink-heading">
              {t(
                `toolCall.extensionManager.title.${action}` as Parameters<
                  typeof t
                >[0],
              )}
            </span>
            {view.name ? (
              <span className="truncate text-sm text-ink-body">{view.name}</span>
            ) : null}
            {view.version ? (
              <span className="rounded-full bg-surface-muted px-1.5 text-2xs text-ink-label">
                v{view.version}
              </span>
            ) : null}
            {view.existingVersion ? (
              <Badge variant="brand">
                {t("toolCall.extensionManager.updatesFrom", {
                  version: view.existingVersion,
                })}
              </Badge>
            ) : null}
            {view.hasBackend ? (
              <Badge variant="metaOutline">
                {t("toolCall.extensionManager.hasBackend")}
              </Badge>
            ) : null}
          </div>

          <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
            {view.id ? (
              <Field label={t("toolCall.extensionManager.field.id")}>
                <span className="break-all font-mono">{view.id}</span>
              </Field>
            ) : null}
            {view.publisher ? (
              <Field label={t("toolCall.extensionManager.field.publisher")}>
                {view.publisher}
              </Field>
            ) : null}
            {view.source ? (
              <Field label={t("toolCall.extensionManager.field.source")}>
                <span className="break-all">{view.source}</span>
              </Field>
            ) : null}
            {view.path ? (
              <Field label={t("toolCall.extensionManager.field.path")}>
                <span className="break-all font-mono">{view.path}</span>
              </Field>
            ) : null}
            {view.sha256 ? (
              <Field label={t("toolCall.extensionManager.field.sha256")}>
                <span className="break-all font-mono">{view.sha256}</span>
              </Field>
            ) : null}
            {action !== "uninstall" ? (
              <Field label={t("toolCall.extensionManager.field.permissions")}>
                {view.permissions.length === 0 ? (
                  <span className="text-ink-meta">
                    {t("toolCall.extensionManager.noPermissions")}
                  </span>
                ) : (
                  <ul className="flex flex-wrap gap-1.5">
                    {view.permissions.map((permission) => (
                      <li key={permission} data-added={added.has(permission)}>
                        <Badge
                          variant={
                            added.has(permission) ? "warning" : "metaOutline"
                          }
                          className="font-mono"
                        >
                          <span>{permission}</span>
                          {added.has(permission) ? (
                            <span className="font-sans">
                              {t("toolCall.extensionManager.newPermission")}
                            </span>
                          ) : null}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </Field>
            ) : null}
            {view.requires.length > 0 ? (
              <Field label={t("toolCall.extensionManager.field.requires")}>
                <ul className="flex flex-wrap gap-1.5">
                  {view.requires.map((requirement) => (
                    <li key={requirement} data-unmet={unmet.has(requirement)}>
                      <Badge
                        variant={
                          unmet.has(requirement) ? "error" : "metaOutline"
                        }
                        className="font-mono"
                      >
                        <span>{requirement}</span>
                        {unmet.has(requirement) ? (
                          <span className="font-sans">
                            {t("toolCall.extensionManager.unmet")}
                          </span>
                        ) : null}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </Field>
            ) : null}
            {action === "publish" && view.scope ? (
              <Field label={t("toolCall.extensionManager.field.scope")}>
                {SCOPES.includes(view.scope)
                  ? t(
                      `toolCall.extensionManager.scope.${view.scope}` as Parameters<
                        typeof t
                      >[0],
                    )
                  : view.scope}
              </Field>
            ) : null}
            {action === "publish" && view.targets.length > 0 ? (
              <Field label={t("toolCall.extensionManager.field.targets")}>
                <ul className="flex flex-wrap gap-1.5">
                  {view.targets.map((target) => (
                    <li key={target}>
                      <Badge variant="metaOutline" className="font-mono">
                        {target}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </Field>
            ) : null}
            {action === "publish" && view.notes ? (
              <Field label={t("toolCall.extensionManager.field.notes")}>
                <span className="whitespace-pre-wrap break-words">
                  {view.notes}
                </span>
              </Field>
            ) : null}
          </dl>

          {action === "uninstall" && view.purgeData !== null ? (
            <p className="text-xs text-ink-body">
              {view.purgeData
                ? t("toolCall.extensionManager.purgeData")
                : t("toolCall.extensionManager.keepData")}
            </p>
          ) : null}
          {!terminal && action === "dev_link" ? (
            <p className="text-xs text-ink-meta">
              {t("toolCall.extensionManager.devHint")}
            </p>
          ) : null}
          {!terminal && (action === "install" || action === "dev_link") ? (
            <p className="text-xs text-warning-text">
              {t("toolCall.extensionManager.risk")}
            </p>
          ) : null}
          {!terminal && action === "publish" ? (
            <p className="text-xs text-ink-meta">
              {t("toolCall.extensionManager.publishHint")}
            </p>
          ) : null}

          {operation.state === "succeeded" ? (
            <p className="text-xs font-medium text-success">
              {t("toolCall.extensionManager.succeeded")}
            </p>
          ) : null}
          {dismissed || operation.state === "stale" ? (
            <p className="text-xs text-ink-meta">
              {t(stateKey(operation.state))}
            </p>
          ) : null}
          {operation.state === "failed" ? (
            <p className="text-xs text-error">
              {operation.error_message ?? t(stateKey("failed"))}
            </p>
          ) : null}
        </div>
      </div>
      {!terminal ? (
        <div className="flex items-center justify-end gap-2 px-4 py-2">
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
            variant={destructive ? "destructive" : "default"}
            size="sm"
            disabled={!canConfirm || Boolean(busy)}
            loading={confirming}
            onClick={onConfirm}
          >
            {operation.state === "failed"
              ? t("common.retry")
              : t(
                  `toolCall.extensionManager.confirm.${action}` as Parameters<
                    typeof t
                  >[0],
                )}
          </Button>
        </div>
      ) : null}
    </div>
  );
});
