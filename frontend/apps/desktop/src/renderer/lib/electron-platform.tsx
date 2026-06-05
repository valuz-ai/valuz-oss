/**
 * Electron platform adapter — wraps desktop-ipc into PlatformCapabilities.
 *
 * Desktop's App.tsx wraps the router with:
 *   <ElectronPlatformProvider>…</ElectronPlatformProvider>
 *
 * Pages then call ``usePlatform()`` from ``@valuz/app`` instead of
 * importing ``desktop-ipc`` directly.
 */

import type { ReactNode } from "react";
import { PlatformProvider } from "@valuz/app/platform";
import type { PlatformCapabilities } from "@valuz/core";
import {
  selectDirectory,
  copyFiles,
  deleteFile,
  revealInFinder,
  quitApp,
  openNewWindow,
  checkCliLogin,
  launchCliLogin,
  isElectron,
} from "./desktop-ipc";

const electronCapabilities: PlatformCapabilities = {
  selectDirectory,
  copyFiles,
  deleteFile,
  revealInFinder,
  quitApp,
  openNewWindow,
  isElectron: isElectron(),
  checkCliLogin: (tool) => checkCliLogin(tool as "claude" | "codex"),
  launchCliLogin: (tool) => launchCliLogin(tool as "claude" | "codex"),
};

export function ElectronPlatformProvider({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <PlatformProvider value={electronCapabilities}>{children}</PlatformProvider>
  );
}
