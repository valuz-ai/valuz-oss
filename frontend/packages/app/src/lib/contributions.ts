import { useSyncExternalStore } from "react";

/**
 * A small keyed registry for things a plugin contributes that are NOT part of
 * the edition registry: the component behind a route or a settings section,
 * a block on the Extensions page.
 *
 * Why it is separate from ``useRegistryStore``: the registry holds plain data
 * that a composition snapshot can serialize and compare across editions.
 * Components and page blocks are the app shell's rendering concern, so they
 * live next to the code that renders them and are tied to a plugin's lifetime
 * the same way (``ctx.effect`` — register on load, dispose on unload).
 *
 * Several registrations may share a key (an overlay replacing a page); the
 * latest wins, and disposing it brings the previous one back.
 */
export interface Contribution<V> {
  readonly key: string;
  readonly value: V;
}

export interface Contributions<V> {
  /** Register ``value`` under ``key``; the returned disposer withdraws it. */
  register(key: string, value: V, options?: { order?: number }): () => void;
  /** The winning value for ``key``, if any. */
  get(key: string): V | undefined;
  /**
   * The winning registration of every key, by ``order`` (lower first; default
   * 0) then registration order. The same array is returned until something
   * changes.
   */
  entries(): readonly Contribution<V>[];
  subscribe(listener: () => void): () => void;
}

interface Registration<V> {
  key: string;
  value: V;
  order: number;
  seq: number;
}

export function createContributions<V>(): Contributions<V> {
  let registrations: Registration<V>[] = [];
  let seq = 0;
  let snapshot: readonly Contribution<V>[] = [];
  const listeners = new Set<() => void>();

  const rebuild = () => {
    const winners = new Map<string, Registration<V>>();
    for (const entry of registrations) winners.set(entry.key, entry);
    snapshot = [...winners.values()]
      .sort((a, b) => a.order - b.order || a.seq - b.seq)
      .map(({ key, value }) => ({ key, value }));
    for (const listener of listeners) listener();
  };

  return {
    register(key, value, options) {
      const entry: Registration<V> = {
        key,
        value,
        order: options?.order ?? 0,
        seq: (seq += 1),
      };
      registrations = [...registrations, entry];
      rebuild();
      return () => {
        if (!registrations.includes(entry)) return;
        registrations = registrations.filter((other) => other !== entry);
        rebuild();
      };
    },
    get(key) {
      for (let at = registrations.length - 1; at >= 0; at -= 1) {
        if (registrations[at].key === key) return registrations[at].value;
      }
      return undefined;
    },
    entries: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** Subscribe a component to the registry's winning registrations. */
export function useContributions<V>(
  registry: Contributions<V>,
): readonly Contribution<V>[] {
  return useSyncExternalStore(
    registry.subscribe,
    registry.entries,
    registry.entries,
  );
}
