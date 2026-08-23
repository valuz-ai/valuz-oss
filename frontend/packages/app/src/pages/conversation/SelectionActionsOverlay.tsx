import { SlotRenderer, useRegistryStore } from "@valuz/core";
import { useCallback, useEffect, useState } from "react";

/**
 * Host extension point for assistant-text selections.
 *
 * OSS itself ships no selection behavior: this overlay watches selections
 * inside the transcript, and when one lands inside a single assistant message
 * (the ``data-assistant-message-id`` anchors stamped by ConversationTurnList)
 * it floats whatever an overlay edition registered under the
 * ``conversation.selection-actions`` slot next to the selection. With nothing
 * registered the component subscribes to nothing and renders nothing.
 *
 * Slot components receive ``{sessionId, messageId, selectedText, clear}`` —
 * enough to resolve the message's citations/evidence server-side and to
 * dismiss the toolbar after acting. Spanning two messages (or leaving the
 * transcript) hides the toolbar instead of guessing an anchor.
 */
export const SELECTION_ACTIONS_SLOT = "conversation.selection-actions";

interface ActiveSelection {
  messageId: string;
  text: string;
  /** Viewport coordinates of the selection's bounding box. */
  top: number;
  centerX: number;
}

/** jsdom (and some embedders) do not implement Range.getBoundingClientRect. */
function rectOf(range: Range): DOMRect | null {
  try {
    return range.getBoundingClientRect();
  } catch {
    return null;
  }
}

export function SelectionActionsOverlay({
  sessionId,
  containerRef,
}: {
  sessionId: string | null;
  containerRef: { readonly current: HTMLElement | null };
}) {
  const hasActions = useRegistryStore(
    (state) => (state.slots[SELECTION_ACTIONS_SLOT]?.length ?? 0) > 0,
  );
  const [active, setActive] = useState<ActiveSelection | null>(null);

  const recompute = useCallback(() => {
    const container = containerRef.current;
    const selection = document.getSelection();
    if (
      !container ||
      !selection ||
      selection.rangeCount === 0 ||
      selection.isCollapsed
    ) {
      setActive(null);
      return;
    }
    const range = selection.getRangeAt(0);
    const common = range.commonAncestorContainer;
    const element =
      common instanceof Element ? common : (common.parentElement ?? null);
    // ``closest`` from the COMMON ancestor: a selection spanning two
    // assistant messages resolves to a shared parent with no anchor and
    // deliberately shows nothing.
    const host = element?.closest("[data-assistant-message-id]") ?? null;
    if (!host || !container.contains(host)) {
      setActive(null);
      return;
    }
    const messageId = host.getAttribute("data-assistant-message-id");
    const text = selection.toString().trim();
    if (!messageId || !text) {
      setActive(null);
      return;
    }
    const rect = rectOf(range);
    setActive({
      messageId,
      text,
      top: rect?.top ?? 0,
      centerX: (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
    });
  }, [containerRef]);

  useEffect(() => {
    if (!hasActions) {
      setActive(null);
      return;
    }
    document.addEventListener("selectionchange", recompute);
    // The anchor is in viewport coordinates — follow the transcript scroll.
    const container = containerRef.current;
    container?.addEventListener("scroll", recompute, { passive: true });
    return () => {
      document.removeEventListener("selectionchange", recompute);
      container?.removeEventListener("scroll", recompute);
    };
  }, [containerRef, hasActions, recompute]);

  const clear = useCallback(() => {
    document.getSelection()?.removeAllRanges();
    setActive(null);
  }, []);

  if (!hasActions || !active) return null;

  return (
    <div
      data-slot="conversation-selection-actions"
      role="toolbar"
      // Keep the selection alive while clicking the toolbar: without this the
      // mousedown collapses the selection and unmounts the button mid-click.
      onMouseDown={(event) => event.preventDefault()}
      className="fixed z-50 -translate-x-1/2 -translate-y-full"
      style={{ top: Math.max(active.top - 8, 8), left: active.centerX }}
    >
      <div className="flex items-center gap-1 rounded-lg border border-surface-border bg-card px-1.5 py-1 shadow-md">
        <SlotRenderer
          name={SELECTION_ACTIONS_SLOT}
          context={{
            sessionId,
            messageId: active.messageId,
            selectedText: active.text,
            clear,
          }}
        />
      </div>
    </div>
  );
}
