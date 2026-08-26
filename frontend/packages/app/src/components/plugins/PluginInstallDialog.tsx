import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  FileArchive,
  Info,
  Link2,
  Upload,
} from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogField,
  DialogFooter,
  DialogHeader,
  DialogInput,
  DialogTitle,
  SegmentedControl,
} from "@valuz/ui";
import type {
  AgentPluginInstallInput,
  AgentPluginInstallResult,
  AgentPluginOnConflict,
  AgentPluginPreview,
} from "@valuz/core";
import { ApiError, pluginsApi, useTranslation } from "@valuz/core";
import { PluginConflictDialog } from "./PluginConflictDialog";
import { PluginMembersList } from "./PluginMembersList";
import {
  PLUGIN_FORMAT_LABEL_KEYS,
  manifestString,
  pluginLocatorInput,
} from "./plugin-format";

type InstallMode = "zip" | "locator";

interface PluginInstallDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstalled: (result: AgentPluginInstallResult) => void;
}

/**
 * Library "安装插件" flow: pick a zip or a local path / URL → preview
 * (manifest, members, detected format, warnings, same-slug conflicts) →
 * conflict prompt when needed → install.
 */
export function PluginInstallDialog({
  open,
  onOpenChange,
  onInstalled,
}: PluginInstallDialogProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<InstallMode>("zip");
  const [file, setFile] = useState<File | null>(null);
  const [locator, setLocator] = useState("");
  const [preview, setPreview] = useState<AgentPluginPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setMode("zip");
    setFile(null);
    setLocator("");
    setPreview(null);
    setPreviewing(false);
    setInstalling(false);
    setConflictOpen(false);
  };

  const close = () => {
    reset();
    onOpenChange(false);
  };

  const buildInput = (
    onConflict?: AgentPluginOnConflict,
  ): AgentPluginInstallInput | null => {
    if (mode === "zip") {
      if (!file) return null;
      return { file, on_conflict: onConflict };
    }
    const value = locator.trim();
    if (!value) return null;
    return { ...pluginLocatorInput(value), on_conflict: onConflict };
  };

  const showError = (err: unknown, fallbackKey: string) => {
    if (err instanceof ApiError && err.i18nKey) {
      toast.error(
        t(err.i18nKey as Parameters<typeof t>[0], err.i18nParams as never),
      );
    } else if (err instanceof ApiError && err.message) {
      toast.error(err.message);
    } else {
      toast.error(t(fallbackKey as Parameters<typeof t>[0]));
    }
  };

  const handlePreview = async () => {
    const input = buildInput();
    if (!input) return;
    setPreviewing(true);
    try {
      setPreview(await pluginsApi.preview(input));
    } catch (err) {
      setPreview(null);
      showError(err, "plugin.previewFailed");
    } finally {
      setPreviewing(false);
    }
  };

  const doInstall = async (onConflict?: AgentPluginOnConflict) => {
    const input = buildInput(onConflict);
    if (!input) return;
    setInstalling(true);
    try {
      const result = await pluginsApi.install(input);
      const name =
        result.plugin?.name ?? manifestString(preview?.manifest, "name") ?? "";
      if (result.status === "already_installed") {
        toast.info(t("plugin.alreadyInstalled", { name }));
      } else if (result.status === "updated") {
        toast.success(t("plugin.updated", { name }));
      } else {
        toast.success(t("plugin.installed", { name }));
      }
      onInstalled(result);
      close();
    } catch (err) {
      showError(err, "plugin.installFailed");
    } finally {
      setInstalling(false);
      setConflictOpen(false);
    }
  };

  const handleInstall = () => {
    if (preview && preview.conflicts.length > 0) {
      setConflictOpen(true);
      return;
    }
    void doInstall();
  };

  const canPreview =
    !previewing &&
    !installing &&
    (mode === "zip" ? !!file : locator.trim().length > 0);
  const manifestName = manifestString(preview?.manifest, "name");
  const manifestVersion = manifestString(preview?.manifest, "version");
  const manifestDescription = manifestString(preview?.manifest, "description");

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next && (installing || previewing)) return;
          if (!next) close();
          else onOpenChange(true);
        }}
      >
        <DialogContent className="flex max-h-[88vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
          <DialogHeader className="border-b border-surface-border px-6 py-4 text-left">
            <DialogTitle>{t("plugin.install")}</DialogTitle>
            <DialogDescription>{t("plugin.installPathHelp")}</DialogDescription>
          </DialogHeader>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4">
            <SegmentedControl<InstallMode>
              value={mode}
              onValueChange={(next) => {
                setMode(next);
                setPreview(null);
              }}
              options={[
                {
                  value: "zip",
                  label: t("plugin.installFromZip"),
                  icon: Upload,
                },
                {
                  value: "locator",
                  label: t("plugin.installFromPath"),
                  icon: Link2,
                },
              ]}
              className="h-9"
            />

            {mode === "zip" ? (
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip,application/zip"
                  className="hidden"
                  data-testid="plugin-zip-input"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setPreview(null);
                  }}
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex w-full items-center gap-3 rounded-lg border border-dashed border-surface-border bg-surface-soft/40 px-4 py-4 text-left transition-colors hover:border-brand/40 hover:bg-surface-soft"
                >
                  <FileArchive className="h-5 w-5 flex-none text-ink-meta" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-ink-heading">
                      {file ? file.name : t("plugin.selectZip")}
                    </div>
                    <div className="text-2xs text-ink-meta">
                      {t("plugin.installPathHelp")}
                    </div>
                  </div>
                </button>
              </div>
            ) : (
              <DialogField
                label={t("plugin.installFromPath")}
                required
                help={t("plugin.installPathHelp")}
              >
                <DialogInput
                  value={locator}
                  placeholder={t("plugin.installPathPlaceholder")}
                  onChange={(e) => {
                    setLocator(e.target.value);
                    setPreview(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && canPreview) void handlePreview();
                  }}
                />
              </DialogField>
            )}

            {preview ? (
              <div className="space-y-3 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-ink-heading">
                    {manifestName ?? "—"}
                  </span>
                  {manifestVersion ? (
                    <Badge variant="metaNeutral" className="font-mono">
                      v{manifestVersion}
                    </Badge>
                  ) : null}
                  <Badge variant="metaOutline">
                    {t(
                      PLUGIN_FORMAT_LABEL_KEYS[preview.format] as Parameters<
                        typeof t
                      >[0],
                    )}
                  </Badge>
                </div>
                {manifestDescription ? (
                  <p className="text-xs leading-relaxed text-ink-body">
                    {manifestDescription}
                  </p>
                ) : null}
                <PluginMembersList members={preview.members} />
                {preview.existing ? (
                  <div className="flex items-start gap-2 rounded-lg border border-info-border bg-info-light px-3 py-2 text-xs text-info-text">
                    <Info className="mt-0.5 h-3.5 w-3.5 flex-none" />
                    <span>
                      {preview.existing === "other_source"
                        ? t("plugin.existingOtherSource")
                        : t("plugin.existingSameSource")}
                    </span>
                  </div>
                ) : null}
                {preview.conflicts.length > 0 ? (
                  <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-light px-3 py-2 text-xs text-warning-text">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
                    <div>
                      <div className="font-medium">
                        {t("plugin.conflictTitle")}
                      </div>
                      <div className="mt-0.5 font-mono">
                        {preview.conflicts.map((c) => c.slug).join(" · ")}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5 text-xs text-success-text">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    {t("plugin.conflictNone")}
                  </div>
                )}
                {preview.warnings.length > 0 ? (
                  <div className="text-xs text-ink-body">
                    <div className="mb-1 font-medium text-ink-heading">
                      {t("plugin.warnings")}
                    </div>
                    <ul className="list-disc space-y-0.5 pl-4">
                      {preview.warnings.map((w, i) => (
                        <li key={`${i}-${w}`}>{w}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <DialogFooter className="border-t border-surface-border px-6 py-3.5">
            <Button
              variant="outline"
              size="sm"
              disabled={installing || previewing}
              onClick={close}
            >
              {t("common.cancel")}
            </Button>
            {preview ? (
              <Button
                size="sm"
                loading={installing}
                disabled={preview.existing === "other_source"}
                onClick={handleInstall}
              >
                {installing
                  ? t("plugin.installing")
                  : t("plugin.confirmInstall")}
              </Button>
            ) : (
              <Button
                size="sm"
                loading={previewing}
                disabled={!canPreview}
                onClick={() => void handlePreview()}
              >
                {previewing ? t("plugin.previewing") : t("plugin.preview")}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PluginConflictDialog
        open={conflictOpen}
        conflicts={preview?.conflicts ?? []}
        busy={installing}
        onOpenChange={setConflictOpen}
        onChoose={(onConflict) => void doInstall(onConflict)}
      />
    </>
  );
}
