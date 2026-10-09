import { useRegistryStore } from "@valuz/core";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ComposerPane } from "./ComposerPane";

/** The composer's four node props are what the pane's slot wiring produces —
 *  surface them as markers so each can be asserted present or absent. */
vi.mock("@valuz/ui", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    Composer: (props: {
      toolbarLeft?: ReactNode;
      toolbarRight?: ReactNode;
      plusMenuItems?: ReactNode;
      attachmentsExtra?: ReactNode;
      footerBar?: ReactNode;
    }) => (
      <div data-testid="composer">
        <div data-testid="left">{props.toolbarLeft}</div>
        <div data-testid="right">{props.toolbarRight}</div>
        <div data-testid="plus">{props.plusMenuItems}</div>
        <div data-testid="attachments">{props.attachmentsExtra}</div>
        <div data-testid="props-present">
          {[
            props.toolbarLeft && "left",
            props.toolbarRight && "right",
            props.plusMenuItems && "plus",
            props.attachmentsExtra && "attachments",
          ]
            .filter(Boolean)
            .join(",")}
        </div>
        {props.footerBar}
      </div>
    ),
  };
});
vi.mock("../../components/QueuedInputsBar", () => ({
  QueuedInputsBar: () => <div data-testid="queue" />,
}));
vi.mock("../../components/CreateAgentDialog", () => ({
  CreateAgentDialog: () => null,
}));
vi.mock("../../components/ExecutionLocationBar", () => ({
  ExecutionLocationBar: (props: { surface?: string }) => (
    <div data-testid="elb" data-surface={props.surface} />
  ),
}));

const noop = () => undefined;

