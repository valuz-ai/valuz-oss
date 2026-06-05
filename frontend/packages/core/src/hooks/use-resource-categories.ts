import { create } from "zustand";
import type { ResourceCategory } from "@valuz/shared";

interface CategoryRegistryState {
  /** Injected categories per resource type ("skill" | "agent" | "connector") */
  injected: Record<string, ResourceCategory<unknown>[]>;
  inject: (type: string, categories: ResourceCategory<unknown>[]) => void;
  remove: (type: string) => void;
}

export const useCategoryRegistry = create<CategoryRegistryState>((set) => ({
  injected: {},
  inject: (type, categories) =>
    set((s) => ({ injected: { ...s.injected, [type]: categories } })),
  remove: (type) =>
    set((s) => {
      const next = { ...s.injected };
      delete next[type];
      return { injected: next };
    }),
}));

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
