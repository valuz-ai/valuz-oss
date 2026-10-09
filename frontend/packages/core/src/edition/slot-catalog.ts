import { HOST_SLOTS } from "./host-slots";

/**
 * The UI extension-point catalog: every named slot a host renders, with how
 * contributions combine and what the host hands them.
 *
 * Modelled on deepseek-harness's SlotMap: a slot is a published contract, not
 * a string that happens to match. The registry stays open — any name can be
 * registered — but a name with no spec renders nowhere, so a contribution to
 * an undeclared (misspelled, renamed or removed) slot is a silent no-op. The
 * catalog is what lets tests and tooling catch that.
 */

/**
 * How a slot combines its contributions.
 *
 * - ``list``   — every contribution renders, ordered by priority then
 *                registration (``SlotRenderer``).
 * - ``single`` — the first contribution by priority takes the region over
 *                and gets ``renderDefault`` to keep, wrap or adjust what
 *                would be there without it (the next contribution, then the
 *                host's default) — Claude Code's ``next(e)`` at a render
 *                site; none renders the default (``SingleSlot``).
 * - ``keyed``  — contributions carry a ``key`` (tab id, section id, approval
 *                subject…) and the host renders those matching what it is
 *                showing: one winner over a fallback (``KeyedSlot``), or all
 *                of them (``SlotRenderer slotKey``, ``useSlotRegistrations``).
 */
export type SlotKind = "list" | "single" | "keyed";

/** Where on screen a slot lives — for docs, tooling and review, not dispatch. */
export type SlotRegion =
  | "shell"
  | "sidebar"
  | "conversation"
  | "composer"
  | "tool-card"
  | "context-panel"
  | "project"
  | "task"
  | "resource"
  | "settings"
  | "notification"
  | "onboarding"
  | "domain";

export interface SlotSpec {
  /**
   * Slot name. A ``{param}`` segment matches any one dot-free segment, so
   * ``resource.{type}.actions`` covers ``resource.agent.actions`` and
   * ``resource.skill.actions``.
   */
  name: string;
  kind: SlotKind;
  region: SlotRegion;
  /** Where it renders and what it is for. */
  description: string;
  /** Props the host passes to every contribution. */
  context?: readonly string[];
}

const declared = new Map<string, SlotSpec>();
let patterns: Array<{ spec: SlotSpec; match: RegExp }> = [];

const isPattern = (name: string) => name.includes("{");

const toRegExp = (name: string) =>
  new RegExp(
    `^${name
      .split(/(\{[^}]+\})/)
      .map((part) =>
        part.startsWith("{")
          ? "[^.]+"
          : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
      )
      .join("")}$`,
  );

/**
 * Declare slots. Hosts declare theirs here at module load; an overlay that
 * renders its own slots (e.g. a settings page other editions contribute into)
 * declares them the same way. Re-declaring a name replaces its spec.
 */
export function declareSlots(specs: readonly SlotSpec[]): () => void {
  for (const spec of specs) {
    declared.set(spec.name, spec);
  }
  patterns = [...declared.values()]
    .filter((spec) => isPattern(spec.name))
    .map((spec) => ({ spec, match: toRegExp(spec.name) }));
  return () => {
    for (const spec of specs) {
      if (declared.get(spec.name) === spec) declared.delete(spec.name);
    }
    patterns = patterns.filter(({ spec }) => declared.get(spec.name) === spec);
  };
}

/** The spec a concrete slot name resolves to, exact names before patterns. */
export function findSlotSpec(name: string): SlotSpec | undefined {
  return (
    declared.get(name) ?? patterns.find(({ match }) => match.test(name))?.spec
  );
}

/** Every declared slot, in declaration order. */
export function listSlotSpecs(): SlotSpec[] {
  return [...declared.values()];
}

declareSlots(HOST_SLOTS);
