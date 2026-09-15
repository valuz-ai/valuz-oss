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
import type { AgentDeployment, ProjectListItem } from "@valuz/core";

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
const listDeployments = vi.fn(
  async (..._args: unknown[]) => ({ deployments: [] as AgentDeployment[] }),
);
const projectsList = vi.fn(
  async (..._args: unknown[]) => ({ projects: [] as ProjectListItem[] }),
);

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
      listDeployments: (...args: unknown[]) => listDeployments(...args),
      getEffectiveResources: async () => null,
    },
    projectsApi: { list: (...args: unknown[]) => projectsList(...args) },
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
  Link: ({
    to,
    className,
    children,
  }: {
    to: string;
    className?: string;
    children?: React.ReactNode;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
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
    listDeployments.mockResolvedValue({ deployments: [] });
    projectsList.mockResolvedValue({ projects: [] });
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

describe("AgentDetailView — joined projects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgent.mockImplementation(async () => ({ ...agentFixture }));
    updateAgent.mockImplementation(async () => ({ ...agentFixture }));
    listDeployments.mockResolvedValue({ deployments: [] });
    projectsList.mockResolvedValue({ projects: [] });
  });

  it("counts the projects in the header and lists them by name in their own tab", async () => {
    // Two members in the same project collapse into one row; the chat
    // project (a per-conversation container) is not a project the user
    // manages, so it is neither counted nor listed; nothing shows a raw id.
    listDeployments.mockResolvedValue({
      deployments: [
        {
          project_id: "p-alpha",
          agent_slug: "researcher",
          project_name: "Alpha 研究",
          project_kind: "project",
        },
        {
          project_id: "p-alpha",
          agent_slug: "researcher-2",
          project_name: "Alpha 研究",
          project_kind: "project",
        },
        {
          project_id: "c-chat",
          agent_slug: "researcher",
          project_name: "Chat",
          project_kind: "chat",
        },
      ],
    });

    render(<AgentDetailView slug="researcher" />);
    await waitFor(() => expect(listDeployments).toHaveBeenCalled());

    const count = await screen.findByRole("button", {
      name: "agent.deployedCount",
    });
    expect(screen.queryByText("agent.notDeployedYet")).toBeNull();

    await userEvent.click(count);
    const tab = screen.getByRole("tab", { name: "agent.tabProjects" });
    expect(tab.getAttribute("data-state")).toBe("active");
    expect(screen.getByText("Alpha 研究")).toBeTruthy();
    expect(screen.getByText("agent.projectMembers")).toBeTruthy();
    expect(screen.queryByText("Chat")).toBeNull();
    expect(screen.queryByText("p-alpha")).toBeNull();
    expect(screen.queryByText("c-chat")).toBeNull();
  });

  it("falls back to the local project list, then a placeholder, never the id", async () => {
    listDeployments.mockResolvedValue({
      deployments: [
        { project_id: "p-old-server", agent_slug: "researcher" },
        { project_id: "p-unknown", agent_slug: "researcher" },
      ],
    });
    projectsList.mockResolvedValue({
      projects: [
        {
          id: "p-old-server",
          name: "Legacy",
          kind: "project",
          root_path: "/tmp/legacy",
          icon: null,
          cwd: "/tmp/legacy",
        },
      ],
    });

    render(<AgentDetailView slug="researcher" />);
    await userEvent.click(
      await screen.findByRole("tab", { name: "agent.tabProjects" }),
    );
    expect(screen.getByText("Legacy")).toBeTruthy();
    // A row the local list does not know either is a chat container on an
    // older server: not listed, and its id never surfaces.
    expect(screen.queryByText("p-unknown")).toBeNull();
    expect(screen.queryByText("p-old-server")).toBeNull();
  });
});
