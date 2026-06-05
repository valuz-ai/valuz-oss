import type { I18nKey } from "../types/i18n";
import { safeLocalGet } from "../utils/safe-storage";
import enUS from "../../../../../i18n/locales/en-US.json";
import zhCN from "../../../../../i18n/locales/zh-CN.json";

export type LocaleCode = "en-US" | "zh-CN";

export interface I18nConfig {
  locale: LocaleCode;
  fallbackLocale?: LocaleCode;
}

export interface I18nState {
  locale: LocaleCode;
  fallbackLocale: LocaleCode;
  translations: Record<string, string>;
  fallbackTranslations: Record<string, string>;
  loading: boolean;
}

function flatten(
  obj: Record<string, unknown>,
  prefix = "",
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      result[fullKey] = value;
    } else if (value && typeof value === "object") {
      Object.assign(result, flatten(value as Record<string, unknown>, fullKey));
    }
  }
  return result;
}

const localeMap: Record<string, Record<string, string>> = {
  "en-US": flatten(enUS as Record<string, unknown>),
  "zh-CN": flatten(zhCN as Record<string, unknown>),
};

// Runtime extension storage for plugin-owned i18n resources. Keyed by
// namespace so a plugin can re-register or hot-swap its locales (the
// dispose return value of ``registerParserPluginUI`` doesn't currently
// drop strings — see the README — but a future iteration could).
const pluginLocaleMap: Map<
  string,
  Partial<Record<string, Record<string, string>>>
> = new Map();

function loadLocale(locale: string): Record<string, string> {
  const base = localeMap[locale] ?? localeMap["en-US"];
  // Layer every plugin's contribution for this locale on top of the
  // main locale file. Plugins keep their own namespace prefix in the
  // flat key (e.g. ``parser_mineru.config.api_token.label``), so they
  // can't shadow main-app keys by accident.
  const merged: Record<string, string> = { ...base };
  for (const perNs of pluginLocaleMap.values()) {
    const entry = perNs[locale];
    if (entry) Object.assign(merged, entry);
  }
  return merged;
}

/**
 * Merge plugin-owned locale resources into the runtime state. Plugins
 * call this from their ``register()`` function before the React tree
 * mounts — the contract is that all ``register()`` calls complete
 * before ``createRoot(...).render(...)``, so ``useSyncExternalStore``
 * subscribers never see a torn snapshot during commit.
 *
 * ``data`` is the raw nested locale object (mirror of the main locale
 * JSON shape); we flatten it with the ``namespace`` as the top-level
 * key prefix. Calling this for the currently-active locale triggers
 * a ``notify()`` so live subscribers re-render with the new strings.
 */
export function registerLocaleNamespace(
  namespace: string,
  locale: string,
  data: Record<string, unknown>,
): void {
  const flat = flatten(data, namespace);
  let perNs = pluginLocaleMap.get(namespace);
  if (perNs === undefined) {
    perNs = {};
    pluginLocaleMap.set(namespace, perNs);
  }
  perNs[locale] = { ...(perNs[locale] ?? {}), ...flat };
  // Live re-merge: if the just-registered locale is the active one
  // (or the fallback), fold into state.translations / state.fallback
  // so already-mounted t() callers see new strings on next render.
  if (state.locale === locale) {
    Object.assign(state.translations, flat);
  }
  if (state.fallbackLocale === locale) {
    Object.assign(state.fallbackTranslations, flat);
  }
  notify();
}

// Self-initialize from the persisted locale on module load. Without
// this, a Vite HMR-driven re-evaluation of this module (e.g. when a
// locale JSON or any importer is edited) would reset ``state`` to
// empty translations until the next ``initI18n()`` call from
// ``main.tsx`` — which doesn't re-run on hot reload. Components then
// render the raw dotted keys until a full page refresh.
function readStoredLocale(): LocaleCode {
  // Use the shared safe-storage helper so jsdom-without-Storage-methods and
  // private-mode DOMException both fall back to the zh-CN default rather
  // than throwing on module load (see utils/safe-storage.ts).
  const v = safeLocalGet("valuz-locale");
  return v === "en-US" || v === "zh-CN" ? v : "zh-CN";
}

const _initialLocale: LocaleCode = readStoredLocale();

const state: I18nState = {
  locale: _initialLocale,
  fallbackLocale: "zh-CN",
  translations: loadLocale(_initialLocale),
  fallbackTranslations: _initialLocale === "zh-CN" ? {} : loadLocale("zh-CN"),
  loading: false,
};
// fallbackTranslations falls back to the active translations when the
// fallback locale == active locale, avoiding a duplicate load.
if (_initialLocale === "zh-CN") {
  state.fallbackTranslations = state.translations;
}

let snapshot: I18nState = state;

const listeners = new Set<() => void>();

function notify() {
  snapshot = { ...state };
  for (const fn of listeners) fn();
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getSnapshot(): I18nState {
  return snapshot;
}

export function initI18n(config: I18nConfig): void {
  const locale = config.locale;
  const fallback = config.fallbackLocale ?? "en-US";
  state.translations = loadLocale(locale);
  state.fallbackTranslations =
    locale === fallback ? state.translations : loadLocale(fallback);
  state.locale = locale;
  state.fallbackLocale = fallback;
  notify();
}

export function setLocale(locale: LocaleCode): void {
  if (state.locale === locale && Object.keys(state.translations).length > 0)
    return;
  state.translations = loadLocale(locale);
  state.locale = locale;
  notify();
}

function interpolate(
  template: string,
  params?: Record<string, string | number>,
): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name) =>
    params[name] !== undefined ? String(params[name]) : `{${name}}`,
  );
}

function resolveTranslation(key: I18nKey): string {
  return state.translations[key] ?? state.fallbackTranslations[key] ?? key;
}

export function t(
  key: I18nKey,
  fallback?: string | Record<string, string | number>,
  params?: Record<string, string | number>,
): string {
  if (fallback !== undefined && typeof fallback === "object") {
    return interpolate(resolveTranslation(key), fallback);
  }
  const raw = resolveTranslation(key);
  const resolved = raw !== key ? raw : (fallback ?? key);
  return interpolate(typeof resolved === "string" ? resolved : key, params);
}

export function getLocale(): LocaleCode {
  return state.locale;
}

// Vite HMR: accept self so a locale JSON edit (which re-evaluates this
// module via its static import chain) replaces this module in place
// instead of falling through to a full page reload. Self-initialized
// ``state`` above means the new module instance already has the right
// translations loaded before any subscriber pulls a snapshot.
if (typeof import.meta !== "undefined") {
  const hot = (import.meta as unknown as { hot?: { accept: () => void } }).hot;
  if (hot) hot.accept();
}
