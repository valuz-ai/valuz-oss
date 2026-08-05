import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CitationBundleV1, ConversationTurn } from "@valuz/shared";
import { ConversationTurnList } from "./ConversationTurnList";

const processedElapsedName = /已处理 (?:\d+ 秒|\d+ 分(?: \d+ 秒)?)/;

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

function citationBundle(
  citationId: string,
  sourceId: string,
  title: string,
): CitationBundleV1 {
  return {
    version: 1,
    citations: [
      {
        citationId,
        source: {
          sourceId,
          providerId: "test",
          sourceType: "document",
          title,
          retrievedAt: "2026-08-04T00:00:00Z",
        },
        evidence: {
          kind: "text",
          quote: `${title} evidence`,
          snippet: `${title} evidence`,
          capturedAt: "2026-08-04T00:00:00Z",
        },
      },
    ],
  };
}

function projectedCitationBundle(
  citationId: string,
  evidenceHandle: string,
  sourceId: string,
  title: string,
): CitationBundleV1 {
  return {
    ...citationBundle(citationId, sourceId, title),
    projection: {
      evidenceHandleToCitationId: { [evidenceHandle]: citationId },
    },
  };
}

function renderList(
  turns: ConversationTurn[],
  opts: {
    onRetry?: (turnId: string) => void;
    loading?: boolean;
    sending?: boolean;
  } = {},
) {
  const scrollContainerRef = createRef<HTMLDivElement>();
  let api: { scrollToTurnTop: (index: number) => void } | null = null;

  const utils = render(
    <div ref={scrollContainerRef} style={{ height: 640, overflowY: "auto" }}>
      <ConversationTurnList
        turns={turns}
        scrollContainerRef={scrollContainerRef}
        sending={opts.sending ?? false}
        loading={opts.loading ?? false}
        error={null}
        onRetry={opts.onRetry}
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

  it("can scroll to a target turn via virtual API", async () => {
    virtualState.start = 0;
    const turns = Array.from({ length: 220 }, (_, i) => buildTurn(i));

    const { getApi, rerender } = renderList(turns);
    getApi()?.scrollToTurnTop(120);
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve()),
    );

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
      name: "已处理 1 分 25 秒",
    });
    expect(processingToggle).toBeTruthy();
    expect(screen.queryByText("first thinking text")).toBeNull();
    expect(screen.queryByText("second thinking text")).toBeNull();
    expect(screen.queryByText("tool-title")).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "已处理 1 分 25 秒" }),
    ).toHaveLength(1);
    fireEvent.click(processingToggle);
    fireEvent.click(screen.getByRole("button", { name: /调用了 1 次工具/ }));
    expect(screen.getByText(/first thinking text/)).toBeTruthy();
    expect(screen.getByText(/second thinking text/)).toBeTruthy();
    expect(screen.getByText("tool-title")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看详情" })).toBeTruthy();
  });

  it("keeps cancelled assistant turns copyable and retryable", () => {
    virtualState.start = 0;
    const onRetry = vi.fn();
    renderList([{ ...buildTurn(1), cancelled: true }], { onRetry });

    expect(screen.getByText("用户取消了当前对话")).toBeTruthy();
    expect(screen.getAllByTitle("复制")).toHaveLength(2);
    const retry = screen.getByTitle("重试");
    expect(retry).toBeTruthy();

    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledWith("turn-1");
  });

  it("shows actions for a reloaded cancelled turn without assistant text", () => {
    virtualState.start = 0;
    const onRetry = vi.fn();
    renderList([{ ...buildTurn(1), blocks: [], cancelled: true }], { onRetry });

    expect(screen.getByText("用户取消了当前对话")).toBeTruthy();
    expect(screen.getAllByTitle("复制")).toHaveLength(2);
    const retry = screen.getByTitle("重试");
    expect(retry).toBeTruthy();

    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledWith("turn-1");
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
      name: processedElapsedName,
    });
    expect(indicators).toHaveLength(1);
    expect(indicators[0].textContent).toContain("已处理 1 分 30 秒");

    expect(screen.queryByText("tool-title")).toBeNull();
    fireEvent.click(indicators[0]);
    fireEvent.click(screen.getByRole("button", { name: /调用了 1 次工具/ }));
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
      name: processedElapsedName,
    });
    expect(indicators).toHaveLength(1);
    expect(indicators[0].textContent).toContain("已处理 2 分");
  });

  it("merges sources and citation numbering across adjacent answer messages", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-repaired-answer",
        userMessageSeq: 4,
        userText: "research",
        failedMessage: null,
        blocks: [
          {
            kind: "assistant",
            text: "Main answer [source](citation://cit_main).",
            messageId: "message-main",
            citationBundle: citationBundle("cit_main", "doc-main", "Main source"),
          },
          {
            kind: "assistant",
            text: "Repair detail [source](citation://cit_repair).",
            messageId: "message-repair",
            citationBundle: citationBundle(
              "cit_repair",
              "doc-repair",
              "Repair source",
            ),
          },
        ],
      },
    ];

    const { container } = renderList(turns);

    expect(
      container.querySelectorAll("[data-citation-source-list]"),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i })
        .textContent,
    ).toBe("1");
    expect(
      screen.getByRole("button", { name: /(?:citation|引用) 2/i })
        .textContent,
    ).toBe("2");
    expect(screen.getByRole("button", { name: /^1Main source$/i })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /^2Repair source$/i }),
    ).toBeTruthy();
  });

  it("resolves an inline citation from the merged turn bundle on hover", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-cross-message-citation",
        userMessageSeq: 5,
        userText: "research",
        failedMessage: null,
        blocks: [
          {
            kind: "assistant",
            text: "Main answer [source](citation://cit_late).",
            messageId: "message-main",
          },
          {
            kind: "assistant",
            text: "Repair completed.",
            messageId: "message-repair",
            citationBundle: citationBundle(
              "cit_late",
              "doc-late",
              "Late sidecar source",
            ),
          },
        ],
      },
    ];

    renderList(turns);

    const pill = screen.getByRole("button", {
      name: /(?:citation|引用) 1/i,
    });
    fireEvent.mouseEnter(pill);
    expect(screen.getByRole("tooltip").textContent).toContain(
      "Late sidecar source",
    );
    expect(screen.getByRole("tooltip").textContent).toContain(
      "Late sidecar source evidence",
    );
  });

  it("numbers post-publish evidence links through the turn projection", () => {
    virtualState.start = 0;
    const turns: ConversationTurn[] = [
      {
        id: "turn-projected-evidence",
        userMessageSeq: 6,
        userText: "research",
        failedMessage: null,
        blocks: [
          {
            kind: "assistant",
            text: "Revenue [source](evidence://ev_revenue_q2).",
            messageId: "message-projected",
            citationBundle: projectedCitationBundle(
              "cit_revenue_q2",
              "ev_revenue_q2",
              "doc-revenue",
              "Revenue source",
            ),
          },
        ],
      },
    ];

    const { container } = renderList(turns);

    expect(
      screen.getByRole("button", { name: /(?:citation|引用) 1/i }).textContent,
    ).toBe("1");
    expect(container.querySelectorAll("[data-citation-source-list]")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^1Revenue source$/i })).toBeTruthy();
  });
});

