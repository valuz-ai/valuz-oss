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
 * selectedCitationIds, selectedCitationRefs, clear, insertDraft}``. References
 * retain each marker's originating message, which may differ from the selected
 * passage's message in a merged turn. Both arrays have stable DOM order and
 * are deduplicated by ID and by (messageId, citationId), respectively. Plain
 * text has empty arrays. Multiple ranges, selections spanning two messages,
 * or selected markers with no explicit origin hide the toolbar, never guess.
 */
export const SELECTION_ACTIONS_SLOT = "conversation.selection-actions";

interface SelectedCitationRef {
  messageId: string;
  citationId: string;
}

interface ActiveSelection {
  sessionId: string | null;
  messageId: string;
  text: string;
  selectedCitationIds: string[];
  selectedCitationRefs: SelectedCitationRef[];
  /** Viewport coordinates of the selection's **end** — the toolbar hangs
   * below where the user stopped dragging, not above the whole span. A long
   * multi-line selection's bounding box is centred somewhere in the middle of
   * the text, which puts the toolbar nowhere near either end of it. */
  bottom: number;
  endX: number;
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

function citationRefsIn(range: Range, host: Element): SelectedCitationRef[] | null {
  const selected = range.cloneRange();
  selected.setStart(
    ...outwardBoundary(range.startContainer, range.startOffset, host),
  );
  selected.setEnd(
    ...outwardBoundary(range.endContainer, range.endOffset, host),
  );
  const refs = new Map<string, SelectedCitationRef>();
  for (const marker of host.querySelectorAll("[data-citation-id]")) {
    if (!selected.intersectsNode(marker)) continue;
    const citationId = marker.getAttribute("data-citation-id");
    const messageId = marker.getAttribute("data-citation-message-id");
    // The quote anchor is not a citation origin. Missing metadata must not
    // silently turn a cited selection into plain text or seal another message.
    if (!citationId || !messageId) return null;
    const key = JSON.stringify([messageId, citationId]);
    if (!refs.has(key)) refs.set(key, { messageId, citationId });
  }
  return [...refs.values()];
}

/** jsdom (and some embedders) do not implement Range.getBoundingClientRect. */
function rectOf(range: Range): DOMRect | null {
  try {
    return range.getBoundingClientRect();
  } catch {
    return null;
  }
}

/** The last line box of the selection. ``getClientRects`` yields one rect per
 * line, so the last one ends where the user stopped dragging; the bounding box
 * would only tell us where the whole span sits. Falls back to the bounding box
 * where ``getClientRects`` is missing (jsdom, some embedders). */
function endRectOf(range: Range): DOMRect | null {
  try {
    const rects = range.getClientRects();
    if (rects.length > 0) return rects[rects.length - 1] ?? null;
  } catch {
    // fall through to the bounding box
  }
  return rectOf(range);
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
    const selectedCitationRefs = citationRefsIn(range, host);
    if (!selectedCitationRefs) {
      setActive(null);
      return;
    }
    const rect = endRectOf(range);
    setActive({
      sessionId,
      messageId,
      text,
      selectedCitationIds: [...new Set(selectedCitationRefs.map((ref) => ref.citationId))],
      selectedCitationRefs,
      bottom: rect?.bottom ?? 0,
      endX: rect?.right ?? 0,
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
      // Hangs below the end of the selection: the text the user just
      // highlighted stays fully visible, and the toolbar lands next to where
      // the pointer already is.
      className="fixed z-50 -translate-x-full"
      style={{ top: active.bottom + 8, left: active.endX }}
    >
      <div className="flex items-center gap-1 rounded-lg border border-surface-border bg-card px-1.5 py-1 shadow-md">
        <SlotRenderer
          name={SELECTION_ACTIONS_SLOT}
          context={{
            sessionId,
            messageId: active.messageId,
            selectedText: active.text,
            selectedCitationIds: active.selectedCitationIds,
            selectedCitationRefs: active.selectedCitationRefs,
            clear,
            insertDraft,
          }}
        />
      </div>
    </div>
  );
}
