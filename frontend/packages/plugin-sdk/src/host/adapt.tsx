/* eslint-disable react-refresh/only-export-components -- an SDK runtime module: it exports hooks, helpers and wrapper components together, and is never hot-reloaded as a component file */
import type { ComponentType } from "react";
import type { ValuzPlugin, ValuzPluginContext } from "@valuz/core";

import { createTranslator, hasLocaleKey, type PluginLocales } from "../i18n";
import { createPluginLog } from "../log";
import {
  createConfigStore,
  PluginScopeProvider,
  type PluginScope,
} from "../scope";
import { setHostServices, type HostServices } from "../services";
import {
  FIXED_PUBLIC_SLOTS,
  KEYED_PUBLIC_SLOTS,
  type HostContextValue,
  type NavItemSpec,
  type PluginContext,
  type PluginDefinition,
  type PluginRegistry,
  type RouteSpec,
  type SettingsSectionSpec,
  type SlotName,
  type SlotRegistration,
  type ValuzClient,
} from "../types";
import { createValuzClient } from "../valuz-client";
import { ExtensionBoundary } from "./boundary";
import { adaptSlotProps } from "./convert";

/** The third-party plugin went outside the public extension surface. */
export class PluginContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PluginContractError";
  }
}

/** What the adapter needs of an entry of ``GET /v1/extensions/third-party``. */
export interface ThirdPartyPluginItem {
  id: string;
  version?: string;
  name?: string | Record<string, string>;
  locales?: Record<string, Record<string, unknown>>;
  permissions?: string[];
  revision?: number;
}

export interface AdaptOptions {
  /** The plugin's configuration as the loader read it before ``apply``. */
  config?: Record<string, unknown>;
  /** A stand-in for ``ctx.valuz`` (the test host's ``mockValuz``). */
  valuz?: ValuzClient;
}

/** A first-party plugin built from a third-party definition. */
export interface AdaptedPlugin extends ValuzPlugin {
  /** Config store, log and client of this plugin (the loader updates config). */
  readonly scope: PluginScope;
}

/** Settings sections of third-party plugins live in this group. */
export const EXTENSIONS_SETTINGS_GROUP = {
  id: "extensions",
  label: "extensions.title",
} as const;

const RESOURCE_ACTIONS_SLOT = /^resource\.[a-z0-9_-]+\.actions$/i;
const MAX_LOCAL_ID = 100;

/** Whether ``name`` is one of the 12 public slots (families by prefix). */
export function isPublicSlot(name: string): boolean {
  if ((FIXED_PUBLIC_SLOTS as readonly string[]).includes(name)) return true;
  if (name.startsWith("conversation.tool-card.")) {
    return name.length > "conversation.tool-card.".length;
  }
  return RESOURCE_ACTIONS_SLOT.test(name);
}

const FIRST_PARTY_ONLY = [
  "removeRoute",
  "removeSettingsSection",
  "projectPanel",
  "removeNavItem",
  "navGroup",
  "service",
  "categories",
  "suppress",
  "capabilities",
] as const;

const localizedName = (
  name: ThirdPartyPluginItem["name"],
  locale: string,
  fallback: string,
): string => {
  if (typeof name === "string") return name;
  if (name && typeof name === "object") {
    return name[locale] ?? name["en-US"] ?? Object.values(name)[0] ?? fallback;
  }
  return fallback;
};

const BASE_HOST_CONTEXT: HostContextValue = {
  locale: "zh-CN",
  theme: "light",
  edition: "personal",
  deployment: "local",
  orgId: null,
};

/**
 * Turn a third-party plugin definition into a first-party ``ValuzPlugin`` whose
 * ``apply`` builds the restricted context (doc 02 §3, doc 12 §3):
 *
 * - only the 12 public slots, routes under ``/x/<id>/``, settings sections in
 *   the Extensions group, nav items to the plugin's own pages — anything else
 *   throws ``PluginContractError``, so only this plugin fails and rolls back;
 * - ids are namespaced ``x:<plugin id>:<local id>``;
 * - every contributed component is wrapped in an error boundary and a scope
 *   (plugin id, ``t``, config, ``valuz``) the SDK hooks read.
 */
