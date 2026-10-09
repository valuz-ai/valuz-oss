/**
 * Layered registry lists: a BASE (what an edition profile declares) with an
 * ordered stack of LAYER operations on top (what plugins register at runtime).
 *
 * The visible list is always derived — base first, then every layer applied in
 * registration order — never edited in place. That is what makes the registry
 * safe to rebase (``hydrate`` swaps the base and the plugins' contributions
 * stay) and what makes a registration reversible (disposing it removes its
 * layer entry and the list is derived again, so a replaced base entry is back
 * in its original position without any restore bookkeeping).
 */

/**
 * Where a NEW entry goes, relative to entries already in the list. Applies
 * only when the id is not in the list yet — an entry that replaces an existing
 * id keeps that id's position.
 *
 * - ``after`` / ``before`` name an anchor id, or several in order of
 *   preference — the first one that is in the list wins, so an entry can say
 *   "after my predecessor, or after the one before it if that is not loaded".
 *   When both are given ``after`` wins if any of its anchors resolves,
 *   otherwise ``before`` is tried.
 * - Entries registered later land closer to a shared ``after`` anchor, so a
 *   plugin that needs several entries in a fixed order chains them
 *   (``b`` after ``a``, ``c`` after ``b``).
 * - An anchor that is not in the list yet (its plugin loads later, or it is
 *   absent from this build) does not fail the registration: the entry waits
 *   and is placed as soon as an anchor appears, and goes to the end if none
 *   ever does.
 */
export interface Placement {
  before?: string | readonly string[];
  after?: string | readonly string[];
}

/** One entry of a layer stack. Identity (the object itself) is what a disposer removes. */
export type LayerOp<T> =
  | {
      readonly op: "put";
      readonly item: T;
      /** Where a new id goes; ignored when ``item`` replaces an existing id. */
      readonly placement?: Placement;
    }
  | {
      readonly op: "remove";
      /** Hide the entry with this key while the layer is present. */
      readonly key: string;
    };

/**
 * Derive the visible list: ``base`` with ``layers`` applied in order.
 *
 * - ``put`` with a key already in the list replaces that entry in place;
 *   otherwise it is inserted at its placement, or appended.
 * - ``remove`` takes the key out of the list as it stands at that point — a
 *   later ``put`` of the same key brings it back (at the end, or its
 *   placement), which is how a remove-then-reregister reorders an entry.
 */
export function applyLayers<T>(
  base: readonly T[],
  layers: readonly LayerOp<T>[],
  keyOf: (item: T) => string,
): T[] {
  const list = base.slice();
  let waiting: Array<{ item: T; placement: Placement }> = [];

  const indexOf = (key: string) =>
    list.findIndex((entry) => keyOf(entry) === key);

  /** Index of the first anchor of ``anchors`` that is in the list, or -1. */
  const firstAnchor = (anchors: string | readonly string[] | undefined) => {
    if (anchors === undefined) return -1;
    for (const anchor of typeof anchors === "string" ? [anchors] : anchors) {
      const at = indexOf(anchor);
      if (at !== -1) return at;
    }
    return -1;
  };

  /** Index a placement resolves to right now, or -1 when no anchor exists. */
  const slotFor = (placement: Placement): number => {
    const after = firstAnchor(placement.after);
    if (after !== -1) return after + 1;
    return firstAnchor(placement.before);
  };

  /** Place every waiting entry whose anchor has appeared (they can chain). */
  const flushWaiting = () => {
    let placed = true;
    while (placed && waiting.length > 0) {
      placed = false;
      for (let w = 0; w < waiting.length; w += 1) {
        const at = slotFor(waiting[w].placement);
        if (at === -1) continue;
        list.splice(at, 0, waiting[w].item);
        waiting.splice(w, 1);
        placed = true;
        break; // the list moved: restart the scan
      }
    }
  };

  for (const layer of layers) {
    if (layer.op === "remove") {
      for (let at = list.length - 1; at >= 0; at -= 1) {
        if (keyOf(list[at]) === layer.key) list.splice(at, 1);
      }
      waiting = waiting.filter((entry) => keyOf(entry.item) !== layer.key);
      continue;
    }

    const key = keyOf(layer.item);
    const existing = indexOf(key);
    if (existing !== -1) {
      list[existing] = layer.item;
      continue;
    }
    // A newer registration supersedes one still waiting for its anchor.
    waiting = waiting.filter((entry) => keyOf(entry.item) !== key);

    if (!layer.placement) {
      list.push(layer.item);
    } else {
      const at = slotFor(layer.placement);
      if (at === -1) {
        waiting.push({ item: layer.item, placement: layer.placement });
        continue;
      }
      list.splice(at, 0, layer.item);
    }
    flushWaiting();
  }

  for (const entry of waiting) list.push(entry.item);
  return list;
}
