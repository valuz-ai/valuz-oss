/**
 * Main-process half of the attention channel (question-attention):
 * OS notifications + dock badge for pending questions.
 *
 * - `attention_notify` — shows an Electron ``Notification``; clicking it
 *   focuses the main window and forwards the in-app route to the renderer
 *   over the dedicated ``attention-navigate`` channel. Deliberately NOT
 *   the ``deep-link-received`` channel: that one is fed by external
 *   ``valuz-oss://`` URLs, and reusing it would let any web page drive
 *   arbitrary in-app navigation by claiming the same payload shape.
 * - `attention_set_badge` — dock badge via ``app.setBadgeCount`` (macOS +
 *   Unity launchers; a no-op elsewhere per Electron docs).
 */

import { app, Notification } from "electron";
import { getMainWindow } from "../windows";

// Electron notifications with no live JS reference can be GC'd before the
// user clicks them, silently dropping the click handler (focus + navigate —
// the feature's core interaction). Retain until closed/clicked/failed.
const liveNotifications = new Set<Notification>();

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
    liveNotifications.add(n);
    const release = () => liveNotifications.delete(n);
    n.on("click", () => {
      release();
      const window = getMainWindow();
      if (!window) return;
      window.show();
      window.focus();
      if (route) {
        window.webContents.send("attention-navigate", { route });
      }
    });
    n.on("close", release);
    n.on("failed", release);
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
