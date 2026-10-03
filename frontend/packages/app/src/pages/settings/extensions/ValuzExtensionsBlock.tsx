import { useSyncExternalStore } from "react";
import { Badge, Card, CardContent, EmptyState } from "@valuz/ui";
import { pluginHost, useTranslation } from "@valuz/core";
import type { PluginRecord, PluginStatus } from "@valuz/core";
import { BackendExtensionsBlock } from "./BackendExtensionsBlock";
import { SubHeading } from "./SubHeading";

const STATUS_VARIANT: Record<
  PluginStatus,
  "success" | "error" | "brand" | "metaNeutral"
> = {
  active: "success",
  failed: "error",
  loading: "brand",
  disposed: "metaNeutral",
  "backend-disabled": "metaNeutral",
};

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error ?? "");

const ValuzExtensionRow = ({ record }: { record: PluginRecord }) => {
  const { t } = useTranslation();
  const legacy = record.legacy.length;
  return (
    <li className="py-3" data-extension-id={record.id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-medium text-ink-heading">
          {record.id}
        </span>
        <Badge variant={STATUS_VARIANT[record.status]}>
          {t(`extensions.valuz.status.${record.status}`)}
        </Badge>
        {legacy > 0 ? (
          <Badge
            variant="metaOutline"
            title={t("extensions.valuz.legacyHint", {
              names: record.legacy.join(", "),
            })}
          >
            {t("extensions.valuz.legacyCount", { count: legacy })}
          </Badge>
        ) : null}
      </div>
      {record.status === "failed" ? (
        <p className="mt-1 break-words text-xs text-error-text">
          {t("extensions.valuz.failedReason", {
            error: errorText(record.error),
          })}
        </p>
      ) : null}
    </li>
  );
};

/** Live, read-only list of the extensions the plugin host has loaded. */
export const ValuzExtensionsBlock = () => {
  const { t } = useTranslation();
  // ``list()`` returns the same array until a plugin changes state.
  const records = useSyncExternalStore(
    pluginHost.subscribe,
    pluginHost.list,
    pluginHost.list,
  );

  return (
    <section aria-labelledby="extensions-valuz-heading" className="mb-8">
      <div className="mb-4">
        <h3
          id="extensions-valuz-heading"
          className="text-sm font-semibold text-ink-heading"
        >
          {t("extensions.valuz.title")}
        </h3>
        <p className="text-xs text-ink-body">{t("extensions.valuz.desc")}</p>
      </div>
      <SubHeading
        id="extensions-frontend-heading"
        title={t("extensions.valuz.frontend.title")}
        desc={t("extensions.valuz.frontend.desc")}
      />
      {records.length === 0 ? (
        <EmptyState message={t("extensions.valuz.empty")} />
      ) : (
        <Card className="rounded-xl shadow-xs">
          <CardContent className="py-1">
            <ul className="divide-y divide-surface-border">
              {records.map((record) => (
                <ValuzExtensionRow key={record.id} record={record} />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      <BackendExtensionsBlock />
    </section>
  );
};
