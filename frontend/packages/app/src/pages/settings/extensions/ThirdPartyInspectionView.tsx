import type { ReactNode } from "react";
import { Badge } from "@valuz/ui";
import { getLocale } from "@valuz/shared/i18n";
import { useTranslation } from "@valuz/core";
import type { LocalizedText, ThirdPartyInspection } from "@valuz/core";
import { formatBytes, localizedText } from "./third-party-helpers";
import { Notice } from "./Notice";

export type InstallMode = "file" | "url" | "dev";

const asString = (value: unknown): string =>
  typeof value === "string" ? value : "";

const Row = ({ label, children }: { label: string; children: ReactNode }) => (
  <>
    <dt className="text-ink-meta">{label}</dt>
    <dd className="min-w-0 text-ink-body">{children}</dd>
  </>
);

/**
 * What ``POST /inspect`` found, laid out for the install confirmation: the
 * identity of the package, what it asks for (permissions the user would newly
 * grant and requirements it cannot meet are highlighted) and the validation
 * errors / warnings. Errors block the install; the dialog owns that.
 */
export const ThirdPartyInspectionView = ({
  inspection,
  mode,
  source,
}: {
  inspection: ThirdPartyInspection;
  mode: InstallMode;
  /** The path or URL the user entered. */
  source: string;
}) => {
  const { t } = useTranslation();
  const locale = getLocale();
  const manifest = inspection.manifest ?? {};
  const name = localizedText(
    manifest.name as LocalizedText | undefined,
    locale,
  );
  const id = asString(manifest.id);
  const version = asString(manifest.version);
  const publisher =
    manifest.publisher && typeof manifest.publisher === "object"
      ? asString((manifest.publisher as { name?: unknown }).name)
      : asString(manifest.publisher);
  const added = new Set(inspection.added_permissions);
  const unmet = new Set(inspection.unmet_requires);
  const size = formatBytes(inspection.size);

  return (
    <div className="space-y-3" data-slot="third-party-inspection">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink-heading">
          {name || id || t("extensions.thirdParty.confirm.unknownName")}
        </p>
        {inspection.has_backend ? (
          <Badge variant="metaOutline" className="mt-1">
            {t("extensions.thirdParty.confirm.hasBackend")}
          </Badge>
        ) : null}
      </div>

      <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
        <Row label={t("extensions.thirdParty.confirm.field.id")}>
          <span className="break-all font-mono">{id || "—"}</span>
        </Row>
        <Row label={t("extensions.thirdParty.confirm.field.version")}>
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono">{version || "—"}</span>
            {inspection.existing ? (
              <Badge variant="brand">
                {t("extensions.thirdParty.confirm.updatesFrom", {
                  version: inspection.existing.version,
                })}
              </Badge>
            ) : null}
          </span>
        </Row>
        <Row label={t("extensions.thirdParty.confirm.field.publisher")}>
          {publisher || "—"}
        </Row>
        <Row label={t("extensions.thirdParty.confirm.field.source")}>
          <span className="break-all">
            {`${t(`extensions.thirdParty.source.${mode}` as Parameters<typeof t>[0])} · ${source}`}
          </span>
        </Row>
        <Row label={t("extensions.thirdParty.confirm.field.sha256")}>
          <span className="break-all font-mono">
            {inspection.sha256 ?? "—"}
          </span>
        </Row>
        {size ? (
          <Row label={t("extensions.thirdParty.confirm.field.size")}>
            {size}
          </Row>
        ) : null}
        <Row label={t("extensions.thirdParty.confirm.field.permissions")}>
          {inspection.permissions.length === 0 ? (
            <span className="text-ink-meta">
              {t("extensions.thirdParty.confirm.noPermissions")}
            </span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {inspection.permissions.map((permission) => (
                <li key={permission} data-added={added.has(permission)}>
                  <Badge
                    variant={added.has(permission) ? "warning" : "metaOutline"}
                    className="font-mono"
                  >
                    <span>{permission}</span>
                    {added.has(permission) ? (
                      <span className="font-sans">
                        {t("extensions.thirdParty.confirm.newPermission")}
                      </span>
                    ) : null}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Row>
        <Row label={t("extensions.thirdParty.confirm.field.requires")}>
          {inspection.requires.length === 0 ? (
            <span className="text-ink-meta">
              {t("extensions.thirdParty.confirm.noRequires")}
            </span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {inspection.requires.map((requirement) => (
                <li key={requirement} data-unmet={unmet.has(requirement)}>
                  <Badge
                    variant={unmet.has(requirement) ? "error" : "metaOutline"}
                    className="font-mono"
                  >
                    <span>{requirement}</span>
                    {unmet.has(requirement) ? (
                      <span className="font-sans">
                        {t("extensions.thirdParty.confirm.unmet")}
                      </span>
                    ) : null}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Row>
      </dl>

      {inspection.errors.length > 0 ? (
        <Notice tone="error">
          <p className="font-medium">
            {t("extensions.thirdParty.confirm.errors")}
          </p>
          <ul className="list-disc space-y-0.5 pl-4">
            {inspection.errors.map((error) => (
              <li key={error} className="break-words">
                {error}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
      {inspection.warnings.length > 0 ? (
        <Notice tone="warning">
          <p className="font-medium">
            {t("extensions.thirdParty.confirm.warnings")}
          </p>
          <ul className="list-disc space-y-0.5 pl-4">
            {inspection.warnings.map((warning) => (
              <li key={warning} className="break-words">
                {warning}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
};
