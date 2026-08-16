import { AlertTriangle } from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import type { AgentPluginMemberRef, AgentPluginOnConflict } from "@valuz/core";

interface PluginConflictDialogProps {
  open: boolean;
  /** Same-slug members whose library copy differs (from ``/v1/plugins/preview``). */
  conflicts: AgentPluginMemberRef[];
  busy?: boolean;
  onOpenChange: (open: boolean) => void;
  /** User picked a per-member policy — the caller then installs/updates. */
  onChoose: (onConflict: AgentPluginOnConflict) => void;
}

/**
 * The "never silently overwrite" prompt: lists the conflicting members and
 * lets the user keep the library copies (skip → link + flag) or overwrite
 * them with the plugin's copies. Cancel aborts the install.
 */
export function PluginConflictDialog({
  open,
  conflicts,
  busy = false,
  onOpenChange,
  onChoose,
}: PluginConflictDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-warning-text" />
            {t("plugin.conflictTitle")}
          </DialogTitle>
          <DialogDescription>{t("plugin.conflictDesc")}</DialogDescription>
        </DialogHeader>
        <ul className="max-h-[40vh] space-y-1 overflow-y-auto rounded-lg border border-surface-border bg-surface-soft/60 p-2">
          {conflicts.map((c) => (
            <li
              key={`${c.kind}:${c.slug}`}
              className="flex items-center gap-2 px-1 py-0.5 text-sm text-ink-heading"
            >
              <Badge variant="metaOutline" className="h-4 px-1 text-2xs">
                {c.kind === "skill"
                  ? t("plugin.skills")
                  : t("plugin.connectors")}
              </Badge>
              <span className="truncate font-mono text-xs">{c.slug}</span>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onOpenChange(false)}
          >
            {t("common.cancel")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onChoose("skip")}
          >
            {t("plugin.conflictSkip")}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            loading={busy}
            onClick={() => onChoose("overwrite")}
          >
            {t("plugin.conflictOverwrite")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
