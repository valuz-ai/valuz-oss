/**
 * Execution-location UI for multi-target editions.
 *
 * Both components render ``null`` on single-backend builds (OSS registers no
 * execution targets), so pages can mount them unconditionally.
 *
 * - ``ExecutionLocationPicker`` — creation-time target choice (new quick chat
 *   / new project). The choice is locked once the entity is created; callers
 *   swap the picker for an ``OriginBadge`` at that point.
 * - ``OriginBadge`` — a small 📍 where-does-this-live pill for headers and
 *   list rows. Reads a provided ``origin`` (list fan-out tags rows directly)
 *   or falls back to the reactive origin index (``useEntityOrigin``).
 */

import {
  getDefaultExecutionTarget,
  useEntityOrigin,
  useExecutionTargets,
  useTranslation,
  type EntityOriginKind,
} from "@valuz/core";
import { SegmentedControl, cn } from "@valuz/ui";
import { executionTargetIcon } from "./execution-target-icon";

type TK = Parameters<ReturnType<typeof useTranslation>["t"]>[0];

export interface ExecutionLocationPickerProps {
  /** Selected target id; ``null`` = follow the registered default. */
  value: string | null;
  onChange: (targetId: string) => void;
  className?: string;
}

export function ExecutionLocationPicker({
  value,
  onChange,
  className,
}: ExecutionLocationPickerProps) {
  const { t } = useTranslation();
  const targets = useExecutionTargets();
  if (targets.length < 2) return null;
  const active = value ?? getDefaultExecutionTarget()?.id ?? targets[0]!.id;
  return (
    <SegmentedControl
      value={active}
      onValueChange={onChange}
      options={targets.map((target) => ({
        value: target.id,
        label: t(target.labelKey as TK),
        icon: executionTargetIcon(target.id, target),
      }))}
      className={cn("h-8 w-fit", className)}
    />
  );
}

export interface OriginIconProps {
  /** Known origin (list fan-out tags rows directly). */
  origin?: string;
  className?: string;
}

/**
 * Icon-only origin marker for tight rows (sidebar recents / project rows)
 * where the full pill would crowd the title out. Tooltip carries the label.
 * Renders nothing on single-target builds or untagged rows.
 */
export function OriginIcon({ origin, className }: OriginIconProps) {
  const { t } = useTranslation();
  const targets = useExecutionTargets();
  if (!origin || targets.length < 2) return null;
  const target = targets.find((candidate) => candidate.id === origin);
  const label = target ? t(target.labelKey as TK) : origin;
  const Icon = executionTargetIcon(origin, target);
  return (
    <Icon
      data-slot="origin-icon"
      data-origin={origin}
      aria-label={label}
      className={cn("h-3 w-3 shrink-0 text-ink-meta", className)}
    >
      <title>{label}</title>
    </Icon>
  );
}

export interface OriginBadgeProps {
  /** Known origin (e.g. tagged by list fan-out). Wins over the lookup. */
  origin?: string;
  /** Entity to look the origin up for when ``origin`` is not provided. */
  entityId?: string | null;
  /** Lets the edition adapter probe on a cache miss (deep links). */
  kind?: EntityOriginKind;
  className?: string;
}

export function OriginBadge({
  origin,
  entityId,
  kind,
  className,
}: OriginBadgeProps) {
  const { t } = useTranslation();
  const targets = useExecutionTargets();
  const observed = useEntityOrigin(origin ? null : entityId, kind);
  const effective = origin ?? observed;
  // Single-target builds show no badge — location noise for OSS users.
  if (!effective || targets.length < 2) return null;
  const target = targets.find((candidate) => candidate.id === effective);
  const label = target ? t(target.labelKey as TK) : effective;
  const Icon = executionTargetIcon(effective, target);
  return (
    <span
      data-slot="origin-badge"
      data-origin={effective}
      title={label}
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-full border border-surface-border bg-surface-soft px-1.5 py-0.5 text-2xs leading-none text-ink-meta",
        className,
      )}
    >
      <Icon className="h-2.5 w-2.5" />
      <span className="truncate">{label}</span>
    </span>
  );
}
