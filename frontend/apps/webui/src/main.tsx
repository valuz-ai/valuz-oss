import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
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
import "./index.css";
import { App } from "./App";

const storedLocale = localStorage.getItem("valuz-locale") as LocaleCode | null;
initI18n({ locale: storedLocale ?? "zh-CN", fallbackLocale: "zh-CN" });
hydrateTheme();

// Register built-in parser plugin i18n / UI bindings BEFORE createRoot —
// see parser-plugins/src/index.ts for the ordering contract.
initParserPlugins();

hydrateOverlayIfPresent().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
