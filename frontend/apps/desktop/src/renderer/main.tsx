import React from "react";
import ReactDOM from "react-dom/client";
import { initI18n, getLocale, subscribe } from "@valuz/shared/i18n";
import type { LocaleCode } from "@valuz/shared/i18n";
import { initParserPlugins } from "@valuz/parser-plugins";
import { hydrateOverlayIfPresent, hydrateTheme } from "@valuz/core";
import {
  loadOssPlugins,
  markThirdPartyBootSettled,
  renderOssBootFailure,
  settleOssPlugins,
} from "@valuz/app/plugins";
import { installSharedModules } from "@valuz/plugin-sdk/host";
import { setMenuLocale } from "./lib/desktop-ipc";
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

// Initialize i18n synchronously before first render so components never see
// empty translations. The localStorage key matches the one used by
// settings-store (valuz-locale). On first run (no stored choice) follow the
// OS language — zh* → Chinese, otherwise English — instead of forcing
// Chinese; an explicit in-app choice always wins.
const systemDefaultLocale = (): LocaleCode =>
  (navigator.language || "").toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
const storedLocale = localStorage.getItem("valuz-locale") as LocaleCode | null;
initI18n({
  locale: storedLocale ?? systemDefaultLocale(),
  fallbackLocale: "zh-CN",
});
hydrateTheme();

// Keep the native menu bar (built in the main process, which can't read this
// renderer's localStorage) in sync with the in-app language — on startup and
// on every language switch.
void setMenuLocale(getLocale());
subscribe(() => void setMenuLocale(getLocale()));

// Built-in parser plugin UIs register their i18n resources here, AFTER
// initI18n so ``state.locale`` is already set — registerLocaleNamespace
// merges into the active locale's state.translations during this call.
// Both must happen BEFORE the React tree mounts so useSyncExternalStore
// subscribers (useTranslation) never see a torn snapshot at commit.
initParserPlugins();

// The OSS app's own features are plugins on the shared plugin host. Load them
// first — before the edition overlay, whose plugins position their pages
// relative to the OSS ones — and before React mounts, so the router sees the
// routes from the first render. ``oss-core`` / ``oss-agents`` failing to load
// is a boot failure (an empty shell would look like a working app); an
// optional plugin that fails is left disabled and the rest still load.
//
// The backend starts alongside this window, so the read of which of its
// plugins are off usually finds no backend yet and everything loads;
// ``settleOssPlugins`` applies the real state as soon as the backend answers.
//
// Hydrate edition overlay before React mounts so the router sees
// the correct routes from the first render. The overlay is optional —
// a hydration failure must NOT block the mount (a bare ``.then`` here
// meant any rejection left a permanently white window, since ``render``
// was never called and nothing logged the cause).
// Third-party plugins import react, react-dom and the plugin SDK from this
// table instead of bundling their own copies; it must exist before any plugin
// loads.
installSharedModules();

const rootElement = document.getElementById("root") as HTMLElement;
loadOssPlugins()
  .then(({ stateKnown }) => {
    if (!stateKnown) void settleOssPlugins();
  })
  .then(() =>
    hydrateOverlayIfPresent().catch((cause: unknown) => {
      console.error(
        "[boot] edition overlay hydration failed — continuing with the base profile",
        cause,
      );
    }),
  )
  .then(
    () => {
      ReactDOM.createRoot(rootElement).render(
        <React.StrictMode>
          <App />
        </React.StrictMode>,
      );
      // First-party plugins and the overlay are in and the app has rendered:
      // third-party plugins may start loading now.
      markThirdPartyBootSettled();
    },
    (error: unknown) => renderOssBootFailure(rootElement, error),
  );
