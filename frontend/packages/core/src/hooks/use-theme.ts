import { useCallback, useSyncExternalStore } from "react";

import { safeLocalGet, safeLocalSet } from "@valuz/shared";

export type Theme = "light" | "dark" | "auto";
export type FontSize = "compact" | "default" | "comfortable";

const STORAGE_THEME = "valuz-theme";
const STORAGE_FONT_SIZE = "valuz-font-size";

let listeners: Array<() => void> = [];
let mqlSetup = false;

function emitChange() {
  for (const fn of listeners) fn();
}

function prefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function")
    return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function ensureMqlListener() {
  if (
    mqlSetup ||
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  )
    return;
  mqlSetup = true;
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      const theme = (safeLocalGet(STORAGE_THEME) as Theme) ?? "light";
      if (theme === "auto") {
        applyTheme("auto");
        emitChange();
      }
    });
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "dark" || (theme === "auto" && prefersDark())) {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

function applyFontSize(size: FontSize) {
  document.documentElement.setAttribute("data-size", size);
}

export function useTheme() {
  ensureMqlListener();

  const theme = useSyncExternalStore(
    (cb) => {
      listeners.push(cb);
      return () => {
        listeners = listeners.filter((fn) => fn !== cb);
      };
    },
    () => (safeLocalGet(STORAGE_THEME) as Theme) ?? "light",
  );

  const fontSize = useSyncExternalStore(
    (cb) => {
      listeners.push(cb);
      return () => {
        listeners = listeners.filter((fn) => fn !== cb);
      };
    },
    () => (safeLocalGet(STORAGE_FONT_SIZE) as FontSize) ?? "default",
  );

  const setTheme = useCallback((t: Theme) => {
    safeLocalSet(STORAGE_THEME, t);
    applyTheme(t);
    emitChange();
    import("../store/settings-store").then(({ useSettingsStore }) => {
      useSettingsStore.getState().setTheme(t);
    });
  }, []);

  const setFontSize = useCallback((s: FontSize) => {
    safeLocalSet(STORAGE_FONT_SIZE, s);
    applyFontSize(s);
    emitChange();
    import("../store/settings-store").then(({ useSettingsStore }) => {
      useSettingsStore.getState().setFontSize(s);
    });
  }, []);

  return { theme, fontSize, setTheme, setFontSize };
}

export function hydrateTheme() {
  ensureMqlListener();
  // If the settings store has loaded from backend, use its values (source of truth).
  // Otherwise fall back to localStorage (offline / first load before fetch completes).
  try {
    const { useSettingsStore } = require("../store/settings-store");
    const store = useSettingsStore.getState();
    if (store.loaded) {
      applyTheme(store.theme);
      applyFontSize(store.fontSize);
      return;
    }
  } catch {
    /* store not available */
  }
  const theme = (safeLocalGet(STORAGE_THEME) as Theme) ?? "light";
  const fontSize = (safeLocalGet(STORAGE_FONT_SIZE) as FontSize) ?? "default";
  applyTheme(theme);
  applyFontSize(fontSize);
}
