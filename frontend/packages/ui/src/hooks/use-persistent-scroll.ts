import { useEffect, type RefObject } from "react";

/**
 * Restore one scroll container for the lifetime of the current browser tab.
 *
 * Keys contain opaque document/session ids only.  Positions are deliberately
 * kept in sessionStorage so they do not become cross-user durable state.
 */
export function usePersistentScroll(
  ref: RefObject<HTMLElement | null>,
  storageKey: string | null,
  ready = true,
): void {
  useEffect(() => {
    if (!storageKey || !ready) return;
    const node = ref.current;
    if (!node) return;

    const stored = Number(window.sessionStorage.getItem(storageKey));
    let restoreFrame = 0;
    restoreFrame = window.requestAnimationFrame(() => {
      restoreFrame = window.requestAnimationFrame(() => {
        if (Number.isFinite(stored) && stored >= 0) node.scrollTop = stored;
      });
    });
    const persist = () => {
      window.sessionStorage.setItem(storageKey, String(node.scrollTop));
    };
    node.addEventListener("scroll", persist, { passive: true });
    return () => {
      window.cancelAnimationFrame(restoreFrame);
      node.removeEventListener("scroll", persist);
      persist();
    };
  }, [ready, ref, storageKey]);
}
