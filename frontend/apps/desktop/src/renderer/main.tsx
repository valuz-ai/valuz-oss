import React from "react";
import ReactDOM from "react-dom/client";
import { initI18n } from "@valuz/shared/i18n";
import type { LocaleCode } from "@valuz/shared/i18n";
import { initParserPlugins } from "@valuz/parser-plugins";
import { hydrateOverlayIfPresent, hydrateTheme } from "@valuz/core";
// Serif display faces — used only for onboarding hero headlines (editorial
// moment). Bundled via @fontsource so the desktop build stays offline-safe;
// CJK subsets are unicode-range split, so the browser only fetches the glyph
// slices actually rendered. Latin (Newsreader) + CJK (Noto Serif SC) pair.
import "@fontsource/newsreader/400.css";
import "@fontsource/newsreader/500.css";
import "@fontsource/newsreader/400-italic.css";
import "@fontsource/noto-serif-sc/500.css";
import "@fontsource/noto-serif-sc/600.css";
import { App } from "./App";

// Initialize i18n synchronously before first render so components
// never see empty translations. The localStorage key matches the
// one used by settings-store (valuz-locale, defaults to zh-CN).
const storedLocale = localStorage.getItem("valuz-locale") as LocaleCode | null;
initI18n({ locale: storedLocale ?? "zh-CN", fallbackLocale: "zh-CN" });
hydrateTheme();

// Built-in parser plugin UIs register their i18n resources here, AFTER
// initI18n so ``state.locale`` is already set — registerLocaleNamespace
// merges into the active locale's state.translations during this call.
// Both must happen BEFORE the React tree mounts so useSyncExternalStore
// subscribers (useTranslation) never see a torn snapshot at commit.
initParserPlugins();

// Hydrate edition overlay before React mounts so the router sees
// the correct routes from the first render.
hydrateOverlayIfPresent().then(() => {
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
