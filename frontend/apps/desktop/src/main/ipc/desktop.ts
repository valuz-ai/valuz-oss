import { app, session } from "electron";
import {
  EgressManager,
  readPersistedEgressMode,
  resolveEgressFrontendsEnabled,
  resolveInitialEgressMode,
  writePersistedEgressMode,
} from "@valuz/desktop-network-egress/main";
import { createServiceManager } from "../services/mod";
import { getMainWindow } from "../windows";
import { createDesktopRuntime } from "./services";

type DesktopRuntime = ReturnType<typeof createDesktopRuntime>;

let _desktopRuntime: DesktopRuntime | null = null;

export const getDesktopRuntime = () => {
  if (!_desktopRuntime) {
    const userDataDir = app.getPath("userData");
    const emergencyOverride =
      process.env.VALUZ_EGRESS_MODE?.trim().toLowerCase() === "off";
    const frontendsEnabled = resolveEgressFrontendsEnabled(
      process.env,
      app.commandLine.hasSwitch("disable-valuz-egress-frontends"),
    );
    // A separately launched development backend cannot be rebuilt when the
    // user crosses the managed/client-managed network boundary. When the
    // canary is enabled, Electron owns the source backend like a sidecar.
    const managedDevMode = !app.isPackaged && frontendsEnabled;
    const egressManager = new EgressManager({
      mode: resolveInitialEgressMode({
        env: process.env,
        persistedMode: readPersistedEgressMode(userDataDir),
      }),
      env: process.env,
      resolveSystemProxy: (targetUrl) =>
        session.defaultSession.resolveProxy(targetUrl),
      frontendsEnabled,
      emergencyOverride,
    });
    _desktopRuntime = createDesktopRuntime(
      createServiceManager(app.getPath("userData"), {
        devMode: !app.isPackaged && !managedDevMode,
        managedDevMode,
        egressManager,
        onEgressModeChanged: (mode) =>
          writePersistedEgressMode(userDataDir, mode),
      }),
      (eventName, payload) => {
        const window = getMainWindow();
        if (
          !window ||
          window.isDestroyed() ||
          window.webContents.isDestroyed()
        ) {
          return;
        }
        window.webContents.send(eventName, payload);
      },
    );
  }
  return _desktopRuntime;
};

/** Convenience alias — safe after app.whenReady(). */
export const desktopRuntime = new Proxy(
  {} as DesktopRuntime,
  {
    get(_target, prop) {
      return Reflect.get(getDesktopRuntime(), prop);
    },
  },
);