describe("ConversationTurnList loading placeholder", () => {
  const shimmer = (container: HTMLElement) =>
    container.querySelector('img[src="/logo.png"]');

  it("shows the shimmer while an existing session's transcript loads", () => {
    // Regression: this state used to render literally nothing — a slow
    // history fetch read as a blank white page.
    const { container } = renderList([], { loading: true });
    expect(shimmer(container)).not.toBeNull();
  });

  it("does not double-render with the sending shimmer", () => {
    const { container } = renderList([], { loading: true, sending: true });
    expect(container.querySelectorAll('img[src="/logo.png"]').length).toBe(1);
  });

  it("hides the shimmer once turns are rendered", () => {
    virtualState.start = 0;
    const { container } = renderList([buildTurn(0)], { loading: true });
    expect(shimmer(container)).toBeNull();
  });

  it("renders nothing for a loaded empty session without error", () => {
    const { container } = renderList([], { loading: false });
    expect(shimmer(container)).toBeNull();
  });
});

describe("UserMessageBody skill-tag rendering", () => {
  it("chips leading commands + real skills — file-path segments stay literal", () => {
    const turns: ConversationTurn[] = [
      {
        id: "t1",
        userMessageSeq: 1,
        // ``/goal`` is a leading command; ``/deep-research`` is a real skill;
        // ``/Users`` / ``/pawa`` are directory segments. A stray whitespace
        // boundary (here spaces; in the wild a ``\r`` between path parts) is
        // what lets the token regex match the path segments at all.
        userText: "/goal 见 /Users /pawa /deep-research 目录",
        blocks: [],
        failedMessage: null,
      },
    ];
    const scrollRef = createRef<HTMLDivElement>();
    const { container } = render(
      <div ref={scrollRef} style={{ height: 640, overflowY: "auto" }}>
        <ConversationTurnList
          turns={turns}
          scrollContainerRef={scrollRef}
          sending={false}
          loading={false}
          error={null}
          skillsBySlug={{ "deep-research": { name: "深度研究" } }}
        />
      </div>,
    );
    const text = container.textContent ?? "";
    // Non-skill path segments keep their literal ``/slug`` form (a chip would
    // consume the leading slash and show the bare word).
    expect(text).toContain("/Users");
    expect(text).toContain("/pawa");
    // The real skill renders as a chip showing its display name; its raw
    // ``/deep-research`` token is consumed into the chip.
    expect(text).toContain("深度研究");
    expect(text).not.toContain("/deep-research");
    // The leading ``/goal`` command chips even though it's not a catalogued
    // skill — its raw ``/goal`` token is consumed into the chip.
    expect(text).toContain("goal");
    expect(text).not.toContain("/goal");
  });
});
