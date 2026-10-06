import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";

const actions = vi.hoisted(() => ({
  operationStates: {} as Record<string, unknown>,
  operationBusy: {} as Record<string, unknown>,
  handleConfirmOperation: vi.fn(),
  handleCancelOperation: vi.fn(),
}));

// The confirm / cancel state machine has its own tests; this file pins what
// ``renderToolCall`` draws for the ``extension_manager`` tool.
vi.mock("./useToolCallCardActions", () => ({
  useToolCallCardActions: () => ({
    submissionStates: {},
    proposalStates: {},
    automationProposalStates: {},
    operationStates: actions.operationStates,
    operationBusy: actions.operationBusy,
    handleConfirmSubmission: vi.fn(),
    handleDismissSubmission: vi.fn(),
    handleConfirmProposal: vi.fn(),
    handleDismissProposal: vi.fn(),
    handleConfirmAutomation: vi.fn(),
    handleDismissAutomation: vi.fn(),
    handleConfirmOperation: actions.handleConfirmOperation,
    handleCancelOperation: actions.handleCancelOperation,
    handleRequestChangesOperation: vi.fn(),
  }),
}));

import { useToolCallCards } from "./useToolCallCards";

type Tool = Parameters<ReturnType<typeof useToolCallCards>["renderToolCall"]>[0];

const Harness = ({ tool }: { tool: Tool }) => {
  const { renderToolCall } = useToolCallCards({
    events: [],
    turns: [],
    isBusy: false,
    selectedSessionId: "s1",
    selectedSessionIdRef: { current: "s1" },
    selectedSessionName: null,
    planAnchors: { taskByRichTool: new Map() },
    workflowStates: new Map(),
    askUserQuestionLocalAnswers: {},
    askUserQuestionSubmitRef: { current: () => undefined },
  } as unknown as Parameters<typeof useToolCallCards>[0]);
  return <div data-testid="out">{renderToolCall(tool)}</div>;
};

const view = (tool: Tool) =>
  render(
    <MemoryRouter>
      <Harness tool={tool} />
    </MemoryRouter>,
  );

const operationRecord = {
  id: "op-1",
  operation_type: "extension.install",
  state: "awaiting_confirmation",
  proposal_hash: "h1",
  preview: {
    id: "acme.dashboard",
    version: "1.2.0",
    publisher: "Acme",
    sha256: "deadbeef",
    permissions: ["projects:read", "storage"],
    added_permissions: ["storage"],
  },
  input_payload: {},
  result_payload: {},
  error_message: null,
};

const envelope = (extra: Record<string, unknown>) =>
  JSON.stringify({ ok: true, action: "install", message: "proposed", ...extra });

beforeAll(() => {
  initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" });
});
beforeEach(() => {
  actions.operationStates = {};
  actions.operationBusy = {};
  actions.handleConfirmOperation.mockReset();
  actions.handleCancelOperation.mockReset();
});
afterEach(cleanup);

describe("renderToolCall — extension_manager", () => {
  it("renders the confirmation card for an operation proposal, under any runtime namespacing", async () => {
    for (const title of [
      "extension_manager",
      "mcp__harness__extension_manager",
      "harness/extension_manager",
    ]) {
      const { container, unmount } = view({
        id: "t1",
        title,
        input: JSON.stringify({ action: "install" }),
        output: envelope({ operation: operationRecord }),
        status: "success",
      });
      const card = container.querySelector(
        '[data-slot="extension-operation-card"]',
      );
      expect(card, title).not.toBeNull();
      expect(screen.getByText("acme.dashboard")).toBeTruthy();
      expect(screen.getByText("deadbeef")).toBeTruthy();
      expect(container.querySelector('[data-added="true"]')?.textContent).toContain(
        "storage",
      );
      unmount();
    }
  });

  it("confirms and cancels through the operation handlers with the live record", async () => {
    // A state the user already moved the record to wins over the snapshot.
    const live = { ...operationRecord, state: "awaiting_confirmation", id: "op-1" };
    actions.operationStates = { "op-1": live };
    view({
      id: "t1",
      title: "extension_manager",
      output: envelope({ operation: operationRecord }),
      status: "success",
    });
    await userEvent.click(screen.getByRole("button", { name: "安装" }));
    expect(actions.handleConfirmOperation).toHaveBeenCalledWith(live);
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(actions.handleCancelOperation).toHaveBeenCalledWith(live);
  });

  it("reads a settled state from the tracked record", () => {
    actions.operationStates = {
      "op-1": { ...operationRecord, state: "succeeded" },
    };
    view({
      id: "t1",
      title: "extension_manager",
      output: envelope({ operation: operationRecord }),
      status: "success",
    });
    expect(screen.getByText("插件操作已完成")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("leaves the read-only actions to the generic tool card", () => {
    const { container } = view({
      id: "t2",
      title: "extension_manager",
      output: envelope({ action: "list", plugins: [] }),
      status: "success",
    });
    expect(container.querySelector('[data-slot="extension-operation-card"]')).toBeNull();
    expect(screen.getByTestId("out").textContent).toBe("");
  });

  it("draws nothing while the tool is still running", () => {
    const { container } = view({
      id: "t3",
      title: "extension_manager",
      status: "running",
    });
    expect(container.querySelector('[data-slot="extension-operation-card"]')).toBeNull();
  });
});
