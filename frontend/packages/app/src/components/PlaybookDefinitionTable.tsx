import {
  Archive,
  BookOpenText,
  CircleCheck,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Play,
  Trash2,
} from "lucide-react";
import {
  useTranslation,
  type PlaybookDefinition,
  type PlaybookStatus,
} from "@valuz/core";
import {
  Button,
  Card,
  CardContent,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  StatusPill,
} from "@valuz/ui";

import { OriginIcon } from "./ExecutionLocationPicker";

export interface PlaybookDefinitionTableProps {
  definitions: PlaybookDefinition[];
  runningId: string | null;
  onOpen: (definition: PlaybookDefinition) => void;
  onRun: (definition: PlaybookDefinition) => void;
  onStatusChange: (
    definition: PlaybookDefinition,
    status: PlaybookStatus,
  ) => void;
  onDelete: (definition: PlaybookDefinition) => void;
  selectedDefinitionId?: string | null;
  title?: string;
  countLabel?: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

/** Canonical Playbook definition rows used by both global and workspace lists. */
export function PlaybookDefinitionTable({
  definitions,
  runningId,
  onOpen,
  onRun,
  onStatusChange,
  onDelete,
  selectedDefinitionId,
  title,
  countLabel,
  collapsed = false,
  onToggleCollapse,
}: PlaybookDefinitionTableProps) {
  const { t } = useTranslation();
  const Chevron = collapsed ? ChevronRight : ChevronDown;
  const rows = [...definitions].sort(
    (a, b) =>
      Number(a.status === "retired") - Number(b.status === "retired"),
  );

  return (
    <Card className="gap-0 overflow-hidden border-0 py-0 shadow-[var(--shadow-1)]">
      <CardContent className="px-0 py-0">
        {title ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            className="flex h-10 w-full items-center justify-between gap-4 px-5 text-left"
            aria-expanded={!collapsed}
          >
            <div className="flex min-w-0 items-center gap-3">
              <Chevron className="h-4 w-4 shrink-0 text-ink-meta" />
              <span className="truncate text-sm font-semibold text-ink-heading">
                {title}
                {countLabel ? (
                  <span className="font-medium text-[#6e7481]">
                    {" · "}
                    {countLabel}
                  </span>
                ) : null}
              </span>
            </div>
          </button>
        ) : null}

        {collapsed ? null : (
          <>
            <div className="hidden border-b border-[#f7f8fa] px-5 py-2 text-xs font-medium text-[#6E7481] md:grid md:grid-cols-[2fr_0.7fr_0.8fr_72px] dark:border-surface-border dark:text-ink-body">
              <div>{t("playbook.nameColumn")}</div>
              <div className="text-center">{t("playbook.versionColumn")}</div>
              <div className="text-center">{t("playbook.statusColumn")}</div>
              <div className="text-center">{t("playbook.actionColumn")}</div>
            </div>

            {rows.map((definition) => {
              const selected = selectedDefinitionId === definition.id;
              const actionMenu = (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="h-8 w-8 hover:bg-[#f3f4f6] hover:text-inherit dark:hover:bg-surface-muted"
                      aria-label={t("playbook.actionColumn")}
                    >
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="min-w-[140px]">
                    <DropdownMenuItem
                      disabled={
                        definition.status === "retired" ||
                        runningId === definition.id
                      }
                      onSelect={() => onRun(definition)}
                    >
                      <Play />
                      {runningId === definition.id
                        ? t("playbook.running")
                        : t("playbook.runAction")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => onOpen(definition)}>
                      <Pencil />
                      {t("common.edit")}
                    </DropdownMenuItem>
                    {definition.status !== "active" ? (
                      <DropdownMenuItem
                        onSelect={() => onStatusChange(definition, "active")}
                      >
                        <CircleCheck />
                        {t("playbook.activateAction")}
                      </DropdownMenuItem>
                    ) : null}
                    {definition.status !== "retired" ? (
                      <DropdownMenuItem
                        onSelect={() => onStatusChange(definition, "retired")}
                      >
                        <Archive />
                        {t("playbook.retireAction")}
                      </DropdownMenuItem>
                    ) : null}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-error-text focus:text-error-text"
                      onSelect={() => onDelete(definition)}
                    >
                      <Trash2 />
                      {t("common.delete")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              );

              return (
                <div
                  key={definition.id}
                  id={`playbook-${definition.id}`}
                  className={
                    selected
                      ? "bg-brand/5 ring-1 ring-inset ring-brand/30"
                      : undefined
                  }
                >
                  <div className="hidden items-center px-5 py-4 md:grid md:grid-cols-[2fr_0.7fr_0.8fr_72px]">
                    <div className="flex min-w-0 items-start gap-2">
                      <BookOpenText
                        className={
                          definition.status === "retired"
                            ? "mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta opacity-50"
                            : "mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta"
                        }
                      />
                      <button
                        type="button"
                        onClick={() => onOpen(definition)}
                        className={
                          definition.status === "retired"
                            ? "flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading opacity-50 transition-colors hover:text-brand"
                            : "flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand"
                        }
                      >
                        <span className="truncate">{definition.name}</span>
                        {definition.exec_origin ? (
                          <OriginIcon origin={definition.exec_origin} />
                        ) : null}
                      </button>
                    </div>
                    <div className="text-center font-mono text-xs text-ink-label">
                      v{definition.current_version}
                    </div>
                    <div className="flex justify-center">
                      <StatusPill
                        status={definition.status}
                        label={t(`playbook.status.${definition.status}`)}
                      />
                    </div>
                    <div className="flex justify-center">{actionMenu}</div>
                  </div>

                  <div className="px-5 py-4 md:hidden">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-2">
                        <BookOpenText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-meta" />
                        <div className="min-w-0">
                          <button
                            type="button"
                            onClick={() => onOpen(definition)}
                            className="flex min-w-0 items-center gap-1 truncate text-left text-sm font-medium text-ink-heading"
                          >
                            <span className="truncate">{definition.name}</span>
                            {definition.exec_origin ? (
                              <OriginIcon origin={definition.exec_origin} />
                            ) : null}
                          </button>
                          <div className="mt-1 font-mono text-xs text-ink-meta">
                            v{definition.current_version}
                          </div>
                        </div>
                      </div>
                      <StatusPill
                        status={definition.status}
                        label={t(`playbook.status.${definition.status}`)}
                      />
                    </div>
                    <div className="mt-2 flex justify-end">{actionMenu}</div>
                  </div>
                </div>
              );
            })}
          </>
        )}
      </CardContent>
    </Card>
  );
}
