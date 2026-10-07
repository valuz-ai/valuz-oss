import { useRegistryStore } from "@valuz/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRef, type ReactNode } from "react";

import { ConversationBody } from "./ConversationBody";

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

const noop = () => undefined;
const HINT = "回复可继续调整计划";

type Turn = {
  id: string;
  userText: string;
  blocks: Array<Record<string, unknown>>;
  failedMessage: string | null;
};

const turn = (i: number, plan = false): Turn => ({
  id: `turn-${i}`,
  userText: `user-${i}`,
  blocks: [
    plan
      ? { kind: "plan_proposal", plan: `# Plan ${i}` }
      : { kind: "assistant", text: `assistant-${i}` },
  ],
  failedMessage: null,
});

function renderBody(over: Record<string, unknown> = {}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const props: any = {
    id: "s1",
    loading: false,
    providers: [{ id: "p" }],
    providerChannelState: { status: "ready" },
    scrollContainerRef: createRef(),
    hasMoreOlder: false,
    loadingOlder: false,
    topSentinelRef: createRef(),
    userScrolledRef: { current: false },
    loadOlderTurns: noop,
    conversationInstanceKey: "k",
    effectiveTurns: [],
    displayBusy: false,
    postRunVerificationActive: false,
    error: null,
    handleRetry: noop,
    retryCounts: {},
    containerHeight: 0,
    skillsBySlug: {},
    handleTurnListVirtualApiReady: noop,
    scrollToTurnIndex: noop,
    renderToolCall: () => null,
    isToolCardFoldable: () => false,
    revealInFinder: noop,
    localFileLinks: { isLocalFileHref: () => false, openLocalFileHref: noop },
    openArtifactFile: noop,
    selectedSessionId: "s1",
    openCitation: noop,
    setDraft: noop,
    hasPendingProjectSend: false,
    startingRuntime: null,
    ...over,
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
  const ui = (p: unknown) => (
    <MemoryRouter>
      <ConversationBody {...(p as typeof props)} />
    </MemoryRouter>
  );
  const utils = render(ui(props));
  // The turn list reads the scroll container's ref during render; the ref is
  // attached by this first commit, so one more render lets the rows mount.
  utils.rerender(ui(props));
  return {
    ...utils,
    rerenderWith: (next: Record<string, unknown>) =>
      utils.rerender(ui({ ...props, ...next })),
  };
}

const register = (
  name: string,
  component: (props: Record<string, unknown>) => ReactNode,
  id = `t-${name}`,
) =>
  act(() => {
    useRegistryStore
      .getState()
      .registerSlot(name, { id, component: component as never });
  });

describe("ConversationBody slots", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  it("places installation after the report, before the file summary and action row", () => {
    register("conversation.turn.actions", () => <button>回复操作</button>);
    const finished = {
      ...turn(1),
      blocks: [
        { kind: "tool", tool: { id: "write", kind: "file", title: "Write", status: "success", input: JSON.stringify({ file_path: "/work/plugin.tsx", content: "export default {};" }) } },
        { kind: "tool", tool: { id: "install", kind: "generic", title: "app_plugin_manager", status: "success" } },
        { kind: "assistant", text: "插件制作完成" },
      ],
    };
    const body = renderBody({
      effectiveTurns: [finished],
      isToolCardTrailing: (tool: { id: string }) => tool.id === "install",
      renderToolCall: (tool: { id: string }) => tool.id === "install" ? <button>安装并启用</button> : null,
    });
    const report = screen.getByText("插件制作完成");
    const card = screen.getByRole("button", { name: "安装并启用" });
    const summary = screen.getByTestId("turn-diff-summary");
    const actions = screen.getByRole("button", { name: "回复操作" });
    for (const [before, after] of [[report, card], [card, summary], [summary, actions]]) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    body.rerenderWith({ displayBusy: true });
    expect(screen.queryByRole("button", { name: "安装并启用" })).toBeNull();
    body.rerenderWith({ displayBusy: false });
    expect(screen.getByRole("button", { name: "安装并启用" })).toBeTruthy();
    expect(screen.getByText("插件制作完成")).toBeTruthy();
  });

  describe("conversation.empty.no-model", () => {
    const noModel = { id: "new", providers: [] };

    it("shows the stock empty state while the slot is empty", () => {
      renderBody(noModel);
      expect(screen.getByText("尚未配置 AI 模型")).toBeTruthy();
      expect(screen.getByRole("button", { name: "前往设置" })).toBeTruthy();
    });

    it("lets a plugin replace it, handing over navigate", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.empty.no-model", (props) => {
        ctx = props;
        return <div data-testid="ext-no-model" />;
      });
      renderBody(noModel);
      expect(screen.getByTestId("ext-no-model")).toBeTruthy();
      expect(screen.queryByText("尚未配置 AI 模型")).toBeNull();
      expect(typeof ctx.navigate).toBe("function");
    });
  });

  describe("welcome slots", () => {
    const welcome = {
      id: "new",
      selectedSessionId: null,
      projectId: "proj-1",
      agentSlug: "valurion",
    };
    const defaultTitle = /告诉我你想做什么/;
    const mascot = (c: HTMLElement) =>
      c.querySelector("img[aria-hidden='true']");

    it("renders the stock mascot, title and suggestions while every slot is empty", () => {
      const { container } = renderBody(welcome);
      expect(mascot(container)).not.toBeNull();
      expect(screen.getByText(defaultTitle)).toBeTruthy();
      expect(
        screen.getByText("帮我梳理这个需求，并拆成可执行计划"),
      ).toBeTruthy();
    });

    it("conversation.empty.hero replaces mascot and title, with variant in context", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.empty.hero", (props) => {
        ctx = props;
        return <div data-testid="ext-hero" />;
      });
      const setDraft = vi.fn();
      const { container } = renderBody({
        ...welcome,
        setDraft,
        variant: "panel",
      });
      expect(screen.getByTestId("ext-hero")).toBeTruthy();
      expect(mascot(container)).toBeNull();
      expect(screen.queryByText(defaultTitle)).toBeNull();
      // Suggestions are not part of the hero.
      expect(
        screen.getByText("帮我梳理这个需求，并拆成可执行计划"),
      ).toBeTruthy();
      expect(ctx).toMatchObject({
        projectId: "proj-1",
        agentSlug: "valurion",
        setDraft,
        variant: "panel",
      });
    });

    it("an embedding host's title override wins over the hero", () => {
      register("conversation.empty.hero", () => <div data-testid="ext-hero" />);
      renderBody({ ...welcome, emptyStateOverride: { title: "Host title" } });
      expect(screen.queryByTestId("ext-hero")).toBeNull();
      expect(screen.getByText("Host title")).toBeTruthy();
    });

    it("an embedding host's hideMascot wins over the hero and the brand mark", () => {
      register("conversation.empty.hero", () => <div data-testid="ext-hero" />);
      register("conversation.empty.brand-mark", () => (
        <div data-testid="ext-mark" />
      ));
      const { container } = renderBody({
        ...welcome,
        emptyStateOverride: { hideMascot: true },
      });
      expect(screen.queryByTestId("ext-hero")).toBeNull();
      expect(screen.queryByTestId("ext-mark")).toBeNull();
      expect(mascot(container)).toBeNull();
      expect(screen.getByText(defaultTitle)).toBeTruthy();
    });

    it("conversation.empty.brand-mark replaces only the mascot image", () => {
      register("conversation.empty.brand-mark", () => (
        <div data-testid="ext-mark" />
      ));
      const { container } = renderBody(welcome);
      expect(screen.getByTestId("ext-mark")).toBeTruthy();
      expect(mascot(container)).toBeNull();
      expect(screen.getByText(defaultTitle)).toBeTruthy();
    });

    it("conversation.empty.hero can keep the stock hero through renderDefault", () => {
      register("conversation.empty.hero", ({ renderDefault }) => (
        <div data-testid="ext-hero">
          {(renderDefault as () => ReactNode)()}
          <span>below the hero</span>
        </div>
      ));
      const { container } = renderBody(welcome);
      const hero = screen.getByTestId("ext-hero");
      // The stock mascot and title are still there, inside the plugin's tree.
      expect(hero.contains(mascot(container))).toBe(true);
      expect(hero.textContent).toMatch(defaultTitle);
      expect(screen.getByText("below the hero")).toBeTruthy();
    });

    it("conversation.empty.brand-mark can frame the stock mascot through renderDefault", () => {
      register("conversation.empty.brand-mark", ({ renderDefault }) => (
        <div data-testid="ext-mark">{(renderDefault as () => ReactNode)()}</div>
      ));
      const { container } = renderBody(welcome);
      expect(screen.getByTestId("ext-mark").contains(mascot(container))).toBe(true);
    });

    it("conversation.empty.extra renders below the suggestions with context", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.empty.extra", (props) => {
        ctx = props;
        return <div data-testid="ext-extra" />;
      });
      const setDraft = vi.fn();
      renderBody({ ...welcome, setDraft });
      const column = screen.getByTestId("ext-extra").parentElement!;
      expect(column.previousElementSibling?.textContent).toContain(
        "帮我梳理这个需求",
      );
      expect(ctx).toMatchObject({
        projectId: "proj-1",
        agentSlug: "valurion",
        setDraft,
      });
    });

    it("renders none of them on an existing conversation", () => {
      register("conversation.empty.hero", () => <div data-testid="ext-hero" />);
      register("conversation.empty.extra", () => (
        <div data-testid="ext-extra" />
      ));
      renderBody({ id: "s1", effectiveTurns: [] });
      expect(screen.queryByTestId("ext-hero")).toBeNull();
      expect(screen.queryByTestId("ext-extra")).toBeNull();
    });
  });

  describe("row slots", () => {
    it("conversation.user-message.actions gets the turn and sessionId", () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.user-message.actions", (props) => {
        seen.push(props);
        return <i data-testid="ext-user-action" />;
      });
      renderBody({ effectiveTurns: [turn(0)] });
      expect(screen.getByTestId("ext-user-action")).toBeTruthy();
      expect(seen[0]).toMatchObject({
        turn: { id: "turn-0" },
        sessionId: "s1",
      });
    });

    it("conversation.turn.tail gets isLatest / inFlight per row", () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.turn.tail", (props) => {
        seen.push(props);
        return <i data-testid={`ext-tail-${(props.turn as Turn).id}`} />;
      });
      renderBody({
        effectiveTurns: [turn(0), turn(1)],
        displayBusy: true,
      });
      expect(screen.getByTestId("ext-tail-turn-0")).toBeTruthy();
      expect(screen.getByTestId("ext-tail-turn-1")).toBeTruthy();
      expect(seen.find((c) => (c.turn as Turn).id === "turn-0")).toMatchObject({
        sessionId: "s1",
        isLatest: false,
        inFlight: false,
      });
      expect(seen.find((c) => (c.turn as Turn).id === "turn-1")).toMatchObject({
        sessionId: "s1",
        isLatest: true,
        inFlight: true,
      });
    });

    it("adds nothing to the transcript while both slots are empty", () => {
      const { container } = renderBody({ effectiveTurns: [turn(0)] });
      expect(container.querySelector("[data-testid^='ext-']")).toBeNull();
    });
  });

  describe("conversation.plan.actions", () => {
    const planTurns = [turn(0, true), turn(1)];

    it("draws no footer on a plan that is not actionable while the slot is empty", () => {
      renderBody({ effectiveTurns: planTurns, selectedSessionMode: "default" });
      expect(screen.queryByText(HINT)).toBeNull();
    });

    it("draws the footer for a registered contribution, with turn, sessionId and sessionMode", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.plan.actions", (props) => {
        ctx = props;
        return <i data-testid="ext-plan" />;
      });
      renderBody({ effectiveTurns: planTurns, selectedSessionMode: "default" });
      expect(screen.getByText(HINT)).toBeTruthy();
      expect(screen.getByTestId("ext-plan")).toBeTruthy();
      expect(ctx).toMatchObject({
        turn: { id: "turn-0" },
        sessionId: "s1",
        sessionMode: "default",
      });
    });

    it("sits next to the approve button, ahead of it, when the plan is actionable", () => {
      register("conversation.plan.actions", () => <i data-testid="ext-plan" />);
      renderBody({
        effectiveTurns: [turn(0, true)],
        selectedSessionMode: "plan",
        performSend: async () => undefined,
      });
      const approve = screen.getByRole("button", { name: /批准并开始执行/ });
      expect(screen.getByTestId("ext-plan").nextElementSibling).toBe(approve);
    });

    it("keeps the stock footer (approve only) when the slot is empty", () => {
      renderBody({
        effectiveTurns: [turn(0, true)],
        selectedSessionMode: "plan",
        performSend: async () => undefined,
      });
      const approve = screen.getByRole("button", { name: /批准并开始执行/ });
      expect(approve.parentElement!.children).toHaveLength(1);
    });

    it("goes to the last proposal only", () => {
      register("conversation.plan.actions", () => <i data-testid="ext-plan" />);
      renderBody({
        effectiveTurns: [turn(0, true), turn(1, true)],
        selectedSessionMode: "default",
      });
      expect(screen.getAllByTestId("ext-plan")).toHaveLength(1);
      expect(screen.getAllByText(HINT)).toHaveLength(1);
    });

    it("re-renders a memoized earlier plan row when the slot becomes occupied", () => {
      // The plan turn is not the latest row, so it only re-renders when
      // ``turnActionsKey`` changes — the slot's occupancy is folded into it.
      renderBody({ effectiveTurns: planTurns, selectedSessionMode: "default" });
      expect(screen.queryByText(HINT)).toBeNull();
      register("conversation.plan.actions", () => <i data-testid="ext-plan" />);
      expect(screen.getByText(HINT)).toBeTruthy();
      expect(screen.getByTestId("ext-plan")).toBeTruthy();
    });
  });
});