export function adaptThirdPartyPlugin(
  definition: PluginDefinition,
  item: ThirdPartyPluginItem,
  services: HostServices,
  options: AdaptOptions = {},
): AdaptedPlugin {
  if (
    !definition ||
    typeof definition !== "object" ||
    typeof definition.apply !== "function"
  ) {
    throw new PluginContractError(
      `plugin "${item.id}" does not default-export a definePlugin({ id, apply }) value`,
    );
  }
  if (definition.id !== item.id) {
    throw new PluginContractError(
      `plugin id "${String(definition.id)}" does not match the manifest id "${item.id}"`,
    );
  }

  const rootId = item.id;
  const locales: PluginLocales = item.locales ?? {};
  const config = createConfigStore(options.config ?? {});
  const log = createPluginLog(rootId, (request) => services.request(request));
  const valuz = options.valuz ?? createValuzClient({ pluginId: rootId, services });

  let hostContext: HostContextValue = BASE_HOST_CONTEXT;
  const readHostContext = (): HostContextValue => {
    const next = services.getHostContext();
    if (JSON.stringify(next) !== JSON.stringify(hostContext)) hostContext = next;
    return hostContext;
  };
  readHostContext();

  const scope: PluginScope = {
    pluginId: rootId,
    valuz,
    log,
    config,
    t: createTranslator(locales, () => services.getLocale()),
    getLocale: () => services.getLocale(),
    subscribeLocale: (listener) => services.subscribeLocale(listener),
    getHostContext: readHostContext,
    subscribeHostContext: (listener) => {
      const offLocale = services.subscribeLocale(listener);
      const offHost = services.subscribeHostContext?.(listener);
      return () => {
        offLocale();
        offHost?.();
      };
    },
  };

  const ns = (kind: string, local: string): string => {
    if (typeof local !== "string" || local.length === 0) {
      throw new PluginContractError(`${kind}: id must be a non-empty string`);
    }
    if (local.length > MAX_LOCAL_ID || /\s/.test(local)) {
      throw new PluginContractError(`${kind}: invalid id "${local}"`);
    }
    return `x:${rootId}:${local}`;
  };

  const text = (key: string | undefined): string =>
    key === undefined || key === ""
      ? ""
      : hasLocaleKey(locales, key)
        ? `ext.${rootId}.${key}`
        : key;

  const ownPath = (kind: string, path: string): void => {
    const base = `/x/${rootId}`;
    const valid =
      typeof path === "string" &&
      (path === base || path.startsWith(`${base}/`)) &&
      !/[?#]/.test(path) &&
      !path.split("/").some((segment) => segment === "." || segment === "..");
    if (!valid) {
      throw new PluginContractError(
        `${kind}: path "${String(path)}" is outside "${base}/"`,
      );
    }
  };

  const wrap = <P extends object>(
    Component: ComponentType<P>,
    where: string,
    convert?: (hostProps: Record<string, unknown>) => P,
  ): ComponentType<Record<string, unknown>> => {
    const Wrapped = (hostProps: Record<string, unknown>) => {
      const props = (convert ? convert(hostProps) : {}) as P;
      return (
        <ExtensionBoundary
          pluginId={rootId}
          where={where}
          onError={(message) => log.error(message)}
        >
          <PluginScopeProvider scope={scope}>
            <div data-valuz-ext={rootId} style={{ display: "contents" }}>
              <Component {...props} />
            </div>
          </PluginScopeProvider>
        </ExtensionBoundary>
      );
    };
    Wrapped.displayName = `Extension(${rootId}:${where})`;
    return Wrapped;
  };

  const buildContext = (host: ValuzPluginContext): PluginContext => {
    const registry = {
      slot<N extends SlotName>(name: N, registration: SlotRegistration<N>) {
        if (!isPublicSlot(name)) {
          throw new PluginContractError(
            `slot "${String(name)}" is not a public slot of plugin API 1.x`,
          );
        }
        if (
          !registration ||
          typeof registration.component !== "function"
        ) {
          throw new PluginContractError(
            `slot "${name}": registration.component must be a component`,
          );
        }
        const id = ns(`slot "${name}"`, registration.id);
        const keyed = KEYED_PUBLIC_SLOTS.includes(name);
        let key: string | undefined;
        if (keyed) {
          if (!registration.key) {
            throw new PluginContractError(
              `slot "${name}" is keyed: registration.key is required`,
            );
          }
          key = ns(`slot "${name}" key`, registration.key);
        }
        host.registry.slot(name, {
          id,
          component: wrap(
            registration.component as unknown as ComponentType<Record<string, unknown>>,
            `slot ${name}`,
            (hostProps) => adaptSlotProps(name, hostProps),
          ),
          priority: registration.priority,
          key,
          label: keyed
            ? registration.label
              ? text(registration.label)
              : localizedName(item.name, services.getLocale(), rootId)
            : undefined,
        });
      },

      route(route: RouteSpec, component: ComponentType) {
        const id = ns("route", route?.id);
        ownPath("route", route.path);
        if (typeof component !== "function") {
          throw new PluginContractError(`route "${route.id}": component is required`);
        }
        host.registry.route({
          id,
          path: route.path,
          label: text(route.title),
          // The host translates ``description`` unconditionally: never hand
          // it an empty key.
          description: text(route.description || route.title),
          layout: "project",
          showInNav: false,
          component: wrap(component, `page ${route.id}`),
          edition: "personal",
        });
      },

      settingsSection(section: SettingsSectionSpec, component: ComponentType) {
        const id = ns("settings section", section?.id);
        if (typeof component !== "function") {
          throw new PluginContractError(
            `settings section "${section.id}": component is required`,
          );
        }
        host.registry.settingsSection({
          id,
          label: text(section.title),
          description: text(section.description || section.title),
          icon: section.icon ?? "puzzle",
          group: { ...EXTENSIONS_SETTINGS_GROUP },
          component: wrap(component, `settings ${section.id}`),
          edition: "personal",
        });
      },

      navItem(navItem: NavItemSpec) {
        const id = ns("nav item", navItem?.id);
        ownPath("nav item", navItem.path);
        host.registry.navItem({
          id,
          label: text(navItem.label),
          href: navItem.path,
          position: "top",
          navGroup: "library",
          icon: navItem.icon ?? "plugins",
          edition: "personal",
        });
      },
    } satisfies PluginRegistry;

    // Everything else of the first-party registry is refused, by name, so a
    // plugin written against the first-party types fails with a clear reason.
    for (const method of FIRST_PARTY_ONLY) {
      Object.defineProperty(registry, method, {
        enumerable: false,
        value: () => {
          throw new PluginContractError(
            `registry.${method} is not available to third-party plugins`,
          );
        },
      });
    }

    const context: PluginContext = {
      pluginId: rootId,
      registry,
      effect: (setup) => host.effect(setup),
      plugin: async (child) => {
        if (!child || typeof child.apply !== "function" || !child.id) {
          throw new PluginContractError("plugin(child): child must be a plugin definition");
        }
        const record = await host.plugin({
          id: `${rootId}:${child.id}`,
          apply: (childHost) => child.apply(buildContext(childHost)),
        });
        if (record.status === "failed") {
          throw record.error instanceof Error
            ? record.error
            : new PluginContractError(`child plugin "${child.id}" failed`);
        }
      },
      valuz,
      get config() {
        return config.get();
      },
      log,
    };
    Object.defineProperty(context, "legacy", {
      enumerable: false,
      value: () => {
        throw new PluginContractError("ctx.legacy is not available to third-party plugins");
      },
    });
    return context;
  };

  return {
    id: rootId,
    scope,
    async apply(host) {
      setHostServices(services);
      if (item.locales && Object.keys(item.locales).length > 0) {
        services.registerLocales?.(rootId, item.locales);
      }
      await definition.apply(buildContext(host));
    },
  };
}
