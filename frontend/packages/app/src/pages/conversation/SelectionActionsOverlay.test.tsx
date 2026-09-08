import { useRegistryStore } from "@valuz/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef, type ReactNode } from "react";

import {
  SELECTION_ACTIONS_SLOT,
  SelectionActionsOverlay,
} from "./SelectionActionsOverlay";

function Harness({
  sessionId = "session-1",
  insertDraft,
  content,
}: {
  sessionId?: string | null;
  insertDraft?: (text: string) => void;
  content?: ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  return (
    <div ref={containerRef}>
      <div data-assistant-message-id="msg-1">
        {content ?? <p>NVIDIA data centre revenue grew again this quarter.</p>}
      </div>
      <div data-assistant-message-id="msg-2">
        <p>Margins were stable.</p>
      </div>
      <p>Outside the transcript anchors.</p>
      <SelectionActionsOverlay
        sessionId={sessionId}
        containerRef={containerRef}
        insertDraft={insertDraft}
      />
    </div>
  );
}

function selectTextIn(element: Element) {
  const range = document.createRange();
  range.selectNodeContents(element);
  selectRange(range);
}

function selectRange(range: Range) {
  const selection = document.getSelection()!;
  act(() => {
    selection.removeAllRanges();
    selection.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
}

function clearSelection() {
  act(() => {
    document.getSelection()?.removeAllRanges();
    document.dispatchEvent(new Event("selectionchange"));
  });
}

describe("SelectionActionsOverlay", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    clearSelection();
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot(SELECTION_ACTIONS_SLOT, "test-action");
    });
  });

  it("renders nothing on selection when no overlay registered the slot", () => {
    const { rerender } = render(<Harness />);
    selectTextIn(screen.getByText(/data centre revenue/));
    expect(screen.queryByRole("toolbar")).toBeNull();
    const text = document.getSelection()!.toString();
    rerender(<Harness sessionId="session-2" />);
    expect(document.getSelection()!.toString()).toBe(text);
  });

  it("floats registered actions over a single assistant-message selection", () => {
    const received: Record<string, unknown>[] = [];
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: (props: Record<string, unknown>) => (
        <button type="button" onClick={() => received.push(props)}>
          加入研究
        </button>
      ),
    });

    render(<Harness />);
    selectTextIn(screen.getByText(/data centre revenue/));

    fireEvent.click(screen.getByRole("button", { name: "加入研究" }));
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({
      sessionId: "session-1",
      messageId: "msg-1",
      selectedText: "NVIDIA data centre revenue grew again this quarter.",
      selectedCitationIds: [],
      selectedCitationRefs: [],
    });
    expect(typeof received[0].clear).toBe("function");
  });

  it("hides for selections spanning two messages or outside an anchor", () => {
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: () => <button type="button">加入研究</button>,
    });

    const { container } = render(<Harness />);
    // Spanning both assistant messages → common ancestor has no anchor.
    selectTextIn(container.firstElementChild!);
    expect(screen.queryByRole("toolbar")).toBeNull();

    selectTextIn(screen.getByText("Outside the transcript anchors."));
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("dismisses when the selection collapses and when clear() is called", () => {
    const clears: Array<() => void> = [];
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: (props: Record<string, unknown>) => {
        clears.push(props.clear as () => void);
        return <button type="button">加入研究</button>;
      },
    });

    render(<Harness />);
    selectTextIn(screen.getByText("Margins were stable."));
    expect(screen.getByRole("toolbar")).toBeTruthy();

    clearSelection();
    expect(screen.queryByRole("toolbar")).toBeNull();

    selectTextIn(screen.getByText("Margins were stable."));
    expect(screen.getByRole("toolbar")).toBeTruthy();
    act(() => clears.at(-1)!());
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("keeps the selection alive on toolbar mousedown", () => {
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: () => <button type="button">加入研究</button>,
    });

    render(<Harness />);
    selectTextIn(screen.getByText("Margins were stable."));
    const toolbar = screen.getByRole("toolbar");
    const mouseDown = fireEvent.mouseDown(toolbar);
    // ``false`` means preventDefault ran — the browser will not collapse the
    // selection out from under the click.
    expect(mouseDown).toBe(false);
  });

  it("hands the host's insertDraft to slot components", () => {
    const drafts: string[] = [];
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: (props: Record<string, unknown>) => (
        <button
          type="button"
          onClick={() =>
            (props.insertDraft as (text: string) => void)(
              `加入研究：${String(props.selectedText)}`,
            )
          }
        >
          加入研究
        </button>
      ),
    });

    render(<Harness insertDraft={(text) => drafts.push(text)} />);
    selectTextIn(screen.getByText("Margins were stable."));
    fireEvent.click(screen.getByRole("button", { name: "加入研究" }));

    expect(drafts).toEqual(["加入研究：Margins were stable."]);
  });

  it("ignores registrations arriving without any selection", () => {
    const spy = vi.fn();
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: () => {
        spy();
        return null;
      },
    });
    render(<Harness />);
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  function captureSelection() {
    const received: Record<string, unknown>[] = [];
    useRegistryStore.getState().registerSlot(SELECTION_ACTIONS_SLOT, {
      id: "test-action",
      component: (props: Record<string, unknown>) => (
        <button type="button" onClick={() => received.push(props)}>Selected action</button>
      ),
    });
    return () => {
      fireEvent.click(screen.getByRole("button", { name: "Selected action" }));
      return received.at(-1)!;
    };
  }

  it("extracts only citations inside the selected part of a message", () => {
    const readSelection = captureSelection();
    render(<Harness content={<p data-testid="claim">First claim <button data-citation-id="cit-first" data-citation-message-id="msg-1">[1]</button> second claim <button data-citation-id="cit-second" data-citation-message-id="msg-1">[2]</button></p>} />);
    const claim = screen.getByTestId("claim");
    const range = document.createRange();
    range.setStart(claim.firstChild!, 0);
    range.setEnd(claim.childNodes[2], 7);
    selectRange(range);
    expect(readSelection()).toMatchObject({ selectedText: "First claim [1] second", selectedCitationIds: ["cit-first"], selectedCitationRefs: [{ messageId: "msg-1", citationId: "cit-first" }] });

    range.setEnd(claim.firstChild!, 5);
    selectRange(range);
    expect(readSelection()).toMatchObject({ selectedText: "First", selectedCitationIds: [] });
  });

  it.each(["element", "nested-text"])("excludes adjacent citation markers touched only at %s boundaries", (boundary) => {
    const readSelection = captureSelection();
    render(<Harness content={<p data-testid="claim"><button data-citation-id="cit-before"><span>[1]</span></button><span data-testid="words">Selected words</span><button data-citation-id="cit-after"><span>[2]</span></button></p>} />);
    const claim = screen.getByTestId("claim");
    const range = document.createRange();
    if (boundary === "element") {
      range.setStart(claim, 1);
      range.setEnd(claim, 2);
    } else {
      const before = claim.firstElementChild!.firstElementChild!.firstChild!;
      const after = claim.lastElementChild!.firstElementChild!.firstChild!;
      range.setStart(before, before.textContent!.length);
      range.setEnd(after, 0);
    }
    selectRange(range);
    expect(readSelection()).toMatchObject({ selectedText: "Selected words", selectedCitationIds: [] });
  });

  it("deduplicates selected citation ids in DOM order and includes icon-only markers", () => {
    const readSelection = captureSelection();
    render(<Harness content={<p data-testid="claim">Claim <button data-citation-id="cit-z" data-citation-message-id="msg-1">[2]</button><button data-citation-id="cit-a" data-citation-message-id="msg-1">[1]</button><button data-citation-id="cit-z" data-citation-message-id="msg-1">[2]</button><button data-citation-id="cit-calculation" data-citation-message-id="msg-1"><svg><path d="M0 0" /></svg></button></p>} />);
    selectTextIn(screen.getByTestId("claim"));
    expect(readSelection().selectedCitationIds).toEqual(["cit-z", "cit-a", "cit-calculation"]);
  });

  it("includes a marker whose nested text is only partially selected", () => {
    const readSelection = captureSelection();
    render(<Harness content={<p><button data-citation-id="cit-partial" data-citation-message-id="msg-1"><span data-testid="label">Source 12</span></button></p>} />);
    const text = screen.getByTestId("label").firstChild!;
    const range = document.createRange();
    range.setStart(text, 2);
    range.setEnd(text, 5);
    selectRange(range);
    expect(readSelection()).toMatchObject({ selectedText: "urc", selectedCitationIds: ["cit-partial"] });
  });

  it("rejects multiple real DOM ranges instead of using only the first", () => {
    captureSelection();
    render(<Harness />);
    const first = document.createRange();
    first.selectNodeContents(screen.getByText(/data centre revenue/));
    const second = document.createRange();
    second.selectNodeContents(screen.getByText("Margins were stable."));
    selectRange(first);
    expect(screen.getByRole("toolbar")).toBeTruthy();
    // jsdom stores one range only; emulate the browser's multi-range Selection
    // container while retaining real DOM Range instances and their boundaries.
    const selection = document.getSelection()!;
    vi.spyOn(selection, "rangeCount", "get").mockReturnValue(2);
    vi.spyOn(selection, "getRangeAt").mockImplementation((index) => [first, second][index]);
    act(() => document.dispatchEvent(new Event("selectionchange")));
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("deduplicates composite citation identities without collapsing another message's same id", () => {
    const readSelection = captureSelection();
    render(<Harness content={<p data-testid="claim">Claim <span data-citation-id="cit-shared" data-citation-message-id="origin-2">[1]</span><span data-citation-id="cit-shared" data-citation-message-id="origin-1">[2]</span><span data-citation-id="cit-shared" data-citation-message-id="origin-2">[1]</span></p>} />);
    selectTextIn(screen.getByTestId("claim"));
    expect(readSelection()).toMatchObject({
      messageId: "msg-1",
      selectedCitationIds: ["cit-shared"],
      selectedCitationRefs: [
        { messageId: "origin-2", citationId: "cit-shared" },
        { messageId: "origin-1", citationId: "cit-shared" },
      ],
    });
  });

  it("does not guess the message anchor for a selected marker with no origin", () => {
    captureSelection();
    render(<Harness content={<p data-testid="claim">Claim <span data-citation-id="cit-unknown">[1]</span></p>} />);
    selectTextIn(screen.getByTestId("claim"));
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("rejects a range with endpoints inside different assistant messages", () => {
    captureSelection();
    render(<Harness />);
    const range = document.createRange();
    range.setStart(screen.getByText(/data centre revenue/).firstChild!, 7);
    range.setEnd(screen.getByText("Margins were stable.").firstChild!, 7);
    selectRange(range);
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("never reuses the previous session's active selection after switching sessions", () => {
    const readSelection = captureSelection();
    const { rerender } = render(<Harness sessionId="session-1" />);
    selectTextIn(screen.getByText("Margins were stable."));
    expect(readSelection().sessionId).toBe("session-1");
    rerender(<Harness sessionId="session-2" />);
    expect(screen.queryByRole("toolbar")).toBeNull();
    act(() => document.dispatchEvent(new Event("selectionchange")));
    expect(screen.queryByRole("toolbar")).toBeNull();
    selectTextIn(screen.getByText(/data centre revenue/));
    expect(readSelection()).toMatchObject({ sessionId: "session-2", messageId: "msg-1", selectedCitationIds: [] });
  });
});
