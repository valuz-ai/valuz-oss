import { useEffect, useRef, type MutableRefObject } from "react";

import { DESKTOP_PREVIEW_CLOSE_REQUESTED } from "@valuz/shared";

interface DesktopBridge {
  runtime?: { platform?: string };
  invoke?: <T>(channel: string, payload?: unknown) => Promise<T>;
  on?: (event: string, handler: (payload: unknown) => void) => void;
  off?: (event: string, handler: (payload: unknown) => void) => void;
}

interface CloseShortcutRegistration {
  token: symbol;
  handler: MutableRefObject<() => void>;
}

const registrations: CloseShortcutRegistration[] = [];
let listening = false;
let desktopConsumerCount = 0;
let desktopListenerBridge: DesktopBridge | null = null;

function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return (window as Window & { valuzDesktop?: DesktopBridge }).valuzDesktop ?? null;
}

function runtimePlatform(): string {
  if (typeof window === "undefined") return "";
  return (
    desktopBridge()?.runtime?.platform ||
    window.navigator.platform ||
    window.navigator.userAgent
  );
}

function isApplePlatform(platform: string): boolean {
  return /^(darwin|mac|iphone|ipad|ipod)/i.test(platform);
}

export function isPreviewCloseShortcut(
  event: Pick<
    KeyboardEvent,
    "altKey" | "ctrlKey" | "isComposing" | "key" | "metaKey" | "shiftKey"
  >,
  platform = runtimePlatform(),
): boolean {
  if (
    event.isComposing ||
    event.altKey ||
    event.shiftKey ||
    event.key.toLowerCase() !== "w"
  ) {
    return false;
  }
  return isApplePlatform(platform)
    ? event.metaKey && !event.ctrlKey
    : event.ctrlKey && !event.metaKey;
}

function handleKeyDown(event: KeyboardEvent): void {
  if (!isPreviewCloseShortcut(event)) return;
  const registration = registrations.at(-1);
  if (!registration) return;

  // Web shells use a capture listener while a preview is active. With no
  // preview registered the listener is detached, so the browser retains its
  // normal tab-close behavior.
  event.preventDefault();
  event.stopImmediatePropagation();
  registration.handler.current();
}

function syncListener(): void {
  // Electron reserves Cmd/Ctrl+W for its native menu accelerator. Its main
  // process forwards that request over the desktop bridge instead, avoiding a
  // browser and menu listener both closing a tab for the same keystroke.
  const shouldListen = registrations.length > 0 && !desktopBridge();
  if (shouldListen === listening || typeof window === "undefined") return;
  listening = shouldListen;
  if (shouldListen) window.addEventListener("keydown", handleKeyDown, true);
  else window.removeEventListener("keydown", handleKeyDown, true);
}

function handleDesktopCloseRequest(): void {
  const registration = registrations.at(-1);
  if (registration) {
    registration.handler.current();
    return;
  }
  // With nothing to close, only macOS keeps the platform meaning of Cmd+W —
  // closing the window leaves the app running in the Dock. On Windows/Linux the
  // main window *is* the app: closing it quits (`window-all-closed`), which is
  // Alt+F4 territory, not what Ctrl+W means there. Stay a no-op instead.
  if (!isApplePlatform(runtimePlatform())) return;
  const closeWindow = desktopBridge()?.invoke?.("window_close");
  void closeWindow?.catch(() => undefined);
}

function syncDesktopListener(): void {
  const bridge = desktopBridge();
  const shouldListen = desktopConsumerCount > 0 && Boolean(bridge?.on);
  if (shouldListen && !desktopListenerBridge && bridge?.on) {
    bridge.on(DESKTOP_PREVIEW_CLOSE_REQUESTED, handleDesktopCloseRequest);
    desktopListenerBridge = bridge;
  } else if (!shouldListen && desktopListenerBridge) {
    desktopListenerBridge.off?.(
      DESKTOP_PREVIEW_CLOSE_REQUESTED,
      handleDesktopCloseRequest,
    );
    desktopListenerBridge = null;
  }
}

export interface UsePreviewCloseShortcutOptions {
  active: boolean;
  onClose: () => void;
}

/**
 * Registers the platform-standard close shortcut for the active preview.
 * Registrations form a stack so a document dialog opened above an artifact
 * pane closes first. Escape is intentionally not a preview-close shortcut.
 * With no preview open, the desktop shortcut falls through to closing the
 * window on macOS only; Windows/Linux keep the app alive and do nothing.
 */
export function usePreviewCloseShortcut({
  active,
  onClose,
}: UsePreviewCloseShortcutOptions): void {
  const handler = useRef(onClose);
  const token = useRef(Symbol("preview-close-shortcut"));

  useEffect(() => {
    handler.current = onClose;
  }, [onClose]);

  useEffect(() => {
    desktopConsumerCount += 1;
    syncDesktopListener();
    return () => {
      desktopConsumerCount -= 1;
      syncDesktopListener();
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    const registration = { token: token.current, handler };
    registrations.push(registration);
    syncListener();
    return () => {
      const index = registrations.findIndex(
        (entry) => entry.token === registration.token,
      );
      if (index !== -1) registrations.splice(index, 1);
      syncListener();
    };
  }, [active]);
}
