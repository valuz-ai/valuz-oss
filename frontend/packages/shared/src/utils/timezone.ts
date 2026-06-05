/**
 * Timezone helpers — pure, dependency-free (lives in @valuz/shared).
 *
 * Architecture (see docs/timezone two-layer model): the user's *live* timezone
 * is whatever the browser reports (`Intl`), never a server/DB value — the
 * renderer runs on the user's machine, which is correct for desktop AND for the
 * headless+WebUI form where the backend may sit in another zone entirely. So
 * every timezone picker DEFAULTS to {@link browserTimezone} and the chosen IANA
 * name is sent explicitly with the schedule. The backend only falls back to OS
 * detection on the no-browser agent/MCP path.
 */

/** Curated fallback list for runtimes without `Intl.supportedValuesOf`. */
export const COMMON_TIMEZONES = [
  "UTC",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "Europe/Moscow",
  "Africa/Cairo",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Bangkok",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
] as const;

let cachedBrowserTZ: string | null = null;

/** The browser/host live IANA timezone (cached). Falls back to `"UTC"`. */
export function browserTimezone(): string {
  if (cachedBrowserTZ !== null) return cachedBrowserTZ;
  try {
    cachedBrowserTZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    cachedBrowserTZ = "UTC";
  }
  return cachedBrowserTZ;
}

/** Test seam — clears the module-level {@link browserTimezone} cache. */
export function resetBrowserTimezoneCache(): void {
  cachedBrowserTZ = null;
}

/** Short GMT offset for a zone, e.g. `"GMT+8"` / `"GMT-7"` / `"UTC"`. */
export function timezoneOffset(tz: string): string {
  if (tz === "UTC") return "UTC";
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? tz;
  } catch {
    return tz;
  }
}

/** Human label like `"Shanghai (GMT+8)"` / `"Los Angeles (GMT-7)"` / `"UTC"`. */
export function timezoneLabel(tz: string): string {
  if (tz === "UTC") return "UTC";
  const city = tz.split("/").pop()?.replace(/_/g, " ") ?? tz;
  return `${city} (${timezoneOffset(tz)})`;
}

type IntlWithSupportedValues = typeof Intl & {
  supportedValuesOf?: (key: "timeZone") => string[];
};

function supportedTimezones(): string[] {
  try {
    const supported = (Intl as IntlWithSupportedValues).supportedValuesOf?.(
      "timeZone",
    );
    return supported && supported.length > 0
      ? supported
      : [...COMMON_TIMEZONES];
  } catch {
    return [...COMMON_TIMEZONES];
  }
}

/**
 * Picker option list: the currently-selected zone + the browser zone first
 * (so they're always present even outside the curated/supported set), then the
 * curated common zones, then the full supported list.
 */
export function timezoneOptions(current: string): string[] {
  return Array.from(
    new Set([
      current,
      browserTimezone(),
      ...COMMON_TIMEZONES,
      ...supportedTimezones(),
    ]),
  ).filter(Boolean);
}
