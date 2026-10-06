import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initI18n } from "@valuz/shared/i18n";
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

// The OSS app's own features are plugins on the shared plugin host. Load them
// first — before the edition overlay, whose plugins position their pages
// relative to the OSS ones — and before React mounts, so the router sees the
// routes from the first render. ``oss-core`` / ``oss-agents`` failing to load
// is a boot failure (an empty shell would look like a working app); an
// optional plugin that fails is left disabled, and one whose backend
// counterpart is switched off is not loaded.
//
// The overlay is optional — a hydration failure must NOT block the mount
// (a bare ``.then`` here meant any rejection left a permanently white
// page, since ``render`` was never called and nothing logged the cause).
// Third-party plugins import react, react-dom and the plugin SDK from this
// table instead of bundling their own copies; it must exist before any plugin
// loads.
installSharedModules();

const rootElement = document.getElementById("root")!;
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
      createRoot(rootElement).render(
        <StrictMode>
          <App />
        </StrictMode>,
      );
      markThirdPartyBootSettled();
    },
    (error: unknown) => renderOssBootFailure(rootElement, error),
  );
