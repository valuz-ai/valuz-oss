import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { ComposerPane } from "./ComposerPane";

/** The pane under test only routes SEND between chat and task — stub the
 *  Composer down to the three knobs that routing touches. */
vi.mock("@valuz/ui", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    Composer: (props: {
      mode?: "chat" | "task";
      onModeChange?: (mode: "chat" | "task") => void;
      onSend: () => void;
    }) => (
      <div>
        <span data-testid="mode">{props.mode ?? "none"}</span>
        {props.onModeChange ? (
          <button type="button" onClick={() => props.onModeChange!("task")}>
            switch-to-task
          </button>
        ) : null}
        <button type="button" onClick={props.onSend}>
          send
        </button>
      </div>
    ),
  };
});
vi.mock("../../components/QueuedInputsBar", () => ({
  QueuedInputsBar: () => null,
}));
vi.mock("../../components/AttachmentParsingDialog", () => ({
  AttachmentParsingDialog: () => null,
}));
vi.mock("../../components/CreateAgentDialog", () => ({
  CreateAgentDialog: () => null,
}));
vi.mock("../../components/ExecutionLocationBar", () => ({
  ExecutionLocationBar: () => null,
}));

function Pane({
  onSendTask,
  handleSend,
  draft: initialDraft = "研究 NVDA 的财报",
}: {
  onSendTask?: (goal: string) => Promise<boolean> | boolean;
  handleSend?: () => void;
  draft?: string;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const noop = () => undefined;
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const base: any = {
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
    draft,
    setDraft,
    isProjectProject: true,
    effectiveAgentSlug: "valurion",
    handleSend: handleSend ?? noop,
    interruptRef: { current: noop },
    sessionAttachments: [],
    handleRemoveSessionAttachment: noop,
    composerAgents: [],
    sessionAgentSlug: null,
    selectedAgentSlug: "valurion",
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
    selectedAgentSkillItems: [],
    composerMentionSkills: [],
    availableSkills: [],
    handleOpenKbPicker: noop,
    handleLocalFilesAttach: noop,
    connectorOptions: [],
    selectedMcpSlugs: [],
    toggleConnector: noop,
    parsingConfirmOpen: false,
    setParsingConfirmOpen: noop,
    performSend: noop,
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */
  return (
    <MemoryRouter>
      <ComposerPane {...base} onSendTask={onSendTask} />
    </MemoryRouter>
  );
}

describe("ComposerPane task mode", () => {
  it("shows no mode toggle without onSendTask and sends via chat", () => {
    const handleSend = vi.fn();
    render(<Pane handleSend={handleSend} />);
    expect(screen.getByTestId("mode").textContent).toBe("none");
    expect(screen.queryByText("switch-to-task")).toBeNull();
    fireEvent.click(screen.getByText("send"));
    expect(handleSend).toHaveBeenCalledTimes(1);
  });

  it("routes task-mode sends to onSendTask and clears the draft on success", async () => {
    const handleSend = vi.fn();
    const onSendTask = vi.fn().mockResolvedValue(true);
    const { rerender } = render(
      <Pane handleSend={handleSend} onSendTask={onSendTask} />,
    );
    expect(screen.getByTestId("mode").textContent).toBe("chat");

    fireEvent.click(screen.getByText("switch-to-task"));
    expect(screen.getByTestId("mode").textContent).toBe("task");

    fireEvent.click(screen.getByText("send"));
    await waitFor(() =>
      expect(onSendTask).toHaveBeenCalledWith("研究 NVDA 的财报"),
    );
    expect(handleSend).not.toHaveBeenCalled();
    rerender(<Pane handleSend={handleSend} onSendTask={onSendTask} />);
  });

  it("keeps the draft when the task kickoff reports failure", async () => {
    const onSendTask = vi.fn().mockResolvedValue(false);
    render(<Pane onSendTask={onSendTask} />);
    fireEvent.click(screen.getByText("switch-to-task"));
    fireEvent.click(screen.getByText("send"));
    await waitFor(() => expect(onSendTask).toHaveBeenCalledTimes(1));
  });
});
