import { app } from "electron";
import { createServiceManager } from "../services/mod";
import { createDesktopRuntime } from "./services";

let _desktopRuntime: ReturnType<typeof createDesktopRuntime> | null = null;

export const getDesktopRuntime = () => {
  if (!_desktopRuntime) {
    _desktopRuntime = createDesktopRuntime(
      createServiceManager(app.getPath("userData"), {
        devMode: !app.isPackaged,
      }),
    );
  }
  return _desktopRuntime;
};

/** Convenience alias — safe after app.whenReady(). */
export const desktopRuntime = new Proxy(
  {} as ReturnType<typeof createDesktopRuntime>,
  {
    get(_target, prop) {
      return (getDesktopRuntime() as any)[prop];
    },
  },
);
