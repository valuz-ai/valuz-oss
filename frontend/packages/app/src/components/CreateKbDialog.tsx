import { useState } from "react";
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
} from "@valuz/ui";
import { DirectoryPicker } from "@valuz/ui";
import {
  getDefaultExecutionTarget,
  targetUsesManagedCwd,
  useExecutionTargets,
  useTranslation,
} from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import type { DirectoryFieldMode } from "../layout";
import { ExecutionLocationPicker } from "./ExecutionLocationPicker";

export interface CreateKbDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** ``"managed"`` hides the local-directory picker and creates a
   * backend-managed KB root (cloud / headless). */
  directoryFieldMode?: DirectoryFieldMode;
  onSubmit: (data: {
    name: string;
    /** Undefined when ``directoryFieldMode="managed"`` or a remote
     * (cloud) execution target is chosen — both force a managed root. */
    root_path?: string;
    auto_discover: boolean;
    /** Chosen execution target id (``"local"``/``"cloud"``) on multi-target
     * editions; ``undefined`` on single-backend builds. The caller resolves
     * it to a ``baseUrl`` for the create call and records the origin. */
    target_id?: string;
  }) => Promise<void>;
}

export const CreateKbDialog = ({
  open,
  onOpenChange,
  directoryFieldMode = "picker",
  onSubmit,
}: CreateKbDialogProps) => {
  const { t } = useTranslation();
  const { selectDirectory } = usePlatform();
  const targets = useExecutionTargets();
  const [targetId, setTargetId] = useState<string | null>(null);
  // A remote (cloud) target can't see this machine's filesystem — its KBs are
  // always backed by a managed root. The overlay-passed managed mode
  // (browser/webui) forces the same. On single-target builds ``targets`` is
  // empty, the picker renders null, and ``effectiveManaged`` collapses to the
  // prop — zero behaviour change.
  const effectiveTarget =
    targets.length === 0
      ? undefined
      : (targets.find((tt) => tt.id === targetId) ??
        getDefaultExecutionTarget());
  // A remote target that can browse its own filesystem (remote desktop)
  // keeps the directory field and uses its chooser; a plain remote (cloud)
  // target stays managed.
  const isRemoteTarget = targetUsesManagedCwd(effectiveTarget);
  const propManaged = directoryFieldMode === "managed";
  const effectiveManaged = propManaged || isRemoteTarget;
  const pickDirectory = async (): Promise<string | null> => {
    const own = effectiveTarget?.selectDirectory;
    if (own) return (await own())?.path ?? null;
    return await selectDirectory();
  };

  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [autoDiscover, setAutoDiscover] = useState(true);
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim() || (!effectiveManaged && !rootPath.trim())) return;
    setCreating(true);
    try {
      await onSubmit({
        name: name.trim(),
        root_path: effectiveManaged ? undefined : rootPath.trim(),
        auto_discover: autoDiscover,
        target_id: targets.length >= 2 ? effectiveTarget?.id : undefined,
      });
      onOpenChange(false);
      setName("");
      setRootPath("");
      setTargetId(null);
      setAutoDiscover(true);
    } catch {
      // Error handling is delegated to the caller via onSubmit rejection
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-0 p-0">
        <DialogHeader className="px-[18px] pt-[18px] pb-1">
          <DialogTitle className="text-sm leading-5">
            {t("knowledge.newKb" as Parameters<typeof t>[0])}
          </DialogTitle>
          <DialogDescription>
            {effectiveManaged
              ? t("knowledge.managedKbHint" as Parameters<typeof t>[0])
              : t("knowledge.linkLocalDir" as Parameters<typeof t>[0])}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-[14px] px-[18px] py-[14px]">
          <FormField label={t("common.name" as Parameters<typeof t>[0])}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t(
                "knowledge.kbNamePlaceholder" as Parameters<typeof t>[0],
              )}
            />
          </FormField>
          {targets.length >= 2 ? (
            <FormField
              label={t("project.execLocation" as Parameters<typeof t>[0])}
            >
              <ExecutionLocationPicker
                value={targetId}
                onChange={setTargetId}
              />
            </FormField>
          ) : null}
          {effectiveManaged ? (
            <FormField
              label={t("knowledge.sourcePath" as Parameters<typeof t>[0])}
            >
              <p className="text-xs text-muted-foreground">
                {t("knowledge.managedKbHint" as Parameters<typeof t>[0])}
              </p>
            </FormField>
          ) : (
            <FormField
              label={t("knowledge.sourcePath" as Parameters<typeof t>[0])}
            >
              <DirectoryPicker
                value={rootPath}
                placeholder={t(
                  "knowledge.selectDir" as Parameters<typeof t>[0],
                )}
                onBrowse={async () => {
                  const dir = await pickDirectory();
                  if (dir) setRootPath(dir);
                }}
              />
            </FormField>
          )}
          <label className="flex items-center gap-2">
            <Checkbox
              checked={autoDiscover}
              onCheckedChange={(checked) => setAutoDiscover(checked === true)}
              className="border-surface-border-hover data-[state=checked]:border-brand data-[state=checked]:bg-brand data-[state=checked]:text-white"
            />
            <span className="text-sm text-ink-body">
              {t("knowledge.autoDiscover" as Parameters<typeof t>[0])}
            </span>
          </label>
        </div>
        <DialogFooter className="px-[18px] pt-1 pb-4">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={creating}
          >
            {t("common.cancel" as Parameters<typeof t>[0])}
          </Button>
          <Button
            onClick={handleCreate}
            loading={creating}
            disabled={!name.trim() || (!effectiveManaged && !rootPath.trim())}
          >
            {t("common.create" as Parameters<typeof t>[0])}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
