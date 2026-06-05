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
import { useTranslation } from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";

export interface CreateKbDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: {
    name: string;
    root_path: string;
    auto_discover: boolean;
  }) => Promise<void>;
}

export const CreateKbDialog = ({
  open,
  onOpenChange,
  onSubmit,
}: CreateKbDialogProps) => {
  const { t } = useTranslation();
  const { selectDirectory } = usePlatform();

  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [autoDiscover, setAutoDiscover] = useState(true);
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim() || !rootPath.trim()) return;
    setCreating(true);
    try {
      await onSubmit({
        name: name.trim(),
        root_path: rootPath.trim(),
        auto_discover: autoDiscover,
      });
      onOpenChange(false);
      setName("");
      setRootPath("");
      setAutoDiscover(true);
    } catch {
      // Error handling is delegated to the caller via onSubmit rejection
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("knowledge.newKb" as Parameters<typeof t>[0])}
          </DialogTitle>
          <DialogDescription>
            {t("knowledge.linkLocalDir" as Parameters<typeof t>[0])}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <FormField label={t("common.name" as Parameters<typeof t>[0])}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t(
                "knowledge.kbNamePlaceholder" as Parameters<typeof t>[0],
              )}
              className="h-10"
            />
          </FormField>
          <FormField
            label={t("knowledge.sourcePath" as Parameters<typeof t>[0])}
          >
            <DirectoryPicker
              value={rootPath}
              placeholder={t("knowledge.selectDir" as Parameters<typeof t>[0])}
              onBrowse={async () => {
                const dir = await selectDirectory();
                if (dir) setRootPath(dir);
              }}
            />
          </FormField>
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
        <DialogFooter>
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
            disabled={!name.trim() || !rootPath.trim()}
          >
            {t("common.create" as Parameters<typeof t>[0])}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
