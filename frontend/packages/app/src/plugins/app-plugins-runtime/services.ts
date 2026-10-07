import { legacyAppPluginLocaleNamespace } from "@valuz/plugin-sdk/host";
import { toast } from "sonner";
import {
  getAppPluginsApiBase,
  requestJson,
  useRegistryStore,
} from "@valuz/core";
import {
  getLocale,
  registerLocaleNamespace,
  subscribe as subscribeLocale,
  type LocaleCode,
} from "@valuz/shared/i18n";
import type { HostServices } from "@valuz/plugin-sdk/host";

/** What the mounted {@link AppPluginBridge} gives the services (router, dialog). */
export interface AppPluginBridgeHandles {
  navigate:
    | ((path: string, options?: { state?: unknown; replace?: boolean }) => void)
    | null;
  confirm:
    | ((options: {
        title: string;
        body?: string;
        confirmLabel?: string;
      }) => Promise<boolean>)
    | null;
}

export const appPluginBridge: AppPluginBridgeHandles = {
  navigate: null,
  confirm: null,
};

let orgIdProvider: () => string | null = () => null;

/**
 * Editions with organisations tell plugins which one is active
 * (``useHostContext().orgId``). OSS has none, so the default is ``null``.
 */
export function setAppPluginOrgIdProvider(
  provider: (() => string | null) | null,
): void {
  orgIdProvider = provider ?? (() => null);
}

const toQuery = (query: Record<string, unknown> | undefined): string => {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
};

/** The desktop shell's open-external IPC, else a new browser tab. */
async function openExternalUrl(url: string): Promise<void> {
  const desktop = (
    window as Window & {
      valuzDesktop?: {
        invoke: <T>(
          channel: string,
          payload?: Record<string, unknown>,
        ) => Promise<T>;
      };
    }
  ).valuzDesktop;
  if (desktop) {
    try {
      if (await desktop.invoke<boolean>("open_external_url", { url })) return;
    } catch {
      // fall through to the browser
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function navigateWithoutRouter(path: string, state?: unknown): void {
  const hashRouted =
    window.location.protocol === "file:" || window.location.hash.startsWith("#/");
  if (hashRouted) {
    window.location.hash = path;
    return;
  }
  window.history.pushState(state ?? null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

const isDark = (): boolean =>
  typeof document !== "undefined" &&
  document.documentElement.classList.contains("dark");

/**
 * The host services the plugin SDK runs on: the app's authenticated request
 * layer (pointed at the local backend), router, toast, confirm dialog,
 * clipboard and language.
 */
export function createAppPluginHostServices(): HostServices {
  const navigate: HostServices["navigate"] = (path, options) => {
    if (appPluginBridge.navigate) appPluginBridge.navigate(path, options);
    else navigateWithoutRouter(path, options?.state);
  };

  return {
    request: ({ method, path, json, query, headers }) =>
      requestJson(`${path}${toQuery(query)}`, {
        baseUrl: getAppPluginsApiBase(),
        method,
        json,
        headers,
      }),

    navigate,

    toast({ title, description, variant }) {
      const options = description ? { description } : undefined;
      if (variant === "success") toast.success(title, options);
      else if (variant === "error") toast.error(title, options);
      else if (variant === "warning") toast.warning(title, options);
      else toast(title, options);
    },

    async confirm(options) {
      if (appPluginBridge.confirm) return appPluginBridge.confirm(options);
      return window.confirm(
        options.body ? `${options.title}\n\n${options.body}` : options.title,
      );
    },

    openExternal: openExternalUrl,

    async copyText(text) {
      await navigator.clipboard.writeText(text);
    },

    draftConversation({ projectId, agent, text }) {
      const params = new URLSearchParams();
      if (projectId) params.set("project", projectId);
      if (agent) params.set("agent", agent);
      const query = params.toString();
      // The new-conversation page fills the composer from ``location.state.draft``.
      navigate(`/conversation/new${query ? `?${query}` : ""}`, {
        state: { draft: text },
      });
    },

    getLocale: () => getLocale(),
    subscribeLocale: (listener) => subscribeLocale(listener),

    getHostContext: () => ({
      locale: getLocale(),
      theme: isDark() ? "dark" : "light",
      edition: useRegistryStore.getState().edition,
      deployment: "local",
      orgId: orgIdProvider(),
    }),

    subscribeHostContext(listener) {
      const offStore = useRegistryStore.subscribe(listener);
      let observer: MutationObserver | null = null;
      if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
        observer = new MutationObserver(listener);
        observer.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ["class"],
        });
      }
      return () => {
        offStore();
        observer?.disconnect();
      };
    },

    registerLocales(pluginId, locales) {
      for (const [locale, tree] of Object.entries(locales)) {
        registerLocaleNamespace(`appPlugin.${pluginId}`, locale as LocaleCode, tree);
        registerLocaleNamespace(legacyAppPluginLocaleNamespace(pluginId), locale as LocaleCode, tree);
      }
    },
  };
}
