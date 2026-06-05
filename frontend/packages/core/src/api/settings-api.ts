/**
 * Client for /v1/settings/* endpoints.
 *
 * Two groups of knobs today:
 *  - Preferences (timezone / locale / theme / font_size) drive cross-
 *    cutting UI defaults.
 *  - Model defaults (runtime / provider / model / effort) drive the
 *    Settings → Models "Default" card. Kernel V5+bba3014 unified the
 *    legacy ``thinking_depth`` knob with the runtime-level
 *    ``ModelSettings.effort`` lever — same 5-value enum + null.
 */

import type { EffortLevel } from "@valuz/shared";
import { createFetchJson } from "./fetch-json";
import type { RuntimeId } from "./sessions-api";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setSettingsApiBase = (url: string): void => {
  _apiBase = url;
};

export type { EffortLevel };

/**
 * Effort values for the Settings → Models default dropdown. The
 * Composer's selector reads the same set. ``null`` is intentionally
 * absent — a legacy ``default_effort = null`` row is coerced to
 * ``"high"`` at display time so the user always picks a concrete
 * level (see ``FALLBACK_EFFORT`` in backend preferences).
 */
export const DEFAULT_EFFORT_VALUES: readonly EffortLevel[] = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

/** Coerce a possibly-null effort (legacy "Default" sentinel) to the
 *  current display fallback. Use everywhere the UI renders an effort
 *  value so the dropdown is always pointing at a real option. */
export const EFFORT_FALLBACK: EffortLevel = "high";

export const resolveEffortForDisplay = (
  value: EffortLevel | null | undefined,
): EffortLevel => value ?? EFFORT_FALLBACK;

export interface ModelDefaults {
  default_runtime: RuntimeId;
  /** ``null`` after the user clears it or after switching runtime to
   *  one that the previously-default provider can't run on. */
  default_provider_id: string | null;
  default_model: string | null;
  /** Always one of the 5 EffortLevel values. Unset / cleared rows
   *  collapse to ``EFFORT_FALLBACK`` ("high") server-side — there is
   *  no "Default" sentinel anymore. */
  default_effort: EffortLevel;
}

export interface PreferencesResponse {
  default_timezone: string;
  default_locale: string;
  detected_timezone: string;
  theme: string;
  font_size: string;
}

export interface PreferencesPatchPayload {
  default_timezone?: string | null;
  default_locale?: string | null;
  theme?: string | null;
  font_size?: string | null;
}

const fetchJson = createFetchJson(() => _apiBase);

export const settingsApi = {
  getPreferences(): Promise<PreferencesResponse> {
    return fetchJson("/v1/settings/preferences");
  },

  patchPreferences(
    payload: PreferencesPatchPayload,
  ): Promise<PreferencesResponse> {
    return fetchJson("/v1/settings/preferences", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  getModelDefaults(): Promise<ModelDefaults> {
    return fetchJson<ModelDefaults>("/v1/settings/model-defaults");
  },

  patchModelDefaults(payload: {
    default_runtime?: RuntimeId;
    /** Pass ``""`` to clear, omit to leave unchanged. */
    default_provider_id?: string;
    default_model?: string;
    /** One of the 5 EffortLevel values. Omit to leave unchanged. */
    default_effort?: EffortLevel;
  }): Promise<ModelDefaults> {
    return fetchJson<ModelDefaults>("/v1/settings/model-defaults", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },
};
