import path from "node:path";
import { t } from "@valuz/shared/i18n";
import {
  Menu,
  Tray,
  app,
  nativeImage,
  dialog,
  type BrowserWindow,
} from "electron";
import { getCliInstallStatus, installCliToPath } from "./ipc/install-cli";

interface CreateTrayOptions {
  getMainWindow: () => BrowserWindow | null;
  checkForUpdates: () => Promise<void>;
}

export const createAppTray = ({
  getMainWindow,
  checkForUpdates,
}: CreateTrayOptions) => {
  // macOS: monochrome template icon (auto-tinted by the OS for light/dark menu bar).
  // Windows/Linux: use the standard ico/png icon.
  const iconName =
    process.platform === "darwin" ? "iconTemplate.png" : "icon.ico";
  const iconPath = path.join(app.getAppPath(), "build", iconName);
  const trayImage = nativeImage.createFromPath(iconPath);
  if (process.platform === "darwin") {
    trayImage.setTemplateImage(true);
  }
  const tray = new Tray(trayImage);

  const showMainWindow = () => {
    const mainWindow = getMainWindow();
    if (!mainWindow) {
      return;
    }

    mainWindow.show();
    mainWindow.focus();
  };

  const hideMainWindow = () => {
    const mainWindow = getMainWindow();
    mainWindow?.hide();
  };

  tray.setToolTip("Valuz");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: t("tray.showWindow" as Parameters<typeof t>[0]),
        click: showMainWindow,
      },
      {
        label: t("tray.hideWindow" as Parameters<typeof t>[0]),
        click: hideMainWindow,
      },
      { type: "separator" },
      {
        label: t("tray.checkUpdate" as Parameters<typeof t>[0]),
        click: () => {
          void checkForUpdates();
        },
      },
      { type: "separator" },
      {
        label: t("cli.installToPath" as Parameters<typeof t>[0]),
        click: () => {
          void handleTrayCliInstall();
        },
      },
      { type: "separator" },
      {
        label: t("tray.quit" as Parameters<typeof t>[0]),
        click: () => {
          app.quit();
        },
      },
    ]),
  );

  tray.on("double-click", showMainWindow);
  tray.on("click", showMainWindow);

  return tray;
};

async function handleTrayCliInstall() {
  const status = await getCliInstallStatus();
  if (status.installed) return;
  const result = await installCliToPath();
  if (!result.success && result.error !== "cancelled") {
    await dialog.showErrorBox(
      t("cli.installFailed" as Parameters<typeof t>[0]),
      result.error ?? "Unknown error",
    );
  }
}
