/**
 * Platform capability context — abstracts Electron-only features so pages
 * work identically in desktop and webui.
 *
 * Desktop wraps Electron IPC into a PlatformCapabilities object.
 * Webui provides no-op / fallback implementations.
 *
 * Usage in pages:
 *   const { selectDirectory, isElectron } = usePlatform();
 *   if (!isElectron) return <WebFallback />;
 *
 * Defensive fallback: if ``usePlatform()`` is called outside any provider
 * (an isolated test render, a portal/tooltip that escapes the tree, or a
 * future window whose entry forgot the wrapper) it returns the no-op
 * ``webCapabilities`` instead of throwing. History: PR #81 made
 * ``StartupScreen`` call ``usePlatform()`` for the frameless-window
 * controls, and ``App.tsx`` originally rendered it in a branch outside
 * ``ElectronPlatformProvider`` — the renderer crashed on every boot with
 * the very error this fallback now makes impossible. Commit 784bafc5
 * patched that one path; this guard closes the entire class. A loud
 * dev-mode warn-once preserves the signal so missing providers don't
 * silently disable Electron features in development.
 */

import { createContext, useContext } from "react";
import type { PlatformCapabilities } from "@valuz/core";
import { webCapabilities } from "./web-capabilities";

const PlatformCtx = createContext<PlatformCapabilities | null>(null);

export const PlatformProvider = PlatformCtx.Provider;

const _isDev =
  typeof import.meta !== "undefined" &&
  // ``import.meta.env`` shape isn't typed across the project; treat
  // it as an opaque record at the call site rather than pulling in a
  // Vite-specific type here. Mirrors the pattern in
  // ``packages/core/src/hooks/useTranslation.ts``.
  (import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV === true;

let _warnedAboutMissingProvider = false;

export function usePlatform(): PlatformCapabilities {
  const ctx = useContext(PlatformCtx);
  if (ctx) return ctx;

  if (_isDev && !_warnedAboutMissingProvider) {
    _warnedAboutMissingProvider = true;
    // eslint-disable-next-line no-console
    console.warn(
      "usePlatform() called outside <PlatformProvider> — returning no-op web capabilities. " +
        "Wrap the tree with <ElectronPlatformProvider> (desktop) or <WebPlatformProvider> (webui) " +
        "to enable native capabilities.",
    );
  }
  return webCapabilities;
}
