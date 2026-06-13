import {
  Menu,
  shell,
  type BrowserWindow,
  type MenuItemConstructorOptions,
} from "electron";
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

  const appSubmenu: MenuItemConstructorOptions[] = [
    { role: "about" },
    separator,
    {
      label: t("menu.checkForUpdates" as Parameters<typeof t>[0]),
      click: () => {
        void checkForUpdates();
      },
    },
    separator,
    { role: "services" },
    separator,
    { role: "hide" },
    { role: "hideOthers" },
    { role: "unhide" },
    separator,
    { role: "quit" },
  ];

  const editSubmenu: MenuItemConstructorOptions[] = isMac
    ? [
        { role: "undo" },
        { role: "redo" },
        separator,
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "pasteAndMatchStyle" },
        { role: "delete" },
        { role: "selectAll" },
      ]
    : [
        { role: "undo" },
        { role: "redo" },
        separator,
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "delete" },
        separator,
        { role: "selectAll" },
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
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    {
      label: t("menu.edit" as Parameters<typeof t>[0]),
      submenu: editSubmenu,
    },
    {
      label: t("menu.view" as Parameters<typeof t>[0]),
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        separator,
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        separator,
        { role: "togglefullscreen" },
      ],
    },
    {
      label: t("menu.window" as Parameters<typeof t>[0]),
      submenu: isMac
        ? [{ role: "minimize" }, { role: "zoom" }, separator, { role: "front" }]
        : [{ role: "minimize" }, { role: "close" }],
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
