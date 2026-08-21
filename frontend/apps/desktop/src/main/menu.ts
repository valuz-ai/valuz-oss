import {
  Menu,
  shell,
  type BrowserWindow,
  type MenuItemConstructorOptions,
} from "electron";
import { DESKTOP_PREVIEW_CLOSE_REQUESTED } from "@valuz/shared";
import { t } from "@valuz/shared/i18n";
import {
  getCliInstallStatus,
  installCliToPath,
  uninstallCliFromPath,
} from "./ipc/install-cli";

interface BuildAppMenuOptions {
  getMainWindow: () => BrowserWindow | null;
  checkForUpdates: () => Promise<void>;
}

const separator: MenuItemConstructorOptions = { type: "separator" };

export const buildAppMenu = ({
  getMainWindow,
  checkForUpdates,
}: BuildAppMenuOptions) => {
  const isMac = process.platform === "darwin";
  // Localize role-based items via the app i18n so the submenus follow the
  // in-app language — Electron would otherwise auto-label role items in the
  // OS locale, leaving the expanded menus out of sync with the UI.
  const tl = (key: string) => t(key as Parameters<typeof t>[0]);
  const requestPreviewOrWindowClose = () => {
    getMainWindow()?.webContents.send(DESKTOP_PREVIEW_CLOSE_REQUESTED);
  };
  const closeMenuItem: MenuItemConstructorOptions = {
    label: tl("menu.close"),
    accelerator: "CmdOrCtrl+W",
    click: requestPreviewOrWindowClose,
  };

  const appSubmenu: MenuItemConstructorOptions[] = [
    { role: "about", label: tl("menu.about") },
    separator,
    {
      label: tl("menu.checkForUpdates"),
      click: () => {
        void checkForUpdates();
      },
    },
    separator,
    { role: "services", label: tl("menu.services") },
    separator,
    { role: "hide", label: tl("menu.hide") },
    { role: "hideOthers", label: tl("menu.hideOthers") },
    { role: "unhide", label: tl("menu.unhide") },
    separator,
    { role: "quit", label: tl("menu.quit") },
  ];

  const editSubmenu: MenuItemConstructorOptions[] = isMac
    ? [
        { role: "undo", label: tl("menu.undo") },
        { role: "redo", label: tl("menu.redo") },
        separator,
        { role: "cut", label: tl("menu.cut") },
        { role: "copy", label: tl("menu.copy") },
        { role: "paste", label: tl("menu.paste") },
        { role: "pasteAndMatchStyle", label: tl("menu.pasteAndMatchStyle") },
        { role: "delete", label: tl("menu.delete") },
        { role: "selectAll", label: tl("menu.selectAll") },
      ]
    : [
        { role: "undo", label: tl("menu.undo") },
        { role: "redo", label: tl("menu.redo") },
        separator,
        { role: "cut", label: tl("menu.cut") },
        { role: "copy", label: tl("menu.copy") },
        { role: "paste", label: tl("menu.paste") },
        { role: "delete", label: tl("menu.delete") },
        separator,
        { role: "selectAll", label: tl("menu.selectAll") },
      ];

  const template: MenuItemConstructorOptions[] = [];

  if (isMac) {
    template.push({
      label: "Valuz Agent",
      submenu: appSubmenu,
    });
  }

  template.push(
    {
      label: t("menu.file" as Parameters<typeof t>[0]),
      submenu: [
        {
          label: t("menu.reloadWindow" as Parameters<typeof t>[0]),
          accelerator: "CmdOrCtrl+R",
          click: () => {
            getMainWindow()?.reload();
          },
        },
        separator,
        isMac ? closeMenuItem : { role: "quit", label: tl("menu.quit") },
      ],
    },
    {
      label: t("menu.edit" as Parameters<typeof t>[0]),
      submenu: editSubmenu,
    },
    {
      label: t("menu.view" as Parameters<typeof t>[0]),
      submenu: [
        { role: "reload", label: tl("menu.reload") },
        { role: "forceReload", label: tl("menu.forceReload") },
        { role: "toggleDevTools", label: tl("menu.toggleDevTools") },
        separator,
        { role: "resetZoom", label: tl("menu.resetZoom") },
        { role: "zoomIn", label: tl("menu.zoomIn") },
        { role: "zoomOut", label: tl("menu.zoomOut") },
        separator,
        { role: "togglefullscreen", label: tl("menu.toggleFullScreen") },
      ],
    },
    {
      label: t("menu.window" as Parameters<typeof t>[0]),
      submenu: isMac
        ? [
            { role: "minimize", label: tl("menu.minimize") },
            { role: "zoom", label: tl("menu.zoom") },
            separator,
            { role: "front", label: tl("menu.front") },
          ]
        : [
            { role: "minimize", label: tl("menu.minimize") },
            closeMenuItem,
          ],
    },
    {
      label: t("cli.menuLabel" as Parameters<typeof t>[0]),
      submenu: [
        {
          label: t("cli.installToPath" as Parameters<typeof t>[0]),
          click: () => {
            void handleCliInstall();
          },
        },
        {
          label: t("cli.uninstallFromPath" as Parameters<typeof t>[0]),
          click: () => {
            void handleCliUninstall();
          },
        },
      ],
    },
    {
      role: "help",
      label: tl("menu.help"),
      submenu: [
        {
          label: t("menu.website" as Parameters<typeof t>[0]),
          click: () => {
            void shell.openExternal("https://valuz.io");
          },
        },
      ],
    },
  );

  return Menu.buildFromTemplate(template);
};

async function handleCliInstall() {
  const status = await getCliInstallStatus();
  if (status.installed) return;
  const result = await installCliToPath();
  if (!result.success && result.error !== "cancelled") {
    const { dialog } = await import("electron");
    await dialog.showErrorBox(
      t("cli.installFailed" as Parameters<typeof t>[0]),
      result.error ?? "Unknown error",
    );
  }
}

async function handleCliUninstall() {
  const result = await uninstallCliFromPath();
  if (!result.success && result.error !== "cancelled") {
    const { dialog } = await import("electron");
    await dialog.showErrorBox(
      t("cli.uninstallFailed" as Parameters<typeof t>[0]),
      result.error ?? "Unknown error",
    );
  }
}
