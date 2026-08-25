/**
 * Conversation context bar — the attached strip under the composer (mounted
 * through the Composer's ``footerBar`` slot). Owns the 📁 project choice for
 * a NEW conversation and keeps showing the bound context on existing ones:
 *
 *  1. location chip（本地服务/云端服务）— ONLY on multi-target editions
 *     (commercial with a cloud backend configured). OSS single-backend
 *     builds never render it — that chip is the whole visual difference.
 *  2. project chip（可选）— always rendered: pick a project (filtered by the
 *     chosen location) or「临时对话」. Replaces the composer's old built-in
 *     📁 toolbar chip.
 *
 * ``locked`` (session exists) renders both chips as a static display —
 * project and location are frozen at creation (ADR-006 semantics).
 *
 * Consistency rule: a selected project OWNS the location (its origin); the
 * location chip displays it and switching location resets the project.
 */

import {
  getDefaultExecutionTarget,
  selectableExecutionTargets,
  useExecutionTargets,
  useTranslation,
} from "@valuz/core";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  cn,
} from "@valuz/ui";
import { Check, ChevronDown, FolderOpen } from "lucide-react";
import { executionTargetIcon } from "./execution-target-icon";

type TK = Parameters<ReturnType<typeof useTranslation>["t"]>[0];

export interface ExecutionLocationBarProject {
  id: string;
  name: string;
  /** Fan-out tag; untagged rows count as the module-default (local). */
  execOrigin?: string;
}

export interface ExecutionLocationBarProps {
  /** Chosen target id; ``null`` follows the registered default. */
  targetId: string | null;
  onTargetChange: (targetId: string) => void;
  /** All projects (merged across targets); the bar filters by location. */
  projects: ExecutionLocationBarProject[];
  /** ``null`` = 临时对话 (no project). */
  selectedProjectId: string | null;
  onProjectChange: (projectId: string | null) => void;
  /**
   * Static display mode for an existing conversation — project and location
   * are frozen at creation, so the chips lose their dropdowns.
   */
  locked?: boolean;
  /**
   * Observed origin of the locked conversation (multi-target editions);
   * drives the static location chip when ``locked``.
   */
  lockedOriginId?: string;
  className?: string;
}

// ``min-w-0`` (not ``shrink-0``): this row sits under a composer that can be
// as narrow as a resized chat card, and a chip that refuses to shrink pushes
// the row past the card edge instead of truncating inside it. It stays one
// line — the labels truncate, and the location label steps aside entirely
// below the container breakpoint.
const CHIP_CLASS =
  "flex h-7 min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-ink-body outline-none";
const CHIP_INTERACTIVE_CLASS =
  "cursor-default transition-colors hover:bg-surface-border data-[state=open]:bg-surface-border";

