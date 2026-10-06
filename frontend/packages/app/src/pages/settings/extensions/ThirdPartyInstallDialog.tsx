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
import { thirdPartyApi, useTranslation } from "@valuz/core";
import type {
  LocalizedText,
  ThirdPartyInspection,
  ThirdPartyPlugin,
  ThirdPartySourceSpec,
} from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { usePlatform } from "@valuz/app/platform";
import { Notice } from "./Notice";
import {
  ThirdPartyInspectionView,
  type InstallMode,
} from "./ThirdPartyInspectionView";
import {
  errorDetail,
  localizedText,
  serverErrorList,
} from "./third-party-helpers";

const sourceSpec = (mode: InstallMode, value: string): ThirdPartySourceSpec =>
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

const displayName = (plugin: ThirdPartyPlugin): string =>
  localizedText(plugin.name as LocalizedText, getLocale()) || plugin.id;

/**
 * Install from a zip / a folder (dev link) / a URL. The source is always
 * inspected first and the result shown for confirmation: nothing is written
 * until the user has read it and ticked "I understand this runs third-party
 * code with my permissions". Validation errors block the install.
 */
export const ThirdPartyInstallDialog = ({
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
  const [inspection, setInspection] = useState<ThirdPartyInspection | null>(
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
      ? t("extensions.thirdParty.form.urlLabel")
      : mode === "dev"
        ? t("extensions.thirdParty.form.devLabel")
        : t("extensions.thirdParty.form.fileLabel");
  const placeholder =
    mode === "url"
      ? t("extensions.thirdParty.form.urlPlaceholder")
      : mode === "dev"
        ? t("extensions.thirdParty.form.devPlaceholder")
        : t("extensions.thirdParty.form.filePlaceholder");

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
      setInspection(await thirdPartyApi.inspect(sourceSpec(mode, source)));
      setUnderstood(false);
    } catch (error) {
      const failed = toFailure(error);
      setFailure({
        ...failed,
        message: t("extensions.thirdParty.form.inspectFailed", {
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
        const { plugin } = await thirdPartyApi.devLink(source);
        toast.success(
          t("extensions.thirdParty.confirm.linked", {
            name: displayName(plugin),
          }),
        );
      } else {
        const result = await thirdPartyApi.install(
          sourceSpec(mode, source),
          inspection.sha256 ? { expected_sha256: inspection.sha256 } : {},
        );
        const name = displayName(result.plugin);
        toast.success(
          result.updated_from
            ? t("extensions.thirdParty.confirm.updated", {
                name,
                from: result.updated_from,
                to: result.plugin.version,
              })
            : t("extensions.thirdParty.confirm.installed", { name }),
        );
      }
      onInstalled();
      onClose();
    } catch (error) {
      const failed = toFailure(error);
      setFailure({
        ...failed,
        message: t("extensions.thirdParty.confirm.failed", {
          error: failed.message,
        }),
      });
    } finally {
      setInstalling(false);
    }
  };

  const confirmLabel =
    mode === "dev"
      ? t("extensions.thirdParty.confirm.link")
      : updating
        ? t("extensions.thirdParty.confirm.update")
        : t("extensions.thirdParty.confirm.install");

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
              ? t("extensions.thirdParty.confirm.titleDev")
              : t("extensions.thirdParty.confirm.title")}
          </DialogTitle>
          <DialogDescription>
            {inspection
              ? t("extensions.thirdParty.confirm.desc")
              : mode === "dev"
                ? t("extensions.thirdParty.form.devHint")
                : t("extensions.thirdParty.desc")}
          </DialogDescription>
        </DialogHeader>

        {inspection ? (
          <div className="space-y-4">
            <ThirdPartyInspectionView
              inspection={inspection}
              mode={mode}
              source={source}
            />
            {mode === "dev" ? (
              <p className="text-xs text-ink-meta">
                {t("extensions.thirdParty.form.devHint")}
              </p>
            ) : null}
            <div className="flex items-start gap-2">
              <Checkbox
                id="third-party-understood"
                checked={understood}
                disabled={blocked || installing}
                onCheckedChange={(next) => setUnderstood(next === true)}
                className="mt-0.5"
              />
              <label
                htmlFor="third-party-understood"
                className="text-xs leading-relaxed text-ink-body"
              >
                {t("extensions.thirdParty.confirm.risk")}
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
                {t("extensions.thirdParty.confirm.back")}
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
            <DialogField label={label} htmlFor="third-party-source">
              <div className="flex items-center gap-2">
                <DialogInput
                  id="third-party-source"
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
                    {t("extensions.thirdParty.form.browse")}
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
                  ? t("extensions.thirdParty.form.inspecting")
                  : t("extensions.thirdParty.form.inspect")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
};
