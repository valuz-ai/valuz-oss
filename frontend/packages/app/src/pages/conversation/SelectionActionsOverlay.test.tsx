import { useRegistryStore } from "@valuz/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";

import {
  SELECTION_ACTIONS_SLOT,
  SelectionActionsOverlay,
} from "./SelectionActionsOverlay";

function Harness({ sessionId = "session-1" }: { sessionId?: string | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  return (
    <div ref={containerRef}>
      <div data-assistant-message-id="msg-1">
        <p>NVIDIA data centre revenue grew again this quarter.</p>
      </div>
      <div data-assistant-message-id="msg-2">
        <p>Margins were stable.</p>
      </div>
      <p>Outside the transcript anchors.</p>
      <SelectionActionsOverlay
        sessionId={sessionId}
        containerRef={containerRef}
      />
    </div>
  );
}

function selectTextIn(element: Element) {
  const range = document.createRange();
  range.selectNodeContents(element);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
  });
}

function clearSelection() {
  document.getSelection()?.removeAllRanges();
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
  });
}

describe("SelectionActionsOverlay", () => {
  afterEach(() => {
    clearSelection();
    useRegistryStore
      .getState()
      .unregisterSlot(SELECTION_ACTIONS_SLOT, "test-action");
  });

  it("renders nothing on selection when no overlay registered the slot", () => {
    render(<Harness />);
    selectTextIn(screen.getByText(/data centre revenue/));
    expect(screen.queryByRole("toolbar")).toBeNull();
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
});
