import { useMemo, useSyncExternalStore } from "react";

import type { I18nKey } from "@valuz/shared";
import {
  getSnapshot,
  setLocale,
  subscribe,
  type LocaleCode,
} from "@valuz/shared/i18n";

// Dev-mode aid: warn once per missing key so a typo'd plugin key
// (e.g. ``parser_minreu.config.api_token.label``) doesn't silently
// render as the raw dotted string. We only warn when both the active
// locale and the fallback miss — that's when ``resolveTranslation``
// returns the key itself.
const _warnedMissingKeys = new Set<string>();
const _isDev =
  typeof import.meta !== "undefined" &&
  // ``import.meta.env`` shape isn't typed across the project; treat
  // it as an opaque record at the call site rather than pulling in a
  // Vite-specific type here.
  (import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV === true;

export function useTranslation() {
  const state = useSyncExternalStore(subscribe, getSnapshot);

  // The whole return value is memoized on ``state`` so consumers that
  // put ``t`` in a ``useCallback`` / ``useEffect`` dependency list
  // don't see a fresh reference on every render. Without this, every
  // ``useCallback(fn, [t])`` re-creates on every render, every
  // ``useEffect(() => refresh(), [refresh])`` then re-fires, and any
  // page-mounted side-effect fetcher (e.g. ParserSettingsSection's
  // ``parserApi.listPlugins()`` + ``getRouting()``) ends up polling
  // the backend instead of running once. The state only changes on
  // locale switch / loading flip, so memoizing here keeps the
  // identity stable in the common case and still invalidates correctly
  // when the locale actually updates.
  return useMemo(() => {
    function resolveTranslation(key: I18nKey): string {
      const hit = state.translations[key] ?? state.fallbackTranslations[key];
      if (hit !== undefined) return hit;
      if (_isDev && !_warnedMissingKeys.has(key)) {
        _warnedMissingKeys.add(key);
        console.warn(
          `[i18n] missing translation for key "${key}" — falling back to the key string`,
        );
      }
      return key;
    }

    return {
      t: (
        key: I18nKey,
        fallback?: string | Record<string, string | number>,
        params?: Record<string, string | number>,
      ): string => {
        if (fallback !== undefined && typeof fallback === "object") {
          const raw = resolveTranslation(key);
          return raw.replace(/\{(\w+)\}/g, (_, name) =>
            fallback[name] !== undefined ? String(fallback[name]) : `{${name}}`,
          );
        }
        const raw = resolveTranslation(key);
        const resolved = raw !== key ? raw : (fallback ?? key);
        if (!params || typeof resolved !== "string") return resolved;
        return resolved.replace(/\{(\w+)\}/g, (_, name) =>
          params[name] !== undefined ? String(params[name]) : `{${name}}`,
        );
      },

      locale: state.locale as LocaleCode,

      fallbackLocale: state.fallbackLocale as LocaleCode,

      setLocale,

      loading: state.loading,
    };
  }, [state]);
}
