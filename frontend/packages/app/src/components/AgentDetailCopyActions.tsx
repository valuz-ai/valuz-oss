import { Copy, Upload } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@valuz/ui";
import { useRegistryStore, useTranslation } from "@valuz/core";
import { ResourceCopyMenuItemSlot } from "./ResourceActionSlot";

export interface AgentDetailCopyActionsProps {
  resource: Record<string, unknown>;
  isSystem: boolean;
  onExport: () => void;
  onCopy: () => void;
}

const iconButtonClassName =
  "flex h-7 w-7 cursor-default items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body";

export function AgentDetailCopyActions({
  resource,
  isSystem,
  onExport,
  onCopy,
}: AgentDetailCopyActionsProps) {
  const { t } = useTranslation();
  const hasCopyMenuItems = useRegistryStore(
    (state) =>
      (state.slots["resource.agent.copy.menu-items"]?.length ?? 0) > 0,
  );
  const exportLabel = t("agent.pack.export" as Parameters<typeof t>[0]);
  const copyLabel = t("agent.copyAgent" as Parameters<typeof t>[0]);

  if (hasCopyMenuItems) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title={copyLabel}
            aria-label={copyLabel}
            className={iconButtonClassName}
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" forceMount>
          {!isSystem ? (
            <DropdownMenuItem onSelect={onExport}>
              <Upload className="h-3.5 w-3.5" />
              {exportLabel}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem onSelect={onCopy}>
            <Copy className="h-3.5 w-3.5" />
            {copyLabel}
          </DropdownMenuItem>
          <ResourceCopyMenuItemSlot
            resourceType="agent"
            resource={resource}
          />
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  return (
    <>
      {!isSystem ? (
        <button
          type="button"
          onClick={onExport}
          title={exportLabel}
          aria-label={exportLabel}
          className={iconButtonClassName}
        >
          <Upload className="h-3.5 w-3.5" />
        </button>
      ) : null}
      <button
        type="button"
        onClick={onCopy}
        title={copyLabel}
        aria-label={copyLabel}
        className={iconButtonClassName}
      >
        <Copy className="h-3.5 w-3.5" />
      </button>
    </>
  );
}