function renderPane(over: Record<string, unknown> = {}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const props: any = {
    showScrollBottom: false,
    handleScrollToBottom: noop,
    displayBusy: false,
    selectedSession: { id: "s1" },
    rosterEmpty: false,
    channelLoaded: true,
    hasChannel: true,
    channelsPending: false,
    agentPending: false,
    setupPending: false,
    refreshChannels: noop,
    refreshAgents: noop,
    createAgentOpen: false,
    setCreateAgentOpen: noop,
    setAgentLibraryRevision: noop,
    setSelectedAgentSlug: noop,
    setComposerTouched: noop,
    selectedSessionId: "s1",
    queue: [],
    isBusy: false,
    queueDispatching: null,
    queuePaused: false,
    handleEditQueued: noop,
    handleDeleteQueued: noop,
    handleResumeQueue: noop,
    handleSteerQueued: noop,
    conversationInstanceKey: "k",
    draft: "hello",
    setDraft: noop,
    isProjectProject: true,
    effectiveAgentSlug: "valurion",
    handleSend: noop,
    interruptRef: { current: noop },
    sessionAttachments: [],
    handleRemoveSessionAttachment: noop,
    discardStagedAttachments: async () => undefined,
    composerAgents: [],
    sessionAgentSlug: "bound-agent",
    selectedAgentSlug: "picked-agent",
    execBarLocked: true,
    sessionExecOrigin: "local",
    execTargetId: null,
    setExecTargetId: noop,
    setSelectedProviderId: noop,
    setSelectedModelId: noop,
    projects: [],
    selectedProjectId: "p1",
    setSelectedProjectId: noop,
    setSelectedComposerSkill: noop,
    execBarProjects: [],
    providerTarget: null,
    panelSetCollapsed: noop,
    composerProviders: [],
    selectedProviderId: null,
    selectedModelId: null,
    composerRuntimes: [],
    selectedRuntimeId: null,
    setSelectedRuntimeId: noop,
    selectedPermissionMode: "default",
    setSelectedPermissionMode: noop,
    isNewSession: false,
    id: "s1",
    selectedEffort: null,
    setSelectedEffort: noop,
    selectedSessionMode: "default",
    setSelectedSessionMode: noop,
    selectedAgentSkillItems: [],
    composerMentionSkills: [],
    availableSkills: [],
    handleOpenKbPicker: noop,
    handleLocalFilesAttach: noop,
    connectorOptions: [],
    selectedMcpSlugs: [],
    toggleConnector: noop,
    performSend: noop,
    ...over,
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
  return render(
    <MemoryRouter>
      <ComposerPane {...props} />
    </MemoryRouter>,
  );
}

const register = (
  name: string,
  component: (props: Record<string, unknown>) => ReactNode,
) =>
  useRegistryStore
    .getState()
    .registerSlot(name, { id: `t-${name}`, component: component as never });

describe("ComposerPane slots", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  it("hands the composer none of the node props, and adds no dock, while every slot is empty", () => {
    const { container } = renderPane();
    expect(screen.getByTestId("props-present").textContent).toBe("");
    // The pane's relative wrapper holds exactly what it always did: the
    // queue's ``px-5`` row and the composer.
    const wrapper = container.querySelector(".relative")!;
    expect(
      Array.from(wrapper.children).map(
        (el) => el.getAttribute("data-testid") ?? el.className,
      ),
    ).toEqual(["px-5", "composer"]);
    expect(screen.getByTestId("elb").getAttribute("data-surface")).toBe(
      "conversation",
    );
  });

  it("passes only the node props whose slot is occupied", () => {
    register("conversation.composer.input.right", () => <i data-testid="x" />);
    register("conversation.composer.attachments", () => <i data-testid="y" />);
    renderPane();
    expect(screen.getByTestId("props-present").textContent).toBe(
      "right,attachments",
    );
  });

  it("gives the toolbar, plus-menu and attachment slots their documented context", () => {
    const seen: Record<string, Record<string, unknown>> = {};
    for (const name of [
      "conversation.composer.input.left",
      "conversation.composer.input.right",
      "conversation.composer.plus.menu-items",
      "conversation.composer.attachments",
    ]) {
      register(name, (props) => {
        seen[name] = props;
        return null;
      });
    }
    const setDraft = vi.fn();
    renderPane({ setDraft });

    const base = { sessionId: "s1", projectId: "p1", surface: "conversation" };
    expect(seen["conversation.composer.input.left"]).toMatchObject({
      ...base,
      draft: "hello",
      setDraft,
    });
    expect(seen["conversation.composer.input.right"]).toMatchObject({
      ...base,
      draft: "hello",
      setDraft,
    });
    expect(seen["conversation.composer.plus.menu-items"]).toMatchObject({
      ...base,
      setDraft,
    });
    expect(seen["conversation.composer.attachments"]).toMatchObject(base);
  });

  it("reports a temporary chat as projectId null", () => {
    let ctx: Record<string, unknown> = {};
    register("conversation.composer.input.left", (props) => {
      ctx = props;
      return null;
    });
    renderPane({ isProjectProject: false, selectedProjectId: "chat-default" });
    expect(ctx.projectId).toBeNull();
  });

  describe("conversation.composer.dock", () => {
    it("renders in an inset centered column between the queue and the composer, with context", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.composer.dock", (props) => {
        ctx = props;
        return <div data-testid="ext-dock" />;
      });
      const setDraft = vi.fn();
      renderPane({ displayBusy: true, setDraft });

      const column = screen.getByTestId("ext-dock").parentElement!;
      expect(column.className).toBe("mx-auto max-w-[760px]");
      const row = column.parentElement!;
      expect(row.className).toBe("px-5");
      expect(row.previousElementSibling?.className).toBe("px-5");
      expect(
        row.previousElementSibling?.contains(screen.getByTestId("queue")),
      ).toBe(true);
      expect(row.nextElementSibling).toBe(screen.getByTestId("composer"));
      expect(ctx).toMatchObject({
        sessionId: "s1",
        projectId: "p1",
        // An existing session is frozen to the agent it was created with.
        agentSlug: "bound-agent",
        draft: "hello",
        setDraft,
        busy: true,
      });
    });

    it("uses the picker's agent on a draft with no session", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.composer.dock", (props) => {
        ctx = props;
        return null;
      });
      renderPane({ selectedSession: null, selectedSessionId: null });
      expect(ctx.agentSlug).toBe("picked-agent");
      expect(ctx.sessionId).toBeNull();
    });
  });

  describe("conversation.composer.overlay", () => {
    it("renders beside the scroll-to-bottom button inside the relative wrapper", () => {
      let ctx: Record<string, unknown> = {};
      register("conversation.composer.overlay", (props) => {
        ctx = props;
        return <div data-testid="ext-overlay" />;
      });
      const setDraft = vi.fn();
      const { container } = renderPane({ showScrollBottom: true, setDraft });

      const overlay = screen.getByTestId("ext-overlay");
      expect(overlay.parentElement).toBe(container.querySelector(".relative"));
      expect(overlay.previousElementSibling?.tagName).toBe("BUTTON");
      expect(ctx).toMatchObject({ sessionId: "s1", draft: "hello", setDraft });
    });
  });
});
