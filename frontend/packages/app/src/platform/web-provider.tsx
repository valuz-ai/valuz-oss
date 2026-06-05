/**
 * No-op platform provider for webui / browser environments.
 * All file-system operations return errors or nulls.
 */

import type { ReactNode } from "react";
import type { PlatformCapabilities } from "@valuz/core";
import { PlatformProvider } from "./context";

const webCapabilities: PlatformCapabilities = {
  selectDirectory: async () => null,
  copyFiles: async () => ({
    copied: 0,
    errors: ["File operations not available in browser"],
  }),
  deleteFile: async () => ({
    success: false,
    error: "File operations not available in browser",
  }),
  revealInFinder: async () => {
    /* no-op */
  },
  quitApp: async () => {
    /* no-op */
  },
  openNewWindow: async () => {
    /* no-op */
  },
  isElectron: false,
};

export function WebPlatformProvider({ children }: { children: ReactNode }) {
  return (
    <PlatformProvider value={webCapabilities}>{children}</PlatformProvider>
  );
}
