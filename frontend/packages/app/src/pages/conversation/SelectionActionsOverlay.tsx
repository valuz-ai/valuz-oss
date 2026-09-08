import { SlotRenderer, useRegistryStore } from "@valuz/core";
import { useCallback, useEffect, useRef, useState } from "react";

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
 * Slot components receive ``{sessionId, messageId, selectedText,
 * selectedCitationIds, clear, insertDraft}``. Citation IDs come only from
 * markers intersecting this Range, in stable DOM order without duplicates;
 * plain text selections have an empty array. Multiple ranges, spanning two
 * messages or leaving the transcript hides the toolbar instead of guessing.
 */
export const SELECTION_ACTIONS_SLOT = "conversation.selection-actions";

interface ActiveSelection {
  sessionId: string | null;
  messageId: string;
  text: string;
  selectedCitationIds: string[];
  /** Viewport coordinates of the selection's bounding box. */
  top: number;
  centerX: number;
}

/** Lift equivalent text/element edges to their parent boundary. Otherwise
 * intersectsNode considers a Range ending at a marker's nested text offset 0
 * to intersect that marker even though none of its content was selected. */
function outwardBoundary(
  node: Node,
  offset: number,
  host: Element,
): [Node, number] {
  while (node !== host && node.parentNode) {
    const length =
      node instanceof CharacterData ? node.length : node.childNodes.length;
    if (offset !== 0 && offset !== length) break;
    const parent = node.parentNode;
    const index = Array.from(parent.childNodes).findIndex(
      (child) => child === node,
    );
    offset = index + (offset === 0 ? 0 : 1);
    node = parent;
  }
  return [node, offset];
}

function citationIdsIn(range: Range, host: Element): string[] {
  const selected = range.cloneRange();
  selected.setStart(
    ...outwardBoundary(range.startContainer, range.startOffset, host),
  );
  selected.setEnd(
    ...outwardBoundary(range.endContainer, range.endOffset, host),
  );
  const ids = new Set<string>();
  for (const marker of host.querySelectorAll("[data-citation-id]")) {
    const id = marker.getAttribute("data-citation-id");
    if (id && selected.intersectsNode(marker)) ids.add(id);
  }
  return [...ids];
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
  insertDraft,
}: {
  sessionId: string | null;
  containerRef: { readonly current: HTMLElement | null };
  /** Append text to THIS conversation's composer draft (never auto-sends).
   *  Handed to slot components so a selection action can stage an
   *  agent-native request — e.g. "add this claim to my research" — for the
   *  user to complete and send. */
  insertDraft?: (text: string) => void;
}) {
  const hasActions = useRegistryStore(
    (state) => (state.slots[SELECTION_ACTIONS_SLOT]?.length ?? 0) > 0,
  );
  const [active, setActive] = useState<ActiveSelection | null>(null);
  const selectionSession = useRef(sessionId);

  const recompute = useCallback(() => {
    const container = containerRef.current;
    const selection = document.getSelection();
    if (
      !container ||
      !selection ||
      selection.rangeCount !== 1 ||
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
      sessionId,
      messageId,
      text,
      selectedCitationIds: citationIdsIn(range, host),
      top: rect?.top ?? 0,
      centerX: (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
    });
  }, [containerRef, sessionId]);

  useEffect(() => {
    if (selectionSession.current === sessionId) return;
    selectionSession.current = sessionId;
    setActive(null);
    if (!hasActions || !active) return;
    // Do not reinterpret an old transcript Range as a selection in the new
    // session on the next selectionchange/scroll. Leave unrelated UI alone.
    const selection = document.getSelection();
    if (
      selection?.rangeCount &&
      containerRef.current?.contains(selection.getRangeAt(0).commonAncestorContainer)
    ) {
      selection.removeAllRanges();
    }
  }, [active, containerRef, hasActions, sessionId]);

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

  if (!hasActions || !active || active.sessionId !== sessionId) return null;

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
            selectedCitationIds: active.selectedCitationIds,
            clear,
            insertDraft,
          }}
        />
      </div>
    </div>
  );
}
