import { createElement, Fragment, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type {
  PluginRegistry as FirstPartyRegistry,
  ValuzPluginContext,
} from "@valuz/core";

import { adaptAppPlugin } from "../host/adapt";
import type { AdaptedPlugin, AppPluginItem } from "../host/adapt";
import type { HostServices } from "../services";
import { setHostServices } from "../services";
import type {
  ConfirmOptions,
  DraftConversationOptions,
  HostContextValue,
  PluginDefinition,
  ToastOptions,
  ValuzClient,
} from "../types";
import { mockValuz } from "./mock-valuz";

type Json = Record<string, unknown>;

interface SlotEntry {
  id: string;
  key?: string;
  priority?: number;
  label?: string;
  component: ComponentType<Json>;
}

/** The registry projection a test asserts on (same plain format as a composition snapshot). */
export interface TestCapture {
  /** ``{ slotName: [registration ids] }`` — ids are ``x:<plugin id>:<local id>``. */
  slots: Record<string, string[]>;
  routes: Json[];
  settings: Json[];
  nav: Json[];
}

export interface TestHostOptions {
  /** ``ctx.valuz``; defaults to an empty ``mockValuz()``. */
  valuz?: ValuzClient;
  /** The plugin's configuration values. */
  config?: Record<string, unknown>;
  /** Host language. Default ``en-US``. */
  locale?: string;
  /** The plugin's own ``locales`` (``{ "en-US": { key: "…" } }``). */
  locales?: Record<string, Record<string, unknown>>;
  /** Overrides of ``useHostContext()``. */
  hostContext?: Partial<HostContextValue>;
  /** What ``host.confirm`` answers. Default ``true``. */
  confirm?: boolean;
}

export interface TestHostCalls {
  navigate: Array<{ path: string; options?: { state?: unknown; replace?: boolean } }>;
  toast: ToastOptions[];
  confirm: ConfirmOptions[];
  openExternal: string[];
  copyText: string[];
  drafts: DraftConversationOptions[];
}

export interface TestHost {
  readonly valuz: ValuzClient;
  /** What the plugin asked the host to do (``host.navigate``, ``host.toast`` …). */
  readonly calls: TestHostCalls;
  /**
   * Load a plugin the way Valuz does: through the restricted context. Rejects
   * with the plugin's error (``PluginContractError`` for a contract
   * violation) after rolling back what it had registered.
   */
  load(plugin: PluginDefinition, item?: Partial<AppPluginItem>): Promise<void>;
  unload(): Promise<void>;
  capture(): TestCapture;
  /** Render every contribution to ``name`` (in slot order) to static markup. */
  renderSlot(name: string, props?: Json): string;
  /**
   * The (wrapped) components registered in ``name``, in slot order, for
   * rendering with your own library (``@testing-library/react``). Give them
   * the host's props.
   */
  getSlotComponents(name: string): ComponentType<Json>[];
  /** Render the page registered for ``/x/<id>/…`` (or its local route id). */
  renderRoute(pathOrId: string): string;
  /** Render a settings section by local id. */
  renderSettings(localId: string): string;
  setLocale(locale: string): void;
  setConfig(values: Record<string, unknown>): void;
  setHostContext(patch: Partial<HostContextValue>): void;
}

const plain = (value: unknown): Json =>
  JSON.parse(
    JSON.stringify(value, (_key, entry: unknown) =>
      typeof entry === "function" ? "[fn]" : entry,
    ),
  );

const unsupported = (method: string) => () => {
  throw new Error(`the test host does not support registry.${method}`);
};

/**
 * A fresh host for plugin tests (doc 12 §7): loads the plugin through the same
 * adapter as Valuz — so contract violations fail here too — without starting
 * the app.
 */
export function createTestHost(options: TestHostOptions = {}): TestHost {
  const valuz = options.valuz ?? mockValuz();
  let locale = options.locale ?? "en-US";
  let hostContext: HostContextValue = {
    locale,
    theme: "light",
    edition: "personal",
    deployment: "local",
    orgId: null,
    ...options.hostContext,
  };
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((listener) => listener());

  const calls: TestHostCalls = {
    navigate: [],
    toast: [],
    confirm: [],
    openExternal: [],
    copyText: [],
    drafts: [],
  };

  const services: HostServices = {
    request: () =>
      Promise.reject(
        new Error("the test host has no network: use mockValuz() for ctx.valuz"),
      ),
    navigate: (path, navOptions) => {
      calls.navigate.push({ path, options: navOptions });
    },
    toast: (toastOptions) => {
      calls.toast.push(toastOptions);
    },
    confirm: async (confirmOptions) => {
      calls.confirm.push(confirmOptions);
      return options.confirm ?? true;
    },
    openExternal: (url) => {
      calls.openExternal.push(url);
    },
    copyText: (text) => {
      calls.copyText.push(text);
    },
    draftConversation: (draft) => {
      calls.drafts.push(draft);
    },
    getLocale: () => locale,
    subscribeLocale: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getHostContext: () => hostContext,
    subscribeHostContext: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };

  const slots = new Map<string, SlotEntry[]>();
  const routes: Json[] = [];
  const settings: Json[] = [];
  const nav: Json[] = [];
  const disposers: Array<() => void> = [];
  let adapted: AdaptedPlugin | null = null;

  const track = <T,>(list: T[], entry: T): void => {
    list.push(entry);
    disposers.push(() => {
      const at = list.indexOf(entry);
      if (at >= 0) list.splice(at, 1);
    });
  };

  const makeContext = (): ValuzPluginContext => {
    const registry = {
      slot(name: string, registration: SlotEntry) {
        const list = slots.get(name) ?? [];
        slots.set(name, list);
        track(list, registration);
      },
      route: (route: Json) => track(routes, route),
      settingsSection: (section: Json) => track(settings, section),
      navItem: (item: Json) => track(nav, item),
      removeRoute: unsupported("removeRoute"),
      removeSettingsSection: unsupported("removeSettingsSection"),
      projectPanel: unsupported("projectPanel"),
      removeNavItem: unsupported("removeNavItem"),
      navGroup: unsupported("navGroup"),
      service: unsupported("service"),
      categories: unsupported("categories"),
      suppress: unsupported("suppress"),
      capabilities: unsupported("capabilities"),
    } as unknown as FirstPartyRegistry;

    const ctx: ValuzPluginContext = {
      pluginId: adapted?.id ?? "test",
      registry,
      effect(setup) {
        const dispose = setup();
        if (dispose) disposers.push(dispose);
      },
      legacy: unsupported("legacy"),
      async plugin(child) {
        const childContext = makeContext();
        await child.apply(childContext);
        return { id: child.id, status: "active", legacy: [] };
      },
    };
    return ctx;
  };

  const rollback = (): void => {
    for (const dispose of disposers.splice(0).reverse()) dispose();
  };

  const sortedSlot = (name: string): SlotEntry[] =>
    [...(slots.get(name) ?? [])]
      .map((entry, index) => ({ entry, index }))
      .sort(
        (a, b) =>
          (a.entry.priority ?? 0) - (b.entry.priority ?? 0) || a.index - b.index,
      )
      .map(({ entry }) => entry);

  const render = (components: ComponentType<Json>[], props: Json): string =>
    renderToStaticMarkup(
      createElement(
        Fragment,
        null,
        ...components.map((Component, index) =>
          createElement(Component, { ...props, key: index }),
        ),
      ),
    );

  return {
    valuz,
    calls,

    async load(plugin, item = {}) {
      if (adapted) throw new Error("the test host already has a plugin loaded");
      setHostServices(services);
      const config = options.config ?? {};
      const candidate = adaptAppPlugin(
        plugin,
        {
          id: plugin?.id,
          name: plugin?.id,
          locales: options.locales,
          ...item,
        },
        services,
        { config, valuz },
      );
      adapted = candidate;
      try {
        await candidate.apply(makeContext());
      } catch (error) {
        rollback();
        adapted = null;
        throw error;
      }
    },

    async unload() {
      rollback();
      adapted = null;
    },

    capture() {
      return {
        slots: Object.fromEntries(
          [...slots.keys()]
            .sort()
            .filter((name) => (slots.get(name) ?? []).length > 0)
            .map((name) => [name, sortedSlot(name).map((entry) => entry.id)]),
        ),
        routes: routes.map(plain),
        settings: settings.map(plain),
        nav: nav.map(plain),
      };
    },

    renderSlot(name, props = {}) {
      return render(
        sortedSlot(name).map((entry) => entry.component),
        props,
      );
    },

    getSlotComponents(name) {
      return sortedSlot(name).map((entry) => entry.component);
    },

    renderRoute(pathOrId) {
      const found = routes.find(
        (route) =>
          route.path === pathOrId ||
          route.id === pathOrId ||
          route.id === `x:${adapted?.id}:${pathOrId}`,
      );
      if (!found) throw new Error(`no page registered for "${pathOrId}"`);
      return render([found.component as ComponentType<Json>], {});
    },

    renderSettings(localId) {
      const found = settings.find(
        (section) =>
          section.id === localId || section.id === `x:${adapted?.id}:${localId}`,
      );
      if (!found) throw new Error(`no settings section "${localId}"`);
      return render([found.component as ComponentType<Json>], {});
    },

    setLocale(next) {
      locale = next;
      hostContext = { ...hostContext, locale: next };
      notify();
    },
    setConfig(values) {
      adapted?.scope.config.set(values);
    },
    setHostContext(patch) {
      hostContext = { ...hostContext, ...patch };
      notify();
    },
  };
}
