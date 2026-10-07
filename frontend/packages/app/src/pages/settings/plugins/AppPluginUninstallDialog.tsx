import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import { appPluginsApi, useTranslation } from "@valuz/core";
import { errorDetail } from "./app-plugin-helpers";
import { Notice } from "./Notice";

/**
 * Uninstall confirmation with the "also delete data" choice. The plugin's
 * data (config, storage, app-plugins-data) is kept unless the box is ticked,
 * so a reinstall picks up where the user left off.
 */
export const AppPluginUninstallDialog = ({
  pluginId,
  name,
  onClose,
  onUninstalled,
}: {
  pluginId: string;
  name: string;
  onClose: () => void;
  onUninstalled: () => void;
}) => {
  const { t } = useTranslation();
  const [purge, setPurge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await appPluginsApi.uninstall(pluginId, purge);
      toast.success(t("pluginSettings.appPlugins.uninstall.done", { name }));
      onUninstalled();
      onClose();
    } catch (cause) {
      setError(
        t("pluginSettings.appPlugins.uninstall.failed", {
          name,
          error: errorDetail(cause),
        }),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      {/* Keep focus off the destructive button when the dialog opens. */}
      <DialogContent onOpenAutoFocus={(event) => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-error-text" />
            {t("pluginSettings.appPlugins.uninstall.title", { name })}
          </DialogTitle>
          <DialogDescription className="ml-7 text-left">
            {t("pluginSettings.appPlugins.uninstall.desc")}
          </DialogDescription>
        </DialogHeader>
        <div className="ml-7 flex items-start gap-2">
          <Checkbox
            id="app-plugin-purge"
            checked={purge}
            disabled={busy}
            onCheckedChange={(next) => setPurge(next === true)}
            className="mt-0.5"
          />
          <div className="space-y-0.5">
            <label
              htmlFor="app-plugin-purge"
              className="text-sm font-medium text-ink-heading"
            >
              {t("pluginSettings.appPlugins.uninstall.purge")}
            </label>
            <p className="text-xs text-ink-meta">
              {t("pluginSettings.appPlugins.uninstall.purgeHint")}
            </p>
          </div>
        </div>
        {error ? <Notice tone="error">{error}</Notice> : null}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={busy}
            loading={busy}
            onClick={() => void submit()}
          >
            {t("pluginSettings.appPlugins.uninstall.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
