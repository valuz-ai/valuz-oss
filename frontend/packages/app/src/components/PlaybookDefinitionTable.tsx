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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  StatusPill,
} from "@valuz/ui";

import { OriginIcon } from "./ExecutionLocationPicker";

export interface PlaybookDefinitionGroup {
  id: string;
  name: string;
  countLabel?: string;
  definitions: PlaybookDefinition[];
}

export interface PlaybookDefinitionTableProps {
  /** Single-group mode (workspace surfaces). */
  definitions?: PlaybookDefinition[];
  /** Multi-group mode: one table, a section row per project (执行手册 page). */
  groups?: PlaybookDefinitionGroup[];
  runningId: string | null;
  onOpen: (definition: PlaybookDefinition) => void;
  onEdit: (definition: PlaybookDefinition) => void;
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

const GRID = "md:grid-cols-[3fr_0.6fr_0.9fr_56px]";

/** Canonical Playbook definition rows used by both global and workspace lists. */
export function PlaybookDefinitionTable({
  definitions = [],
  groups,
  runningId,
  onOpen,
  onEdit,
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
  const sortRows = (items: PlaybookDefinition[]) =>
    [...items].sort(
      (a, b) =>
        Number(a.status === "retired") - Number(b.status === "retired"),
    );
  const sections: PlaybookDefinitionGroup[] = groups ?? [
    { id: "__single", name: title ?? "", countLabel, definitions },
  ];
  const singleCollapsible = !groups && Boolean(title);

  const sectionHeading = (section: PlaybookDefinitionGroup) =>
    singleCollapsible ? (
      <button
        type="button"
        onClick={onToggleCollapse}
        className="flex h-9 w-full items-center gap-3 px-0 text-left"
        aria-expanded={!collapsed}
      >
        <Chevron className="h-4 w-4 shrink-0 text-ink-meta" />
        <span className="truncate text-sm font-semibold text-ink-heading">
          {section.name}
          {section.countLabel ? (
            <span className="font-medium text-[#6e7481]">
              {" · "}
              {section.countLabel}
            </span>
          ) : null}
        </span>
      </button>
    ) : (
      <div className="mt-8 flex h-7 items-center gap-2 px-3 text-xs font-semibold text-ink-body first:mt-2">
        <span className="truncate">{section.name}</span>
        {section.countLabel ? (
          <span className="font-normal text-ink-meta">{section.countLabel}</span>
        ) : null}
      </div>
    );

  const renderRow = (definition: PlaybookDefinition) => {
    const selected = selectedDefinitionId === definition.id;
    const retired = definition.status === "retired";
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
          <DropdownMenuItem onSelect={() => onEdit(definition)}>
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
            ? "rounded-xl bg-brand/5 ring-1 ring-inset ring-brand/30"
            : "rounded-xl transition-colors hover:bg-surface-soft"
        }
      >
        <div className={`hidden items-center px-3 py-3 md:grid ${GRID}`}>
          <div className="flex min-w-0 items-center gap-2">
            <BookOpenText
              className={
                retired
                  ? "h-3.5 w-3.5 shrink-0 text-ink-meta opacity-50"
                  : "h-3.5 w-3.5 shrink-0 text-ink-meta"
              }
            />
            <button
              type="button"
              onClick={() => onOpen(definition)}
              className={
                retired
                  ? "flex min-w-0 items-center gap-1.5 truncate text-left text-sm font-medium text-ink-heading opacity-50 transition-colors hover:text-brand"
                  : "flex min-w-0 items-center gap-1.5 truncate text-left text-sm font-medium text-ink-heading transition-colors hover:text-brand"
              }
            >
              <span className="truncate">{definition.name}</span>
              {definition.exec_origin ? (
                <OriginIcon origin={definition.exec_origin} />
              ) : null}
            </button>
          </div>
          <div className="font-mono text-xs text-ink-label">
            v{definition.current_version}
          </div>
          <div className="flex">
            <StatusPill
              status={definition.status}
              label={t(`playbook.status.${definition.status}`)}
            />
          </div>
          <div className="flex justify-end">{actionMenu}</div>
        </div>

        <div className="px-0 py-3 md:hidden">
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
  };

  const hasRows = sections.some((section) => section.definitions.length > 0);
  return (
    <section>
      {singleCollapsible ? sectionHeading(sections[0]!) : null}
      {singleCollapsible && collapsed ? null : (
        <>
          {hasRows ? (
            <div
              className={`sticky top-0 z-10 hidden border-b border-surface-border bg-card px-3 py-2 text-xs font-medium text-[#6E7481] md:grid dark:text-ink-body ${GRID}`}
            >
              <div>{t("playbook.nameColumn")}</div>
              <div>{t("playbook.versionColumn")}</div>
              <div>{t("playbook.statusColumn")}</div>
              <div className="text-right">{t("playbook.actionColumn")}</div>
            </div>
          ) : null}
          {sections.map((section) => (
            <div key={section.id} className="space-y-1">
              {groups && section.name ? sectionHeading(section) : null}
              {sortRows(section.definitions).map(renderRow)}
            </div>
          ))}
        </>
      )}
    </section>
  );
}
