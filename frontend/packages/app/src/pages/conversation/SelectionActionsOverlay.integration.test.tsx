import { useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRegistryStore } from "@valuz/core";
import type { CitationBundleV1, ConversationTurn } from "@valuz/shared";
import { ConversationTurnList } from "@valuz/ui";

import { SelectionActionsOverlay, SELECTION_ACTIONS_SLOT } from "./SelectionActionsOverlay";

// jsdom has no layout. Supply viewport measurements to the real virtualizer;
// the turn, Markdown, CitationPill and slot components remain unmocked.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(640);
});

function bundle(citationId: string, title: string): CitationBundleV1 {
  return { version: 1, citations: [{
    citationId,
    source: { sourceId: title, providerId: "test", sourceType: "document", title, retrievedAt: "2026-09-08T00:00:00Z" },
    evidence: { kind: "text", quote: title, snippet: title, capturedAt: "2026-09-08T00:00:00Z" },
  }] };
}

function Harness({ turns }: { turns: ConversationTurn[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  return <div ref={containerRef}>
    <ConversationTurnList turns={turns} scrollContainerRef={containerRef} sending={false} loading={false} error={null} />
    <SelectionActionsOverlay sessionId="session-1" containerRef={containerRef} />
  </div>;
}

afterEach(() => {
  act(() => {
    document.getSelection()?.removeAllRanges();
    useRegistryStore.getState().unregisterSlot(SELECTION_ACTIONS_SLOT, "integration-action");
  });
  vi.restoreAllMocks();
});

it.each(["single-message", "local", "late-sidecar", "reused-id"])("preserves the rendered %s citation origin through the actual turn and selection slot", (kind) => {
  const received: Record<string, unknown>[] = [];
  useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
    id: "integration-action",
    component: (props) => <button type="button" onClick={() => received.push(props)}>Selected action</button>,
  });
  const main = bundle("cit-selected", "Main source");
  const repair = bundle("cit-selected", "Repair source");
  const turns: ConversationTurn[] = [{
    id: "turn-1", userMessageSeq: 1, userText: "Research", failedMessage: null,
    blocks: [
      { kind: "assistant", messageId: "message-main", text: "Main finding [source](citation://cit-selected).", ...(kind === "late-sidecar" ? {} : { citationBundle: main }) },
      ...(kind === "single-message" ? [] : [{ kind: "assistant" as const, messageId: "message-repair", text: kind === "reused-id" ? "Repaired finding [source](citation://cit-selected)." : "Repair completed.", citationBundle: repair }]),
    ],
  }];
  // Conversation history arrives after the scroll container is mounted.
  const { container, rerender } = render(<Harness turns={[]} />);
  rerender(<Harness turns={turns} />);
  const quoteMessageId = kind === "reused-id" ? "message-repair" : "message-main";
  const originMessageId = kind === "late-sidecar" ? "message-repair" : "message-main";
  const host = container.querySelector(`[data-assistant-message-id="${quoteMessageId}"]`)!;
  expect(host, container.innerHTML).not.toBeNull();
  const marker = host.querySelector("[data-citation-id]")!;
  expect(marker.getAttribute("data-citation-message-id")).toBe(originMessageId);
  const range = document.createRange();
  range.selectNodeContents(host.querySelector("p")!);
  act(() => {
    document.getSelection()!.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
  fireEvent.click(screen.getByRole("button", { name: "Selected action" }));
  expect(received).toHaveLength(1);
  expect(received[0]).toMatchObject({
    sessionId: "session-1", messageId: quoteMessageId,
    selectedCitationIds: ["cit-selected"],
    selectedCitationRefs: [{ messageId: originMessageId, citationId: "cit-selected" }],
  });
});

describe("source-row marker identity", () => {
  it("uses the actual source message passed into calculation source rows", async () => {
    const { CitationPill } = await import("../../../../ui/src/components/conversation/CitationInline");
    const { container } = render(<CitationPill citationId="cit-source-row" messageId="message-source-row" variant="source-row" sourceLabel="Source row" />);
    expect(container.querySelector("[data-citation-id]")?.getAttribute("data-citation-message-id")).toBe("message-source-row");
  });
});
