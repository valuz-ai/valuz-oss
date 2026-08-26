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
  readFileContent,
  quitApp,
  relaunchApp,
  openNewWindow,
  checkCliLogin,
  launchCliLogin,
  isElectron,
  windowMinimize,
  windowMaximize,
  windowClose,
  windowIsMaximized,
  windowReload,
  windowToggleDevTools,
  windowToggleFullscreen,
  cliInstallToPath,
  cliUninstallFromPath,
  cliInstallStatus,
} from "./desktop-ipc";

const isMac = navigator.platform.startsWith("Mac");

const electronCapabilities: PlatformCapabilities = {
  selectDirectory,
  copyFiles,
  deleteFile,
  revealInFinder,
  readFileContent,
  quitApp,
  relaunchApp,
  openNewWindow,
  isElectron: isElectron(),
  isMac,
  checkCliLogin: (tool) => checkCliLogin(tool as "claude" | "codex"),
  launchCliLogin: (tool) => launchCliLogin(tool as "claude" | "codex"),
  windowMinimize,
  windowMaximize,
  windowClose,
  windowIsMaximized,
  windowReload,
  windowToggleDevTools,
  windowToggleFullscreen,
  cliInstallToPath,
  cliUninstallFromPath,
  cliInstallStatus,
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
