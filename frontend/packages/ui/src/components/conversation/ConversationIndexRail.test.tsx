/**
 * The rail is a navigation aid layered over a virtualized transcript, so
 * the things worth pinning down are the ones a refactor could quietly
 * break: that a tick maps to the right turn index, that the hover card
 * shows the turn's own prompt (stripped of markdown), and that the rail
 * stays out of the way when there is nothing to navigate or no room to
 * render in.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationTurn } from "@valuz/shared";
import { ConversationIndexRail } from "./ConversationIndexRail";

// jsdom reports every element as 0×0, which would trip the rail's
// width guard in every test. Give it a wide parent by default; the
// narrow case overrides it.
let containerWidth = 1200;

beforeEach(() => {
  containerWidth = 1200;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() {
      return containerWidth;
    },
  });
});

const turn = (
  index: number,
  overrides: Partial<ConversationTurn> = {},
): ConversationTurn => ({
  id: `turn-${index}`,
  userMessageSeq: index,
  userText: `问题 ${index}`,
  blocks: [],
  failedMessage: null,
  ...overrides,
});

const renderRail = (turns: ConversationTurn[], onSelect = vi.fn()) => {
  const utils = render(
    <div>
      <ConversationIndexRail
        turns={turns}
        activeIndex={0}
        onSelect={onSelect}
      />
    </div>,
  );
  return { ...utils, onSelect };
};

describe("ConversationIndexRail", () => {
  it("renders one tick per turn", () => {
    renderRail(Array.from({ length: 7 }, (_, i) => turn(i)));
    expect(screen.getAllByRole("button")).toHaveLength(7);
  });

  it("stays hidden until the transcript is worth navigating", () => {
    renderRail(Array.from({ length: 4 }, (_, i) => turn(i)));
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("hides itself when the transcript column leaves no gutter", () => {
    containerWidth = 500;
    renderRail(Array.from({ length: 7 }, (_, i) => turn(i)));
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("reports the clicked turn's index", () => {
    const { onSelect } = renderRail(
      Array.from({ length: 7 }, (_, i) => turn(i)),
    );
    fireEvent.click(screen.getAllByRole("button")[3]!);
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("shows the turn's prompt as plain text on hover, and drops it on leave", () => {
    renderRail([
      ...Array.from({ length: 5 }, (_, i) => turn(i)),
      turn(5, { userText: "/research **看看** 这份财报\n\n第三章" }),
    ]);
    const tick = screen.getAllByRole("button")[5]!;
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseEnter(tick);
    const card = screen.getByRole("tooltip");
    expect(card.textContent).toContain("看看 这份财报 第三章");
    expect(card.textContent).toContain("#6");

    fireEvent.mouseLeave(tick);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("falls back to the attachment name for a turn with no text", () => {
    renderRail([
      ...Array.from({ length: 5 }, (_, i) => turn(i)),
      turn(5, { userText: "", attachments: [{ name: "年报.pdf", size: 10 }] }),
    ]);
    fireEvent.mouseEnter(screen.getAllByRole("button")[5]!);
    expect(screen.getByRole("tooltip").textContent).toContain("年报.pdf");
  });

  it("summarizes what the assistant did in that turn", () => {
    renderRail([
      ...Array.from({ length: 5 }, (_, i) => turn(i)),
      turn(5, {
        blocks: [
          { kind: "assistant", text: "先看一下" },
          {
            kind: "tool",
            tool: { id: "t1", kind: "bash", title: "Bash", status: "success" },
          },
          {
            kind: "tool",
            tool: { id: "t2", kind: "bash", title: "Bash", status: "success" },
          },
        ],
      }),
    ]);
    fireEvent.mouseEnter(screen.getAllByRole("button")[5]!);
    // Same phrase pipeline the transcript and the activity dashboard use.
    expect(screen.getByRole("tooltip").textContent).toMatch(/2/);
  });

  it("magnifies the hovered tick and tapers its neighbours", () => {
    renderRail(Array.from({ length: 11 }, (_, i) => turn(i)));
    const ticks = screen.getAllByRole("button");
    const widthAt = (i: number) =>
      parseFloat(
        (ticks[i]!.querySelector("[data-tick-bar]") as HTMLElement).style.width,
      );

    fireEvent.mouseEnter(ticks[5]!);
    // Strictly decreasing away from the cursor, symmetric on both sides,
    // and flat again once past the magnification radius.
    expect(widthAt(5)).toBeGreaterThan(widthAt(6));
    expect(widthAt(6)).toBeGreaterThan(widthAt(7));
    expect(widthAt(7)).toBeGreaterThan(widthAt(8));
    expect(widthAt(4)).toBeCloseTo(widthAt(6));
    expect(widthAt(3)).toBeCloseTo(widthAt(7));
    expect(widthAt(9)).toBeCloseTo(widthAt(0));
    expect(widthAt(10)).toBeCloseTo(widthAt(0));
  });

  it("falls back to a flat rail with only the active tick raised", () => {
    renderRail(Array.from({ length: 7 }, (_, i) => turn(i)));
    const ticks = screen.getAllByRole("button");
    const widthAt = (i: number) =>
      parseFloat(
        (ticks[i]!.querySelector("[data-tick-bar]") as HTMLElement).style.width,
      );
    // activeIndex is 0 in these renders.
    expect(widthAt(0)).toBeGreaterThan(widthAt(1));
    expect(widthAt(1)).toBeCloseTo(widthAt(6));
  });

  it("follows the cursor between ticks, not just onto them", () => {
    // Regression: the magnification once mixed coordinate spaces —
    // ``offsetTop`` (measured against the full-height positioned nav)
    // against a track-relative cursor Y — which pinned the peak to one
    // end of the rail no matter where the pointer was. jsdom reports all
    // rects as zero, so the geometry has to be supplied here.
    renderRail(Array.from({ length: 9 }, (_, i) => turn(i)));
    const ticks = screen.getAllByRole("button");
    ticks.forEach((tick, i) => {
      tick.getBoundingClientRect = () =>
        ({ top: 100 + i * 8, height: 8, left: 0, width: 26 }) as DOMRect;
    });
    const track = ticks[0]!.parentElement!;
    const widthAt = (i: number) =>
      parseFloat(
        (ticks[i]!.querySelector("[data-tick-bar]") as HTMLElement).style.width,
      );

    // Centre of tick 5 → that tick peaks.
    fireEvent.mouseMove(track, { clientY: 100 + 5 * 8 + 4 });
    expect(widthAt(5)).toBeGreaterThan(widthAt(4));
    expect(widthAt(5)).toBeGreaterThan(widthAt(6));

    // Exactly between 5 and 6 → both sit just below the peak, and level
    // with each other. This is the half of the behaviour a per-tick
    // hover handler cannot express.
    fireEvent.mouseMove(track, { clientY: 100 + 5 * 8 + 8 });
    expect(widthAt(5)).toBeCloseTo(widthAt(6), 5);
    expect(widthAt(5)).toBeGreaterThan(widthAt(4));
    expect(widthAt(6)).toBeGreaterThan(widthAt(7));

    // Past the last tick → clamped, not wrapped or blown up.
    fireEvent.mouseMove(track, { clientY: 100 + 40 * 8 });
    expect(widthAt(8)).toBeGreaterThan(widthAt(7));
    expect(screen.getByRole("tooltip").textContent).toContain("#9");
  });

  it("opens the same card on keyboard focus", () => {
    renderRail(Array.from({ length: 7 }, (_, i) => turn(i)));
    fireEvent.focus(screen.getAllByRole("button")[2]!);
    expect(screen.getByRole("tooltip").textContent).toContain("问题 2");
  });
});
