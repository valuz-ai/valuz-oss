/**
 * Client for the Runtime Agent registry endpoint introduced in REP-107.
 *
 * The Runtime picker on the session-creation form fetches this list to
 * render its options + grey out runtimes whose binary isn't installed
 * (only Codex needs one today). Display labels and supported protocols
 * come from the backend so renaming a runtime ("DeepAgents" → "Valuz
 * Agent") doesn't require a frontend release.
 */

import { createFetchJson } from "./fetch-json";
import type { ApiProtocol } from "./providers-api";
import type { RuntimeId } from "./sessions-api";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setRuntimesApiBase = (url: string): void => {
  _apiBase = url;
};

export interface RuntimeListItem {
  id: RuntimeId;
  /** User-facing label for the picker (e.g. "Claude Agent"). */
  display_name: string;
  /** Protocols this runtime can dispatch — used to filter eligible
   *  channels for the model dropdown. Kernel V5+bba3014 4-value enum
   *  (anthropic | openai-completion | openai-response | gemini). */
  supported_protocols: ApiProtocol[];
  /** Name of an external CLI the runtime spawns; ``null`` for
   *  pure-Python runtimes. */
  requires_binary: string | null;
  /** Live host probe — ``false`` greys out the picker option. */
  available: boolean;
  /** Short, user-actionable explanation when ``available`` is
   *  ``false`` (e.g. "'codex' binary not found on PATH"). ``null``
   *  when available. */
  unavailable_reason: string | null;
}

export interface ListRuntimesResponse {
  runtimes: RuntimeListItem[];
}

const fetchJson = createFetchJson(() => _apiBase);

export const runtimesApi = {
  list(): Promise<ListRuntimesResponse> {
    return fetchJson("/v1/runtimes");
  },
};