export function ExecutionLocationBar({
  targetId,
  onTargetChange,
  projects,
  selectedProjectId,
  onProjectChange,
  locked = false,
  lockedOriginId,
  className,
}: ExecutionLocationBarProps) {
  const { t } = useTranslation();
  const targets = useExecutionTargets();
  // Choices exclude unselectable targets; ``targets`` itself still resolves
  // the label of wherever this conversation actually runs.
  const targetChoices = selectableExecutionTargets(targets);
  const multiTarget = targets.length >= 2;

  const selectedProject =
    selectedProjectId != null
      ? (projects.find((project) => project.id === selectedProjectId) ?? null)
      : null;
  // A picked project owns the location; a locked conversation shows its
  // recorded origin; otherwise the explicit pick / default.
  const effectiveTargetId =
    (locked ? lockedOriginId : undefined) ??
    (selectedProject
      ? (selectedProject.execOrigin ?? "local")
      : (targetId ?? getDefaultExecutionTarget()?.id ?? targets[0]?.id));
  const effectiveTarget = multiTarget
    ? (targets.find((target) => target.id === effectiveTargetId) ?? targets[0]!)
    : undefined;
  const scopedProjects = multiTarget
    ? projects.filter(
        (project) => (project.execOrigin ?? "local") === effectiveTargetId,
      )
    : projects;

  const projectLabel = selectedProject
    ? selectedProject.name
    : locked
      ? t("conversation.tempChat" as TK)
      : t("conversation.execSelectProject" as TK);

  const LocationIcon = effectiveTarget
    ? executionTargetIcon(effectiveTarget.id, effectiveTarget)
    : null;
  const locationChipBody =
    effectiveTarget && LocationIcon ? (
      <>
        <LocationIcon className="h-3.5 w-3.5 shrink-0" />
        <span className="hidden max-w-[120px] truncate @[360px]/execbar:inline">
          {t(effectiveTarget.labelKey as TK)}
        </span>
      </>
    ) : null;

  const projectChipBody = (
    <>
      <FolderOpen
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          selectedProject || locked ? "text-ink-heading" : "text-ink-muted",
        )}
      />
      <span
        className={cn(
          "max-w-[220px] truncate",
          selectedProject || locked ? "text-ink-heading" : "text-ink-meta",
        )}
      >
        {projectLabel}
      </span>
    </>
  );

  return (
    <div
      data-slot="execution-location-bar"
      data-locked={locked || undefined}
      className={cn(
        // pt-3 covers the 8px tuck under the input card + breathing room.
        "@container/execbar flex items-center gap-1 rounded-b-xl border border-t-0 border-surface-soft bg-surface-soft px-2 pb-1 pt-3",
        className,
      )}
    >
      {/* Location chip — multi-target editions only. */}
      {effectiveTarget ? (
        locked ? (
          <span className={cn(CHIP_CLASS, "font-medium text-ink-heading")}>
            {locationChipBody}
          </span>
        ) : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={cn(
                  CHIP_CLASS,
                  CHIP_INTERACTIVE_CLASS,
                  "font-medium text-ink-heading",
                )}
              >
                {locationChipBody}
                <ChevronDown className="h-3 w-3 shrink-0 text-ink-muted" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              className="min-w-[160px]"
              onCloseAutoFocus={(e) => e.preventDefault()}
            >
              {targetChoices.map((target) => {
                const Icon = executionTargetIcon(target.id, target);
                return (
                  <DropdownMenuItem
                    key={target.id}
                    onSelect={() => {
                      if (target.id === effectiveTargetId) return;
                      onTargetChange(target.id);
                    }}
                    className="flex items-center gap-2"
                  >
                    <Icon className="h-4 w-4" />
                    <span className="flex-1 truncate text-sm">
                      {t(target.labelKey as TK)}
                    </span>
                    {target.id === effectiveTargetId ? (
                      <Check className="h-4 w-4 shrink-0 text-primary" />
                    ) : null}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        )
      ) : null}

      {/* Project chip — all editions (replaces the old toolbar 📁 chip). */}
      {locked ? (
        <span className={CHIP_CLASS}>{projectChipBody}</span>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(CHIP_CLASS, CHIP_INTERACTIVE_CLASS)}
            >
              {projectChipBody}
              <ChevronDown className="h-3 w-3 shrink-0 text-ink-muted" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="min-w-[220px]"
            onCloseAutoFocus={(e) => e.preventDefault()}
          >
            <DropdownMenuItem
              onSelect={() => onProjectChange(null)}
              className="flex items-center gap-2"
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm">
                  {t("conversation.tempChat" as TK)}
                </span>
                <span className="truncate text-xs text-ink-meta">
                  {t("conversation.tempChatHint" as TK)}
                </span>
              </span>
              {selectedProject == null ? (
                <Check className="h-4 w-4 shrink-0 text-primary" />
              ) : null}
            </DropdownMenuItem>
            {scopedProjects.length > 0 ? <DropdownMenuSeparator /> : null}
            {scopedProjects.map((project) => (
              <DropdownMenuItem
                key={project.id}
                onSelect={() => onProjectChange(project.id)}
                className="flex items-center gap-2"
              >
                <FolderOpen className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-sm">
                  {project.name}
                </span>
                {project.id === selectedProjectId ? (
                  <Check className="h-4 w-4 shrink-0 text-primary" />
                ) : null}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
