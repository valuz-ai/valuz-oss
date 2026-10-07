import { useState } from "react";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  EmptyState,
  SettingsRow,
  Spinner,
  Switch,
} from "@valuz/ui";
import {
  describeDshApiError,
  dshPluginsApi,
  useTranslation,
} from "@valuz/core";
import type { DshBundleInfo, DshChangeResult } from "@valuz/core";
import { t as translate } from "@valuz/shared/i18n";
import {
  bundleDescription,
  bundleTitle,
  changeTone,
  isIncompatibleBundle,
  isLockedBundle,
  isRemovableBundle,
  managementErrorLine,
} from "./dsh-helpers";

interface DshBundleListProps {
  bundles: readonly DshBundleInfo[];
  /** Re-read the list after a change that may have altered it. */
  onChanged: () => Promise<void> | void;
}

type Busy = { name: string; action: "toggle" | "remove" } | null;

/** Toast the outcome of a toggle / removal (dsh answers failures with HTTP 200). */
function reportChange(
  result: DshChangeResult,
  name: string,
  successKey: "enabledToast" | "disabledToast" | "removedToast",
): void {
  const tone = changeTone(result);
  if (tone === "error") {
    toast.error(
      translate("pluginSettings.dsh.bundles.changeFailed", {
        name,
        error: managementErrorLine(result.error),
      }),
    );
  } else if (result.application === "restart-required") {
    toast.warning(
      translate("pluginSettings.dsh.bundles.restartRequired", { name }),
    );
  } else if (result.application === "overridden") {
    toast.warning(translate("pluginSettings.dsh.bundles.overridden", { name }));
  } else {
    toast.success(translate(`pluginSettings.dsh.bundles.${successKey}`, { name }));
  }
}

const BundleRow = ({
  bundle,
  busy,
  onToggle,
  onRemove,
}: {
  bundle: DshBundleInfo;
  busy: Busy;
  onToggle: (bundle: DshBundleInfo, enabled: boolean) => void;
  onRemove: (bundle: DshBundleInfo) => void;
}) => {
  const { t, locale } = useTranslation();
  const title = bundleTitle(bundle, locale);
  const locked = isLockedBundle(bundle);
  const incompatible = !locked && isIncompatibleBundle(bundle);
  const mine = busy?.name === bundle.name ? busy.action : null;
  const anyBusy = busy !== null;

  return (
    <div data-bundle={bundle.name}>
      <SettingsRow
        className="px-0"
        label={title}
        desc={bundleDescription(bundle, locale)}
      >
        <div className="flex items-center gap-2">
          {title !== bundle.name ? (
            <span className="max-w-48 truncate font-mono text-2xs text-ink-meta">
              {bundle.name}
            </span>
          ) : null}
          {bundle.version ? (
            <span className="tabular text-xs text-ink-meta">
              v{bundle.version}
            </span>
          ) : null}
          {locked ? (
            <Badge
              variant="metaNeutral"
              title={t("pluginSettings.dsh.bundles.builtinHint")}
            >
              {t("pluginSettings.dsh.bundles.builtin")}
            </Badge>
          ) : null}
          {incompatible ? (
            <Badge
              variant="metaNeutral"
              title={t("pluginSettings.dsh.bundles.incompatibleHint")}
            >
              {t("pluginSettings.dsh.bundles.incompatible")}
            </Badge>
          ) : null}
          {!locked && !incompatible && bundle.optional ? (
            <Badge
              variant="metaOutline"
              title={t("pluginSettings.dsh.bundles.optionalHint")}
            >
              {t("pluginSettings.dsh.bundles.optional")}
            </Badge>
          ) : null}
          {bundle.error ? (
            <Badge variant="error">{t("pluginSettings.dsh.bundles.broken")}</Badge>
          ) : null}
          {mine ? <Spinner className="text-ink-meta" /> : null}
          {isRemovableBundle(bundle) ? (
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`${t("pluginSettings.dsh.bundles.remove")} ${title}`}
              disabled={anyBusy}
              onClick={() => onRemove(bundle)}
            >
              <Trash2 />
            </Button>
          ) : null}
          <Switch
            size="sm"
            checked={bundle.enabled}
            disabled={locked || incompatible || anyBusy}
            aria-label={t("pluginSettings.dsh.bundles.toggleLabel", {
              name: title,
            })}
            onCheckedChange={(next) => onToggle(bundle, next)}
          />
        </div>
      </SettingsRow>
      {bundle.error ? (
        <p className="-mt-1 mb-2 break-words text-xs text-error-text">
          {managementErrorLine(bundle.error)}
        </p>
      ) : null}
    </div>
  );
};

/**
 * The bundles dsh reports for its managed profile. Built-in bundles (Valuz's
 * own and the dsh installation's management bundles) are locked: no toggle, no
 * removal. Everything else goes through dsh's ``setBundleEnabled`` /
 * ``removeBundle``, whose result decides what the user is told.
 */
export const DshBundleList = ({ bundles, onChanged }: DshBundleListProps) => {
  const { t, locale } = useTranslation();
  const [busy, setBusy] = useState<Busy>(null);
  const [removeTarget, setRemoveTarget] = useState<DshBundleInfo | null>(null);

  const toggle = async (bundle: DshBundleInfo, enabled: boolean) => {
    const label = bundleTitle(bundle, locale);
    setBusy({ name: bundle.name, action: "toggle" });
    try {
      const result = await dshPluginsApi.setBundleEnabled(bundle.name, enabled);
      reportChange(result, label, enabled ? "enabledToast" : "disabledToast");
    } catch (error) {
      toast.error(
        translate("pluginSettings.dsh.bundles.changeFailed", {
          name: label,
          error: describeDshApiError(error).message,
        }),
      );
    } finally {
      // Re-read either way: a failed toggle must snap the Switch back.
      await onChanged();
      setBusy(null);
    }
  };

  const confirmRemove = async () => {
    const bundle = removeTarget;
    if (!bundle) return;
    const label = bundleTitle(bundle, locale);
    setBusy({ name: bundle.name, action: "remove" });
    try {
      const result = await dshPluginsApi.removeBundle(bundle.name);
      reportChange(result, label, "removedToast");
    } catch (error) {
      toast.error(
        translate("pluginSettings.dsh.bundles.changeFailed", {
          name: label,
          error: describeDshApiError(error).message,
        }),
      );
    } finally {
      setRemoveTarget(null);
      await onChanged();
      setBusy(null);
    }
  };

  return (
    <>
      {bundles.length === 0 ? (
        <EmptyState message={t("pluginSettings.dsh.bundles.empty")} />
      ) : (
        <Card className="rounded-xl shadow-xs">
          <CardContent className="divide-y divide-surface-border py-1">
            {bundles.map((bundle) => (
              <BundleRow
                key={bundle.name}
                bundle={bundle}
                busy={busy}
                onToggle={(b, next) => void toggle(b, next)}
                onRemove={setRemoveTarget}
              />
            ))}
          </CardContent>
        </Card>
      )}
      <DeleteConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(open) => {
          if (!open && busy?.action !== "remove") setRemoveTarget(null);
        }}
        title={t("pluginSettings.dsh.bundles.removeTitle", {
          name: removeTarget ? bundleTitle(removeTarget, locale) : "",
        })}
        description={t("pluginSettings.dsh.bundles.removeDesc", {
          package: removeTarget?.name ?? "",
        })}
        confirmLabel={t("pluginSettings.dsh.bundles.remove")}
        loading={busy?.action === "remove"}
        onConfirm={() => void confirmRemove()}
      />
    </>
  );
};
