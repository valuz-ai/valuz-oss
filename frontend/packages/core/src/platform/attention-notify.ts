/**
 * System-level attention channel (question-attention): OS notification +
 * dock/tray badge for questions that arrive while the window is in the
 * background.
 *
 * Desktop: routed over the generic ``valuzDesktop.invoke`` bridge to the
 * main process (`attention_notify` / `attention_set_badge`), which owns
 * Electron ``Notification`` + ``app.setBadgeCount`` and turns a click into
 * focus + in-app navigation.
 *
 * WebUI fallback: the Web Notification API (no badge). Permission is
 * requested lazily on first use; denial degrades to nothing — the in-app
 * badge and Activity group still carry the pending.
 *
 * Fire-and-forget by design: a lost notification is recoverable through
 * every other surface, so failures are swallowed (matching the decision
 * stream's silent-error posture).
 */

interface DesktopBridge {
  invoke: <T>(channel: string, payload?: Record<string, unknown>) => Promise<T>;
}

function desktopBridge(): DesktopBridge | null {
  const w = globalThis as { valuzDesktop?: DesktopBridge };
  return typeof w.valuzDesktop?.invoke === "function" ? w.valuzDesktop : null;
}

export interface AttentionNotification {
  title: string;
  body: string;
  /** In-app route to open when the notification is clicked. */
  route: string;
}

export function sendAttentionNotification(n: AttentionNotification): void {
  const bridge = desktopBridge();
  if (bridge) {
    void bridge.invoke("attention_notify", { ...n }).catch(() => {});
    return;
  }
  // WebUI fallback — click focuses the tab; SPA routing is handled by the
  // caller reacting to focus (the pending is still in the store).
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "granted") {
    try {
      new Notification(n.title, { body: n.body });
    } catch {
      // e.g. insecure context — degrade silently.
    }
  } else if (Notification.permission === "default") {
    void Notification.requestPermission().then((p) => {
      if (p === "granted") sendAttentionNotification(n);
    });
  }
}

export function setAttentionBadge(count: number): void {
  const bridge = desktopBridge();
  if (!bridge) return; // no dock/tray outside the desktop shell
  void bridge.invoke("attention_set_badge", { count }).catch(() => {});
}
