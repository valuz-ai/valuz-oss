import type { FC } from "react";
import { AlertTriangle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

export interface PermissionRequestDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  type: "file-access" | "execute" | "file-write";
  path?: string;
  command?: string;
  onAllow?: () => void;
  onDeny?: () => void;
}

export const PermissionRequestDialog: FC<PermissionRequestDialogProps> = ({
  open,
  onOpenChange,
  type,
  path,
  command,
  onAllow,
  onDeny,
}) => {
  const { t } = useI18n();

  const titles: Record<PermissionRequestDialogProps["type"], string> = {
    "file-access": t("permission.fileAccess"),
    execute: t("permission.runCommand"),
    "file-write": t("permission.fileWrite"),
  };

  const descriptions: Record<PermissionRequestDialogProps["type"], string> = {
    "file-access": t("permission.fileAccessDesc"),
    execute: t("permission.runCommandDesc"),
    "file-write": t("permission.fileWriteDesc"),
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-warning-text" />
            {titles[type]}
          </DialogTitle>
          <DialogDescription>{descriptions[type]}</DialogDescription>
        </DialogHeader>

        {type === "file-access" && path ? (
          <code className="rounded-md bg-surface-soft px-3 py-2 font-mono text-xs text-ink-body">
            {path}
          </code>
        ) : null}

        {type === "execute" && command ? (
          <code className="rounded-md bg-surface-soft px-3 py-2 font-mono text-xs text-ink-body">
            {command}
          </code>
        ) : null}

        {type === "file-write" && path ? (
          <code className="rounded-md bg-surface-soft px-3 py-2 font-mono text-xs text-ink-body">
            {path}
          </code>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onDeny}>
            {t("permission.deny")}
          </Button>
          <Button onClick={onAllow}>{t("permission.allow")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
