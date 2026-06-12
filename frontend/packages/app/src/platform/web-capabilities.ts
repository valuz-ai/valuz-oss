/**
 * No-op PlatformCapabilities — the implicit fallback when ``usePlatform()``
 * is called outside any ``<PlatformProvider>`` (and the explicit value
 * mounted by ``<WebPlatformProvider>`` in webui).
 *
 * All file-system / native operations return inert results so callers that
 * gate on ``isElectron`` keep working; callers that don't gate get a benign
 * no-op instead of a thrown exception that would crash the renderer.
 */

import type { PlatformCapabilities } from "@valuz/core";

export const webCapabilities: PlatformCapabilities = {
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
  isMac: false,
};
