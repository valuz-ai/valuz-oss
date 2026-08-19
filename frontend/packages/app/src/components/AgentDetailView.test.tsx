/**
 * The agent detail view re-fetches the agent after ANY tab's save, so the
 * other tabs see fresh data. That re-fetch must not walk over an edit the
 * user is still typing.
 *
 * The regression this pins down: with the instructions tab half-written,
 * saving the inheritance switch (or any other tab) reloaded the agent and
 * reverted the textarea to the stored version — the user's text vanished
 * without them ever pressing Save.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const agentFixture = {
  id: "a1",
  slug: "researcher",
  name: "研究员",
  description: "desc",
  instructions: "原始指令",
  runtime: "claude_agent",
  model: "claude-opus-4-8",
  provider_id: null,
  effort: "high",
  avatar: null,
  skills: [],
  connector_types: [],
  knowledge_scope: [],
  kind: "standard",
  resource_policy: "explicit",
  inherit_global_instructions: true,
  permission_mode: "default",
};

const stableTranslation = { t: (key: string) => key };
const getAgent = vi.fn();
const updateAgent = vi.fn();

vi.mock("@valuz/core", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@valuz/core");
  return {
    ...actual,
    // Memoized exactly like the real hook: ``t`` is a dependency of the
    // view's ``loadData``, so an unstable identity would turn every render
    // into a re-fetch.
    useTranslation: () => stableTranslation,
    useResourceGuard: () => ({ canDelete: true }),
    agentsApi: {
      getAgent: (...args: unknown[]) => getAgent(...args),
      updateAgent: (...args: unknown[]) => updateAgent(...args),
      listDeployments: async () => ({ deployments: [] }),
      getEffectiveResources: async () => null,
    },
    projectsApi: { list: async () => ({ projects: [] }) },
    channelsApi: {
      getWeComAIBotBinding: async () => null,
      getFeishuBinding: async () => null,
    },
    skillsApi: { list: async () => ({ project_id: "", skills: [] }) },
    connectorsApi: {
      list: async () => ({ connectors: [] }),
      listDirectory: async () => ({ items: [] }),
    },
  };
});

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
  useOutletContext: () => null,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AgentDetailView } from "./AgentDetailView";

const openInstructionsTab = async () => {
  await userEvent.click(
    await screen.findByRole("tab", { name: "agent.tabInstructions" }),
  );
};

describe("AgentDetailView — instructions draft", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A fresh object per call, like a real JSON response — the view keys its
    // draft re-seed off the agent's identity, so a shared instance would hide
    // exactly the behaviour under test.
    getAgent.mockImplementation(async () => ({ ...agentFixture }));
    updateAgent.mockImplementation(async () => ({ ...agentFixture }));
  });

  it("keeps characters typed while a save is still in flight", async () => {
    // Every save re-fetches the agent so the other tabs see fresh data. The
    // reply used to be written straight back into every draft — so anything
    // typed between pressing Save and the reply landing was silently
    // reverted to the stored version.
    let releaseSave: () => void = () => {};
    updateAgent.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseSave = () => resolve({ ...agentFixture });
        }),
    );

    render(<AgentDetailView slug="researcher" />);
    await waitFor(() => expect(getAgent).toHaveBeenCalled());
    await openInstructionsTab();

    const textarea = (await screen.findByPlaceholderText(
      "agent.instructionsPlaceholder",
    )) as HTMLTextAreaElement;
    expect(textarea.value).toBe("原始指令");

    fireEvent.change(textarea, { target: { value: "原始指令 第一段" } });
    fireEvent.click(screen.getByRole("button", { name: "agent.save" }));
    await waitFor(() => expect(updateAgent).toHaveBeenCalled());

    // Still typing while the request is out.
    fireEvent.change(textarea, { target: { value: "原始指令 第一段 第二段" } });

    // The save lands, and the view re-fetches (server still has the old text
    // in this fixture — the point is that a re-fetch must not win).
    await act(async () => {
      releaseSave();
      await Promise.resolve();
    });
    await waitFor(() => expect(getAgent.mock.calls.length).toBeGreaterThan(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });

    expect(textarea.value).toBe("原始指令 第一段 第二段");
  });

  it("still refreshes a draft the user has not touched", async () => {
    // The other half of the contract: protecting edits must not freeze the
    // tab. An untouched draft still picks up whatever the re-fetch returns.
    render(<AgentDetailView slug="researcher" />);
    await waitFor(() => expect(getAgent).toHaveBeenCalled());
    await openInstructionsTab();

    const textarea = (await screen.findByPlaceholderText(
      "agent.instructionsPlaceholder",
    )) as HTMLTextAreaElement;
    expect(textarea.value).toBe("原始指令");

    // Someone changed the agent elsewhere; the inheritance switch saves
    // immediately, and that save re-fetches.
    getAgent.mockImplementation(async () => ({
      ...agentFixture,
      instructions: "别处改过的指令",
    }));
    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => expect(textarea.value).toBe("别处改过的指令"));
  });
});
