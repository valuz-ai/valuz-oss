/**
 * Main-process half of the attention channel (question-attention):
 * OS notifications + dock badge for pending questions.
 *
 * - `attention_notify` — shows an Electron ``Notification``; clicking it
 *   focuses the main window and forwards an in-app route to the renderer
 *   over the existing ``deep-link-received`` channel (synthetic
 *   ``host: "navigate"`` payload — the renderer's DeepLinkRoot dispatches
 *   it to the router, same as external deep links).
 * - `attention_set_badge` — dock badge via ``app.setBadgeCount`` (macOS +
 *   Unity launchers; a no-op elsewhere per Electron docs).
 */

import { app, Notification } from "electron";
import { getMainWindow } from "../windows";

interface NotifyPayload {
  title?: unknown;
  body?: unknown;
  route?: unknown;
}

interface BadgePayload {
  count?: unknown;
}

export const attentionHandlers = {
  attention_notify: async (
    _event: unknown,
    payload: NotifyPayload,
  ): Promise<{ shown: boolean }> => {
    if (!Notification.isSupported()) return { shown: false };
    const title = typeof payload?.title === "string" ? payload.title : "";
    const body = typeof payload?.body === "string" ? payload.body : "";
    const route = typeof payload?.route === "string" ? payload.route : "";
    if (!title) return { shown: false };

    const n = new Notification({ title, body, silent: false });
    n.on("click", () => {
      const window = getMainWindow();
      if (!window) return;
      window.show();
      window.focus();
      if (route) {
        window.webContents.send("deep-link-received", {
          raw: `app://navigate${route}`,
          host: "navigate",
          pathname: route,
          search: "",
        });
      }
    });
    n.show();
    return { shown: true };
  },

  attention_set_badge: async (
    _event: unknown,
    payload: BadgePayload,
  ): Promise<{ count: number }> => {
    const count =
      typeof payload?.count === "number" && Number.isFinite(payload.count)
        ? Math.max(0, Math.floor(payload.count))
        : 0;
    app.setBadgeCount(count);
    return { count };
  },
};
