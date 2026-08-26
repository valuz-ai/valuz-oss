/**
 * The turn header runs TWO counters, one per phase, and never mixes them.
 *
 * Startup is measured on the client clock (Send → now) under its own label;
 * processing restarts at zero on the kernel's ``message.user`` stamp. A single
 * counter spanning both would disagree with itself across a refresh —
 * ``clientSentAtMs`` is React state and does not survive one — so a live turn
 * would include the startup window and a reloaded one would not, a gap of tens
 * of seconds on a cold sandbox.
 *
 * The header is also held back for the first {@link HEADER_REVEAL_DELAY_MS} of
 * a turn: a local runtime usually starts inside that window, and a label that
 * renders for three frames is worse than no label.
 */
import { createRef } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConversationTurn } from "@valuz/shared";
import { ConversationTurnList } from "./ConversationTurnList";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }).map((_, index) => ({
        index,
        start: index * 220,
      })),
    getTotalSize: () => count * 220,
    measureElement: () => {},
    scrollToIndex: () => {},
  }),
}));

const T0 = 1_700_000_000_000; // arbitrary fixed epoch — the test owns the clock
const BOOT_MS = 20_000; // Send → runtime up
const NOW_MS = 30_000; // Send → "now"
const REVEAL_MS = 500; // must match HEADER_REVEAL_DELAY_MS

function renderTurn(
  turn: ConversationTurn,
  opts: { sending: boolean; startingRuntime?: "local" | "cloud" | null },
) {
  const scrollContainerRef = createRef<HTMLDivElement>();
  return render(
    <div ref={scrollContainerRef}>
      <ConversationTurnList
        turns={[turn]}
        scrollContainerRef={scrollContainerRef}
        sending={opts.sending}
        loading={false}
        error={null}
        startingRuntime={opts.startingRuntime}
      />
    </div>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("turn header elapsed", () => {
  it("restarts the counter at zero when the runtime reports in", () => {
    vi.useFakeTimers();
    // Runtime came up 20s after Send; it has been processing for 10s since.
    vi.setSystemTime(T0 + BOOT_MS + 10_000);

    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: null },
    );

    // 10s of processing — NOT 30s. The startup window belongs to the other
    // phase, and counting it here is exactly what a refresh could not
    // reproduce (``clientSentAtMs`` is gone after one).
    expect(screen.getByText("已处理 10 秒")).toBeTruthy();
    expect(screen.queryByText("已处理 30 秒")).toBeNull();
  });

  it("agrees with a reloaded turn, which has no Send stamp at all", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + BOOT_MS + 10_000);

    // Same turn as above, rebuilt from history: no ``clientSentAtMs``.
    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
      },
      { sending: true, startingRuntime: null },
    );

    expect(screen.getByText("已处理 10 秒")).toBeTruthy();
  });

  it("counts the startup phase on its own clock, under its own label", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + NOW_MS);

    const { unmount } = renderTurn(
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: "cloud" },
    );
    expect(screen.getByText("正在启动云端运行环境 · 30 秒")).toBeTruthy();
    unmount();

    renderTurn(
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: "local" },
    );
    expect(screen.getByText("正在启动本地运行环境 · 30 秒")).toBeTruthy();
  });

  it("holds the header back for the first half second so a fast local start doesn't flash", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 100); // 100ms in — well inside the reveal delay

    renderTurn(
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: "hi",
        blocks: [],
        failedMessage: null,
        userTimestamp: T0,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: "local" },
    );

    expect(screen.queryByText(/正在启动/)).toBeNull();
    expect(screen.queryByText(/已处理/)).toBeNull();

    // Crossing the threshold reveals it.
    act(() => {
      vi.advanceTimersByTime(REVEAL_MS);
    });
    expect(screen.getByText(/正在启动本地运行环境/)).toBeTruthy();
  });

  it("stops claiming the runtime is starting once the turn has real content", () => {
    // Regression: the startup label is driven by the host page's pending
    // send, which used to be released only by the LIVE ``message.user``
    // handler. When the echo instead arrived in the history refetch that
    // bootstrap runs on landing, nothing released it — so the header sat on
    // "正在启动云端运行环境" counting upwards while the agent was already
    // answering on screen. The host now retires the pending as soon as the
    // echo is visible; this pins the renderer's half of the contract: with
    // ``startingRuntime`` cleared, a turn that has content reads as processing.
    vi.useFakeTimers();
    vi.setSystemTime(T0 + BOOT_MS + 8_000);

    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "你好",
        blocks: [{ kind: "assistant", text: "你好！有什么我可以帮你的吗？" }],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
        clientSentAtMs: T0,
      },
      { sending: true, startingRuntime: null },
    );

    expect(screen.queryByText(/正在启动/)).toBeNull();
    expect(screen.getByText("已处理 8 秒")).toBeTruthy();
  });

  it("shows a settled turn's header immediately — the delay is for live turns", () => {
    renderTurn(
      {
        id: "turn-1",
        userMessageSeq: 1,
        userText: "hi",
        blocks: [
          {
            kind: "tool",
            tool: {
              id: "t1",
              kind: "bash",
              title: "Bash",
              status: "success",
            },
            elapsedMs: 5_000,
          },
        ],
        failedMessage: null,
        userTimestamp: T0 + BOOT_MS,
        clientSentAtMs: T0,
      },
      { sending: false },
    );

    // 5s of work — the boot window is NOT folded in, so this is the same
    // number the turn will show forever, refresh or not.
    expect(screen.getByText("已处理 5 秒")).toBeTruthy();
  });
});
