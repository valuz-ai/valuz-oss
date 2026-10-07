import type {
  ConfirmOptions,
  DraftConversationOptions,
  HostContextValue,
  ToastOptions,
} from "./types";

export interface HostRequest {
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  /** Relative to the API base: ``/v1/projects``. */
  path: string;
  json?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
}

/**
 * What the host app provides to the SDK runtime. The loader builds one of
 * these from the app's router, request layer, toast, dialog and clipboard; the
 * test host builds one from mocks. The SDK never imports host packages.
 */
export interface HostServices {
  /** The host's authenticated request layer, pointed at the local backend. */
  request(request: HostRequest): Promise<unknown>;
  navigate(
    path: string,
    options?: { state?: unknown; replace?: boolean },
  ): void;
  toast(options: ToastOptions): void;
  confirm(options: ConfirmOptions): Promise<boolean>;
  openExternal(url: string): void | Promise<void>;
  copyText(text: string): void | Promise<void>;
  /** Open a new conversation with the composer prefilled. */
  draftConversation(options: DraftConversationOptions): void | Promise<void>;
  getLocale(): string;
  subscribeLocale(listener: () => void): () => void;
  getHostContext(): HostContextValue;
  subscribeHostContext?(listener: () => void): () => void;
  /**
   * Make the plugin's own locales visible to the host's i18n under
   * ``appPlugin.<plugin id>`` (tab and page titles are translated by the host).
   */
  registerLocales?(
    pluginId: string,
    locales: Record<string, Record<string, unknown>>,
  ): void;
}

let current: HostServices | null = null;

/** The installed services (set by the adapter / the test host). */
export function setHostServices(services: HostServices | null): void {
  current = services;
}

export function getHostServices(): HostServices {
  if (!current) {
    throw new Error(
      "@valuz/plugin-sdk: host services are not installed (is the plugin running inside Valuz or the test host?)",
    );
  }
  return current;
}
