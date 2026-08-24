import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmAutomationProposalAndNotify,
  confirmOperationAndNotify,
} from "./useToolCallCardActions";

const api = vi.hoisted(() => ({
  confirmAutomation: vi.fn(),
  confirmOperation: vi.fn(),
  notifyResourceRefresh: vi.fn(),
}));

vi.mock("@valuz/core", () => ({
  agentsApi: {},
  automationsApi: {
    confirmProposal: api.confirmAutomation,
  },
  operationsApi: {
    confirm: api.confirmOperation,
  },
  parseOperationToolOutput: vi.fn(),
  skillsApi: {},
  notifyResourceRefresh: api.notifyResourceRefresh,
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("useToolCallCardActions resource refresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("publishes the created Automation after confirmation succeeds", async () => {
    api.confirmAutomation.mockResolvedValue({
      automation_id: "automation-1",
      project_id: "project-1",
    });
    await confirmAutomationProposalAndNotify("session-1", "tool-1", {
      name: "Morning review",
      prompt_template: "Review the market",
      trigger: { kind: "cron", cron_expr: "0 9 * * *", timezone: null },
    });

    expect(api.notifyResourceRefresh).toHaveBeenCalledWith({
      resourceType: "automation",
      projectId: "project-1",
      resourceId: "automation-1",
    });
  });

  it("publishes the changed Playbook after its Operation succeeds", async () => {
    api.confirmOperation.mockResolvedValue({
      id: "operation-1",
      project_id: "project-1",
      operation_type: "playbook.definition.create",
      state: "succeeded",
      preview: { change: "create" },
      canonical_result_refs: [],
    });
    await confirmOperationAndNotify("session-1", {
      id: "operation-1",
      project_id: "project-1",
      operation_type: "playbook.definition.create",
      state: "awaiting_confirmation",
      proposal_hash: "hash-1",
      preview: { change: "create" },
    } as never);

    expect(api.notifyResourceRefresh).toHaveBeenCalledWith({
      resourceType: "playbook",
      projectId: "project-1",
      resourceId: "operation-1",
    });
  });
});
