import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  LoadingState,
  SettingsRow,
  Spinner,
  Switch,
} from "@valuz/ui";
import { builtinPluginsApi, useTranslation } from "@valuz/core";
import type { BuiltinPluginRow } from "@valuz/core";
import { t as translate } from "@valuz/shared/i18n";
import {
  BACKEND_STATUS_LABEL,
  BACKEND_STATUS_VARIANT,
  dependentsOf,
  isLocked,
  isPendingRestart,
} from "./backend-helpers";
import { Notice } from "./Notice";
import { SubHeading } from "./SubHeading";
import { useBuiltinPlugins } from "./use-builtin-plugins";

/** The server's ``detail`` (``ApiError.message``), or whatever was thrown. */
const errorDetail = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

const BackendRow = ({
  row,
  editable,
  busy,
  anyBusy,
  pending,
  onToggle,
}: {
  row: BuiltinPluginRow;
  editable: boolean;
  busy: boolean;
  anyBusy: boolean;
  pending: boolean;
  onToggle: (row: BuiltinPluginRow, enabled: boolean) => void;
}) => {
  const { t } = useTranslation();

  const meta = [
    row.provides.length > 0
      ? t("pluginSettings.backend.meta.provides", {
          list: row.provides.join(", "),
        })
      : null,
    row.needs.length > 0
      ? t("pluginSettings.backend.meta.needs", { list: row.needs.join(", ") })
      : null,
    row.entitlement
      ? t("pluginSettings.backend.meta.entitlement", { key: row.entitlement })
      : null,
  ].filter((part): part is string => part !== null);

  const dependents = dependentsOf(row);
  const locked = isLocked(row);

  const statusLabel =
    row.status in BACKEND_STATUS_LABEL
      ? t(`pluginSettings.backend.status.${BACKEND_STATUS_LABEL[row.status]}`)
      : row.status;

  return (
    <div data-builtin-backend-plugin={row.id}>
      <SettingsRow
        className="px-0"
        label={row.id}
        desc={meta.length > 0 ? meta.join(" · ") : undefined}
      >
        <div className="flex items-center gap-2">
          <Badge variant={BACKEND_STATUS_VARIANT[row.status] ?? "metaNeutral"}>
            {statusLabel}
          </Badge>
          {row.required ? (
            <Badge
              variant="metaNeutral"
              title={t("pluginSettings.backend.requiredHint")}
            >
              {t("pluginSettings.backend.required")}
            </Badge>
          ) : dependents.length > 0 ? (
            <Badge
              variant="metaNeutral"
              title={t("pluginSettings.backend.requiredByHint", {
                ids: dependents.join(", "),
              })}
            >
              {t("pluginSettings.backend.requiredBy", {
                ids: dependents.join(", "),
              })}
            </Badge>
          ) : null}
          {busy ? <Spinner className="text-ink-meta" /> : null}
          <Switch
            size="sm"
            // A required plugin — or one a required plugin needs — is locked on
            // whatever the persisted desire says.
            checked={locked ? true : row.desiredEnabled}
            disabled={locked || !editable || anyBusy}
            aria-label={t("pluginSettings.backend.toggleLabel", { id: row.id })}
            onCheckedChange={(next) => onToggle(row, next)}
          />
        </div>
      </SettingsRow>
      {row.status === "failed" && row.error ? (
        <p className="-mt-1 mb-2 break-words text-xs text-error-text">
          {t("pluginSettings.valuz.failedReason", { error: row.error })}
        </p>
      ) : null}
      {pending ? (
        <p className="-mt-1 mb-2 text-xs text-warning-text">
          {row.desiredEnabled
            ? t("pluginSettings.backend.pendingEnable")
            : t("pluginSettings.backend.pendingDisable")}
        </p>
      ) : null}
    </div>
  );
};

/**
 * The backend half of 「Valuz 扩展」: the plugins the running backend composed.
 * A toggle is recorded and applies at the next start, so a changed row says so
 * and the block carries one "restart to apply" hint. A bare OSS app (no plugin
 * host) and a cloud deployment (operator-managed) are states, not errors.
 */
export const BuiltinPluginsBlock = () => {
  const { t } = useTranslation();
  const { state, reload, markEnabled } = useBuiltinPlugins();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set());

  const toggle = async (row: BuiltinPluginRow, enabled: boolean) => {
    setBusyId(row.id);
    try {
      await builtinPluginsApi.setBuiltinPluginEnabled(row.id, enabled);
      markEnabled(row.id, enabled);
      setToggled((prev) => new Set(prev).add(row.id));
    } catch (error) {
      toast.error(
        translate("pluginSettings.backend.toggleFailed", {
          id: row.id,
          error: errorDetail(error),
        }),
      );
      // The refusal usually means what is on screen is stale (403: the
      // deployment is operator-managed; 404 / 409: the plugin changed).
      void reload({ silent: true });
    } finally {
      setBusyId(null);
    }
  };

  let body: ReactNode;
  if (state.phase === "loading") {
    body = <LoadingState variant="section" />;
  } else if (state.phase === "error") {
    body = (
      <EmptyState
        title={t("pluginSettings.backend.loadFailed")}
        description={state.message}
        action={
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            {t("common.retry")}
          </Button>
        }
      />
    );
  } else if (!state.data.composed) {
    body = <EmptyState message={t("pluginSettings.backend.notComposed")} />;
  } else if (state.data.plugins.length === 0) {
    body = <EmptyState message={t("pluginSettings.backend.empty")} />;
  } else {
    const { plugins, editable } = state.data;
    const anyPending = plugins.some((row) => isPendingRestart(row, toggled));
    body = (
      <>
        {!editable ? (
          <p className="mb-3 text-xs text-ink-meta">
            {t("pluginSettings.backend.readOnly")}
          </p>
        ) : null}
        {anyPending ? (
          <div className="mb-3">
            <Notice tone="warning">
              <p>{t("pluginSettings.backend.restartHint")}</p>
            </Notice>
          </div>
        ) : null}
        <Card className="rounded-xl shadow-xs">
          <CardContent className="divide-y divide-surface-border py-1">
            {plugins.map((row) => (
              <BackendRow
                key={row.id}
                row={row}
                editable={editable}
                busy={busyId === row.id}
                anyBusy={busyId !== null}
                pending={isPendingRestart(row, toggled)}
                onToggle={(r, next) => void toggle(r, next)}
              />
            ))}
          </CardContent>
        </Card>
      </>
    );
  }

  return (
    <section aria-labelledby="builtin-backend-plugins-heading" className="mt-6">
      <SubHeading
        id="builtin-backend-plugins-heading"
        title={t("pluginSettings.backend.title")}
        desc={t("pluginSettings.backend.desc")}
      />
      {body}
    </section>
  );
};
