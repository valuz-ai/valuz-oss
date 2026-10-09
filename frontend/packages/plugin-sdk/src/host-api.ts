import { getHostServices } from "./services";
import type { ConfirmOptions, DraftConversationOptions, ToastOptions } from "./types";

/**
 * Host interaction (doc 12 §4): navigation, toast, confirm, links, clipboard.
 * Functions of the app the plugin runs in; they work from event handlers and
 * effects as well as from components.
 */
export const host = {
  /** Go to a page inside Valuz, including the plugin's own pages. */
  navigate(path: string): void {
    getHostServices().navigate(path);
  },
  openSession(sessionId: string): void {
    getHostServices().navigate(`/conversation/${encodeURIComponent(sessionId)}`);
  },
  openProject(projectId: string): void {
    getHostServices().navigate(`/projects/${encodeURIComponent(projectId)}`);
  },
  toast(options: ToastOptions): void {
    getHostServices().toast(options);
  },
  /** A host-styled confirmation dialog; resolves ``true`` on confirm. */
  confirm(options: ConfirmOptions): Promise<boolean> {
    return getHostServices().confirm(options);
  },
  /** Open an external link in the system browser. */
  async openExternal(url: string): Promise<void> {
    if (!/^https?:\/\//i.test(url)) {
      throw new Error("host.openExternal only opens http(s) URLs");
    }
    await getHostServices().openExternal(url);
  },
  async copyText(text: string): Promise<void> {
    await getHostServices().copyText(text);
  },
  /** Open a new conversation with the composer prefilled (not sent). */
  async draftConversation(options: DraftConversationOptions): Promise<void> {
    await getHostServices().draftConversation(options);
  },
};
