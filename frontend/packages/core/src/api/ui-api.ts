import { resolveApiBase } from "./base-resolver";
import { createFetchJson } from "./fetch-json";

/**
 * Client for ``/v1/ui`` — the UI bus (hooks-and-plugin-ui.md §6): backend
 * plugins draw element trees into UI slots; this module fetches them and
 * delivers clicks back. Types mirror ``api/openapi.yaml`` (``UiItem``,
 * ``UiRenderRequest``) and ``backend/valuz_agent/modules/plugin_ui``.
 */

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setUiApiBase = (url: string): void => {
  _apiBase = url;
};

const fetchJson = createFetchJson(() => _apiBase);

export type UiSiteKind = "list" | "single" | "keyed";

/** A UI slot some plugin draws into. */
export interface UiSite {
  site: string;
  kind: UiSiteKind;
  /** Keyed sites only (context-panel tabs): the tab id and its label. */
  key?: string;
  label?: string;
}

export interface UiElement {
  type: string;
  props?: Record<string, unknown>;
  children?: UiNode[];
}

/** A string (text) or an element. */
export type UiNode = string | UiElement;

export interface UiItem {
  /** The plugin id that drew it. */
  owner: string;
  tree: UiNode;
  /** Echoed back with actions; a stale one is refused. */
  generation: number;
  key?: string;
}

export interface UiRenderRequest {
  site: string;
  /** Which occurrence of the site (e.g. ``<session>:<toolUseId>``). */
  instance?: string;
  session_id?: string | null;
  context?: Record<string, unknown>;
}

export interface UiActionRequest extends UiRenderRequest {
  owner: string;
  action: string;
  value?: unknown;
  generation: number;
}

export interface UiActionResult {
  ok: boolean;
  /** The plugin redrew since the element was drawn; ``items`` is fresh. */
  stale?: boolean;
  items: UiItem[];
}

function baseFor(sessionId: string | null | undefined): string | undefined {
  return sessionId ? resolveApiBase({ sessionId }, _apiBase) : undefined;
}

export const uiApi = {
  async sites(): Promise<UiSite[]> {
    const result = await fetchJson<{ sites: UiSite[] }>("/v1/ui/sites");
    return result.sites ?? [];
  },

  async render(request: UiRenderRequest): Promise<UiItem[]> {
    const result = await fetchJson<{ site: string; items: UiItem[] }>(
      "/v1/ui/render",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        baseUrl: baseFor(request.session_id),
      },
    );
    return result.items ?? [];
  },

  async action(request: UiActionRequest): Promise<UiActionResult> {
    return fetchJson<UiActionResult>("/v1/ui/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      baseUrl: baseFor(request.session_id),
    });
  },
};
