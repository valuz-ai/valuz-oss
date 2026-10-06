/* eslint-disable react-refresh/only-export-components -- an SDK runtime module: it exports hooks, helpers and wrapper components together, and is never hot-reloaded as a component file */
import {
  createContext,
  createElement,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import type { Translate } from "./i18n";
import type { HostContextValue, PluginLog, ValuzClient } from "./types";

/** Observable plugin configuration (``usePluginConfig`` re-renders on change). */
export interface ConfigStore {
  get(): Readonly<Record<string, unknown>>;
  /** Replace the values; listeners run only when something changed. */
  set(values: Record<string, unknown>): boolean;
  subscribe(listener: () => void): () => void;
}

export function createConfigStore(
  initial: Record<string, unknown> = {},
): ConfigStore {
  let current: Readonly<Record<string, unknown>> = Object.freeze({ ...initial });
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    set(values) {
      if (JSON.stringify(values) === JSON.stringify(current)) return false;
      current = Object.freeze({ ...values });
      for (const listener of [...listeners]) listener();
      return true;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** What the hooks read: one per loaded plugin, provided around every contribution. */
export interface PluginScope {
  readonly pluginId: string;
  readonly valuz: ValuzClient;
  readonly log: PluginLog;
  readonly config: ConfigStore;
  readonly t: Translate;
  getLocale(): string;
  subscribeLocale(listener: () => void): () => void;
  getHostContext(): HostContextValue;
  subscribeHostContext(listener: () => void): () => void;
}

const ScopeContext = createContext<PluginScope | null>(null);

export function PluginScopeProvider(props: {
  scope: PluginScope;
  children?: ReactNode;
}) {
  return createElement(
    ScopeContext.Provider,
    { value: props.scope },
    props.children,
  );
}

function useScope(hook: string): PluginScope {
  const scope = useContext(ScopeContext);
  if (!scope) {
    throw new Error(
      `${hook}() must be called from a component the plugin registered (inside a slot, page or settings section)`,
    );
  }
  return scope;
}

/** The plugin's own ``t`` (locales of its package), following the host language. */
export function useTranslation(): { t: Translate; locale: string } {
  const scope = useScope("useTranslation");
  const locale = useSyncExternalStore(
    scope.subscribeLocale,
    scope.getLocale,
    scope.getLocale,
  );
  // ``locale`` is a dependency on purpose: ``t`` reads the current locale, so a
  // new identity per language makes memoised children re-translate.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const t = useMemo<Translate>(() => (key, params) => scope.t(key, params), [scope, locale]);
  return { t, locale };
}

/** ``ctx.valuz`` for components. */
export function useValuz(): ValuzClient {
  return useScope("useValuz").valuz;
}

/** The plugin's configuration; re-renders when it changes. */
export function usePluginConfig<
  T extends Record<string, unknown> = Record<string, unknown>,
>(): Readonly<T> {
  const scope = useScope("usePluginConfig");
  return useSyncExternalStore(
    scope.config.subscribe,
    scope.config.get,
    scope.config.get,
  ) as Readonly<T>;
}

/** Language, theme, edition, deployment and the active organisation. */
export function useHostContext(): HostContextValue {
  const scope = useScope("useHostContext");
  return useSyncExternalStore(
    scope.subscribeHostContext,
    scope.getHostContext,
    scope.getHostContext,
  );
}
