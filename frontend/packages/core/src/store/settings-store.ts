import { create } from "zustand";

import { safeLocalGet, safeLocalSet } from "@valuz/shared";
import { settingsApi } from "../api/settings-api";

type Theme = "light" | "dark" | "auto";
type FontSize = "compact" | "default" | "comfortable";

const STORAGE_THEME = "valuz-theme";
const STORAGE_FONT_SIZE = "valuz-font-size";
const STORAGE_LOCALE = "valuz-locale";

interface SettingsState {
  theme: Theme;
  fontSize: FontSize;
  locale: string;
  defaultTimezone: string;
  loaded: boolean;

  fetchFromBackend: () => Promise<void>;
  setTheme: (theme: Theme) => void;
  setFontSize: (size: FontSize) => void;
  setLocale: (locale: string) => void;
  setDefaultTimezone: (tz: string) => void;
}

function readLS(key: string, fallback: string): string {
  return safeLocalGet(key) ?? fallback;
}

function writeLS(key: string, value: string): void {
  safeLocalSet(key, value);
}

export const useSettingsStore = create<SettingsState>((set) => ({
  theme: readLS(STORAGE_THEME, "light") as Theme,
  fontSize: readLS(STORAGE_FONT_SIZE, "default") as FontSize,
  locale: readLS(STORAGE_LOCALE, "zh-CN"),
  defaultTimezone: "UTC",
  loaded: false,

  async fetchFromBackend() {
    try {
      const prefs = await settingsApi.getPreferences();
      const theme = (prefs.theme || "light") as Theme;
      const fontSize = (prefs.font_size || "default") as FontSize;
      const locale = prefs.default_locale || "zh-CN";
      const defaultTimezone = prefs.default_timezone || "UTC";

      writeLS(STORAGE_THEME, theme);
      writeLS(STORAGE_FONT_SIZE, fontSize);
      writeLS(STORAGE_LOCALE, locale);

      set({ theme, fontSize, locale, defaultTimezone, loaded: true });
    } catch {
      // Backend unavailable — keep localStorage defaults, mark loaded so
      // hydrateTheme() knows we tried.
      set({ loaded: true });
    }
  },

  setTheme(theme: Theme) {
    writeLS(STORAGE_THEME, theme);
    set({ theme });
    settingsApi.patchPreferences({ theme }).catch(() => {});
  },

  setFontSize(fontSize: FontSize) {
    writeLS(STORAGE_FONT_SIZE, fontSize);
    set({ fontSize });
    settingsApi.patchPreferences({ font_size: fontSize }).catch(() => {});
  },

  setLocale(locale: string) {
    writeLS(STORAGE_LOCALE, locale);
    set({ locale });
    settingsApi.patchPreferences({ default_locale: locale }).catch(() => {});
  },

  setDefaultTimezone(defaultTimezone: string) {
    set({ defaultTimezone });
    settingsApi
      .patchPreferences({ default_timezone: defaultTimezone })
      .catch(() => {});
  },
}));
