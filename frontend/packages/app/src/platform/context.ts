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
 */

import { createContext, useContext } from "react";
import type { PlatformCapabilities } from "@valuz/core";

const PlatformCtx = createContext<PlatformCapabilities | null>(null);

export const PlatformProvider = PlatformCtx.Provider;

export function usePlatform(): PlatformCapabilities {
  const ctx = useContext(PlatformCtx);
  if (!ctx) {
    throw new Error(
      "usePlatform() must be used inside <PlatformProvider>. " +
        "Wrap your app with <PlatformProvider value={...}>.",
    );
  }
  return ctx;
}
