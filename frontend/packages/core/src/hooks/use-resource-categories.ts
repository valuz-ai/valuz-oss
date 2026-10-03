import { create } from "zustand";
import type { ResourceCategory } from "@valuz/shared";

type Contributions = Record<string, Array<{ contributor: string; categories: ResourceCategory<unknown>[] }>>;

interface CategoryRegistryState {
  /**
   * Effective injected categories per resource type ("skill" | "agent" |
   * "connector" | "kb" …): every contributor's list merged in contribution
   * order, a later category replacing an earlier one with the same id.
   * Readers (``useResourceCategories``) only ever look at this.
   */
  injected: Record<string, ResourceCategory<unknown>[]>;
  /** Raw per-contributor lists that ``injected`` is derived from. */
  contributions: Contributions;
  /**
   * Add ``contributor``'s categories for ``type`` (replacing that
   * contributor's previous list for the type) and return a disposer that
   * withdraws exactly them. Independent features — or plugins — contribute
   * side by side without overwriting each other.
   */
  contribute: (
    type: string,
    contributor: string,
    categories: ResourceCategory<unknown>[],
  ) => () => void;
  /**
   * Compatibility entry point: one anonymous contributor per type, so a second
   * ``inject`` for the same type replaces the first. New code should
   * ``contribute`` under its own contributor id instead.
   */
  inject: (type: string, categories: ResourceCategory<unknown>[]) => void;
  /** Drop every contribution for ``type``. */
  remove: (type: string) => void;
}

const LEGACY_INJECT = "inject";

function merge(lists: Contributions[string] | undefined): ResourceCategory<unknown>[] | undefined {
  if (!lists || lists.length === 0) return undefined;
  const byId = new Map<string, ResourceCategory<unknown>>();
  for (const { categories } of lists) {
    for (const category of categories) {
      byId.delete(category.id); // a later contribution moves to the end
      byId.set(category.id, category);
    }
  }
  return [...byId.values()];
}

function withType(
  injected: Record<string, ResourceCategory<unknown>[]>,
  type: string,
  merged: ResourceCategory<unknown>[] | undefined,
): Record<string, ResourceCategory<unknown>[]> {
  const next = { ...injected };
  if (merged) next[type] = merged;
  else delete next[type];
  return next;
}

export const useCategoryRegistry = create<CategoryRegistryState>((set, get) => {
  const writeType = (type: string, lists: Contributions[string]) =>
    set((s) => {
      const contributions = { ...s.contributions };
      if (lists.length === 0) delete contributions[type];
      else contributions[type] = lists;
      return {
        contributions,
        injected: withType(s.injected, type, merge(lists)),
      };
    });

  return {
    injected: {},
    contributions: {},
    contribute: (type, contributor, categories) => {
      const entry = { contributor, categories };
      const others = (get().contributions[type] ?? []).filter(
        (c) => c.contributor !== contributor,
      );
      writeType(type, [...others, entry]);
      return () => {
        const current = get().contributions[type] ?? [];
        if (!current.includes(entry)) return; // replaced or removed since
        writeType(
          type,
          current.filter((c) => c !== entry),
        );
      };
    },
    inject: (type, categories) => {
      get().contribute(type, LEGACY_INJECT, categories);
    },
    remove: (type) => writeType(type, []),
  };
});

/**
 * Merge built-in categories with injected ones.
 * Injected categories with the same `id` override built-in ones.
 * New injected categories are appended.
 */
export function useResourceCategories<T>(
  type: string,
  builtIn: ResourceCategory<T>[],
): ResourceCategory<T>[] {
  const injected =
    (useCategoryRegistry((s) => s.injected[type]) as
      | ResourceCategory<T>[]
      | undefined) ?? [];
  const injectedMap = new Map(injected.map((c) => [c.id, c]));
  const merged = builtIn.map((c) =>
    injectedMap.has(c.id) ? injectedMap.get(c.id)! : c,
  );
  for (const c of injected) {
    if (!merged.some((m) => m.id === c.id)) merged.push(c);
  }
  return merged.sort((a, b) => a.order - b.order);
}
