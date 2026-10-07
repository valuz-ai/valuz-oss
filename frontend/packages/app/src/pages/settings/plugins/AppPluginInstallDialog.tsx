import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogField,
  DialogFooter,
  DialogHeader,
  DialogInput,
  DialogTitle,
  Spinner,
} from "@valuz/ui";
import { appPluginsApi, useTranslation } from "@valuz/core";
import type {
  LocalizedText,
  AppPluginInspection,
  AppPlugin,
  AppPluginSourceSpec,
} from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { usePlatform } from "@valuz/app/platform";
import { Notice } from "./Notice";
import {
  AppPluginInspectionView,
  type InstallMode,
} from "./AppPluginInspectionView";
import {
  errorDetail,
  localizedText,
  serverErrorList,
} from "./app-plugin-helpers";

const sourceSpec = (mode: InstallMode, value: string): AppPluginSourceSpec =>
  mode === "url" ? { url: value } : { source_path: value };

interface Failure {
  message: string;
  errors: string[];
}

const toFailure = (error: unknown): Failure => ({
  message: errorDetail(error),
  errors: serverErrorList(error),
});

const FailureNotice = ({ failure }: { failure: Failure }) => (
  <Notice tone="error">
    <p className="break-words">{failure.message}</p>
    {failure.errors.length > 0 ? (
      <ul className="list-disc space-y-0.5 pl-4">
        {failure.errors.map((error) => (
          <li key={error} className="break-words">
            {error}
          </li>
        ))}
      </ul>
    ) : null}
  </Notice>
);

const displayName = (plugin: AppPlugin): string =>
  localizedText(plugin.name as LocalizedText, getLocale()) || plugin.id;

/**
 * Install from a zip / a folder (dev link) / a URL. The source is always
 * inspected first and the result shown for confirmation: nothing is written
 * until the user has read it and ticked "I understand this runs third-party
 * code with my permissions". Validation errors block the install.
 */
export const AppPluginInstallDialog = ({
  mode,
  onClose,
  onInstalled,
}: {
  mode: InstallMode;
  onClose: () => void;
  /** Called after a successful install / link so the list can refresh. */
  onInstalled: () => void;
}) => {
  const { t } = useTranslation();
  const platform = usePlatform();
  const [value, setValue] = useState("");
  const [inspecting, setInspecting] = useState(false);
  const [inspection, setInspection] = useState<AppPluginInspection | null>(
    null,
  );
  const [understood, setUnderstood] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  const source = value.trim();
  const busy = inspecting || installing;
  const blocked = inspection ? inspection.errors.length > 0 : false;
  const updating = inspection?.existing != null;

  const label =
    mode === "url"
      ? t("pluginSettings.appPlugins.form.urlLabel")
      : mode === "dev"
        ? t("pluginSettings.appPlugins.form.devLabel")
        : t("pluginSettings.appPlugins.form.fileLabel");
  const placeholder =
    mode === "url"
      ? t("pluginSettings.appPlugins.form.urlPlaceholder")
      : mode === "dev"
        ? t("pluginSettings.appPlugins.form.devPlaceholder")
        : t("pluginSettings.appPlugins.form.filePlaceholder");

  const browse = async () => {
    const picked = await platform.selectDirectory();
    if (picked) setValue(picked);
  };

  const inspect = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!source || busy) return;
    setInspecting(true);
    setFailure(null);
    try {
      // The dev link inspects the directory the same way an install does.
      setInspection(await appPluginsApi.inspect(sourceSpec(mode, source)));
      setUnderstood(false);
    } catch (error) {
      const failed = toFailure(error);
      setFailure({
        ...failed,
        message: t("pluginSettings.appPlugins.form.inspectFailed", {
          error: failed.message,
        }),
      });
    } finally {
      setInspecting(false);
    }
  };

  const confirm = async () => {
    if (!inspection || blocked || !understood || installing) return;
    setInstalling(true);
    setFailure(null);
    try {
      if (mode === "dev") {
        const { plugin } = await appPluginsApi.devLink(source);
        toast.success(
          t("pluginSettings.appPlugins.confirm.linked", {
            name: displayName(plugin),
          }),
        );
      } else {
        const result = await appPluginsApi.install(
          sourceSpec(mode, source),
          inspection.sha256 ? { expected_sha256: inspection.sha256 } : {},
        );
        const name = displayName(result.plugin);
        toast.success(
          result.updated_from
            ? t("pluginSettings.appPlugins.confirm.updated", {
                name,
                from: result.updated_from,
                to: result.plugin.version,
              })
            : t("pluginSettings.appPlugins.confirm.installed", { name }),
        );
      }
      onInstalled();
      onClose();
    } catch (error) {
      const failed = toFailure(error);
      setFailure({
        ...failed,
        message: t("pluginSettings.appPlugins.confirm.failed", {
          error: failed.message,
        }),
      });
    } finally {
      setInstalling(false);
    }
  };

  const confirmLabel =
    mode === "dev"
      ? t("pluginSettings.appPlugins.confirm.link")
      : updating
        ? t("pluginSettings.appPlugins.confirm.update")
        : t("pluginSettings.appPlugins.confirm.install");

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !installing) onClose();
      }}
    >
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {mode === "dev"
              ? t("pluginSettings.appPlugins.confirm.titleDev")
              : t("pluginSettings.appPlugins.confirm.title")}
          </DialogTitle>
          <DialogDescription>
            {inspection
              ? t("pluginSettings.appPlugins.confirm.desc")
              : mode === "dev"
                ? t("pluginSettings.appPlugins.form.devHint")
                : t("pluginSettings.appPlugins.desc")}
          </DialogDescription>
        </DialogHeader>

        {inspection ? (
          <div className="space-y-4">
            <AppPluginInspectionView
              inspection={inspection}
              mode={mode}
              source={source}
            />
            {mode === "dev" ? (
              <p className="text-xs text-ink-meta">
                {t("pluginSettings.appPlugins.form.devHint")}
              </p>
            ) : null}
            <div className="flex items-start gap-2">
              <Checkbox
                id="app-plugin-understood"
                checked={understood}
                disabled={blocked || installing}
                onCheckedChange={(next) => setUnderstood(next === true)}
                className="mt-0.5"
              />
              <label
                htmlFor="app-plugin-understood"
                className="text-xs leading-relaxed text-ink-body"
              >
                {t("pluginSettings.appPlugins.confirm.risk")}
              </label>
            </div>
            {failure ? <FailureNotice failure={failure} /> : null}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={installing}
                onClick={() => {
                  setInspection(null);
                  setFailure(null);
                }}
              >
                {t("pluginSettings.appPlugins.confirm.back")}
              </Button>
              <Button
                disabled={blocked || !understood || installing}
                loading={installing}
                onClick={() => void confirm()}
              >
                {confirmLabel}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={(e) => void inspect(e)}>
            <DialogField label={label} htmlFor="app-plugin-source">
              <div className="flex items-center gap-2">
                <DialogInput
                  id="app-plugin-source"
                  value={value}
                  placeholder={placeholder}
                  autoFocus
                  spellCheck={false}
                  onChange={(event) => setValue(event.target.value)}
                />
                {mode !== "url" && platform.isElectron ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void browse()}
                  >
                    {t("pluginSettings.appPlugins.form.browse")}
                  </Button>
                ) : null}
              </div>
            </DialogField>
            {failure ? <FailureNotice failure={failure} /> : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={!source || busy}>
                {inspecting ? <Spinner /> : null}
                {inspecting
                  ? t("pluginSettings.appPlugins.form.inspecting")
                  : t("pluginSettings.appPlugins.form.inspect")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
};
