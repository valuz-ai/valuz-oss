import { contextBridge, ipcRenderer } from "electron";
import { buildDesktopApi } from "./desktop-api";

const listenerMap = new Map<
  (payload: unknown) => void,
  (_event: unknown, payload: unknown) => void
>();

const desktopApi = buildDesktopApi({
  runtime: {
    shell: "electron",
    platform: process.platform,
    version: process.versions.electron,
  },
  invoke: (channel, payload) => ipcRenderer.invoke(channel, payload),
  on: (event, handler) => {
    // Guard against duplicate registration (e.g. React StrictMode double-invoke):
    // remove any existing wrapped listener for this handler before adding a new one.
    const existing = listenerMap.get(handler);
    if (existing) {
      ipcRenderer.removeListener(event, existing);
    }

    const wrapped = (_event: unknown, payload: unknown) => {
      handler(payload);
    };

    listenerMap.set(handler, wrapped);
    ipcRenderer.on(event, wrapped);
  },
  off: (event, handler) => {
    const wrapped = listenerMap.get(handler);
    if (!wrapped) {
      return;
    }

    ipcRenderer.removeListener(event, wrapped);
    listenerMap.delete(handler);
  },
});

contextBridge.exposeInMainWorld("valuzDesktop", desktopApi);
