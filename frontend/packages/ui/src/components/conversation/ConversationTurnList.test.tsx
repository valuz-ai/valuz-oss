import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ConversationTurn } from "@valuz/shared";
import { ConversationTurnList } from "./ConversationTurnList";

const virtualState = {
  start: 0,
  windowSize: 10,
};

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getVirtualItems: () => {
      const end = Math.min(count, virtualState.start + virtualState.windowSize);
      return Array.from({ length: Math.max(0, end - virtualState.start) }).map(
        (_, idx) => {
          const index = virtualState.start + idx;
          return {
            index,
            start: index * 220,
          };
        },
      );
    },
    getTotalSize: () => count * 220,
    measureElement: () => {},
    scrollToIndex: (index: number) => {
      virtualState.start = Math.max(0, index);
    },
  }),
}));

function buildTurn(i: number): ConversationTurn {
  return {
    id: `turn-${i}`,
    userMessageSeq: i,
    userText: `user-${i}`,
    blocks: [{ kind: "assistant", text: `assistant-${i}` }],
    failedMessage: null,
  };
}

function renderList(turns: ConversationTurn[]) {
  const scrollContainerRef = createRef<HTMLDivElement>();
  let api: { scrollToTurnTop: (index: number) => void } | null = null;

  const utils = render(
    <div ref={scrollContainerRef} style={{ height: 640, overflowY: "auto" }}>
      <ConversationTurnList
        turns={turns}
        scrollContainerRef={scrollContainerRef}
        sending={false}
        loading={false}
        error={null}
        onVirtualApiReady={(nextApi) => {
          api = nextApi;
        }}
      />
    </div>,
  );

  return {
    ...utils,
    getApi: () => api,
  };
}

describe("ConversationTurnList virtualization", () => {
  it("renders only a virtual window instead of all turns", () => {
    virtualState.start = 0;
    const turns = Array.from({ length: 220 }, (_, i) => buildTurn(i));

    const { container } = renderList(turns);

    const renderedTurns = container.querySelectorAll(
      "[data-conversation-turn]",
    );
    expect(renderedTurns.length).toBeLessThan(turns.length);
    expect(renderedTurns.length).toBe(10);
  });

  it("can scroll to a target turn via virtual API", () => {
    virtualState.start = 0;
    const turns = Array.from({ length: 220 }, (_, i) => buildTurn(i));

    const { getApi, rerender } = renderList(turns);
    getApi()?.scrollToTurnTop(120);

    const scrollContainerRef = createRef<HTMLDivElement>();
    rerender(
      <div ref={scrollContainerRef} style={{ height: 640, overflowY: "auto" }}>
        <ConversationTurnList
          turns={turns}
          scrollContainerRef={scrollContainerRef}
          sending={false}
          loading={false}
          error={null}
          onVirtualApiReady={() => {}}
        />
      </div>,
    );

    expect(screen.getByText("assistant-120")).toBeTruthy();
  });

  it("keeps thinking/tool/failed rendering in virtual rows", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-special",
        userMessageSeq: 1,
        userText: "special-user",
        failedMessage: "failed-msg",
        blocks: [
          { kind: "thinking", text: "first thinking text", elapsedMs: 55000 },
          { kind: "thinking", text: "second thinking text", elapsedMs: 85000 },
          {
            kind: "tool",
            tool: {
              id: "tool-1",
              kind: "bash",
              title: "tool-title",
              status: "success",
              output: "ok",
            },
          },
          { kind: "assistant", text: "assistant body" },
        ],
      },
    ];

    renderList(turns);

    const processingToggle = screen.getByRole("button", {
      name: "已处理 85 秒",
    });
    expect(processingToggle).toBeTruthy();
    expect(screen.queryByText("first thinking text")).toBeNull();
    expect(screen.queryByText("second thinking text")).toBeNull();
    expect(screen.queryByText("tool-title")).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "已处理 85 秒" }),
    ).toHaveLength(1);
    fireEvent.click(processingToggle);
    expect(screen.getByText(/first thinking text/)).toBeTruthy();
    expect(screen.getByText(/second thinking text/)).toBeTruthy();
    expect(screen.getByText("tool-title")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看详情" })).toBeTruthy();
  });

  it("renders a single processing indicator that wraps interleaved thinking and tool calls", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-interleaved",
        userMessageSeq: 2,
        userText: "user-msg",
        failedMessage: null,
        blocks: [
          { kind: "thinking", text: "before tool", elapsedMs: 40000 },
          {
            kind: "tool",
            tool: {
              id: "tool-1",
              kind: "bash",
              title: "tool-title",
              status: "success",
              output: "ok",
            },
          },
          { kind: "thinking", text: "after tool", elapsedMs: 90000 },
          { kind: "assistant", text: "final answer" },
        ],
      },
    ];

    renderList(turns);

    const indicators = screen.getAllByRole("button", {
      name: /已处理 \d+ 秒/,
    });
    expect(indicators).toHaveLength(1);
    expect(indicators[0].textContent).toContain("已处理 90 秒");

    expect(screen.queryByText("tool-title")).toBeNull();
    fireEvent.click(indicators[0]);
    expect(screen.getByText("tool-title")).toBeTruthy();
    expect(screen.getByText(/before tool/)).toBeTruthy();
    expect(screen.getByText(/after tool/)).toBeTruthy();
  });

  it("uses tool elapsedMs when the last block is a tool call without trailing thinking", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-tool-trailing",
        userMessageSeq: 3,
        userText: "user-msg",
        failedMessage: null,
        blocks: [
          { kind: "thinking", text: "early thinking", elapsedMs: 30000 },
          {
            kind: "tool",
            tool: {
              id: "tool-1",
              kind: "bash",
              title: "tool-title",
              status: "success",
              output: "ok",
            },
            elapsedMs: 120000,
          },
        ],
      },
    ];

    renderList(turns);

    const indicators = screen.getAllByRole("button", {
      name: /已处理 \d+ 秒/,
    });
    expect(indicators).toHaveLength(1);
    expect(indicators[0].textContent).toContain("已处理 120 秒");
  });
});
