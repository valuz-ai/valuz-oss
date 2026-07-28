/** @vitest-environment jsdom */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import type { ChatProjectBinding, Task } from "@valuz/core";
import { channelsApi, useSessionStore } from "@valuz/core";
import type { SessionListItem } from "@valuz/shared";

// ── Shared, hoisted test state the module mocks read from ────────────────────
const h = vi.hoisted(() => ({
  currentId: "A",
  currentSearch: "",
  tasksByProject: new Map<string, unknown[]>(),
  sessions: [] as unknown[],
  members: [] as unknown[],
  chatBindings: [] as ChatProjectBinding[],
  rightPanel: null as unknown,
  setRightPanel: vi.fn(),
  setHeader: vi.fn(),
  setMainClassName: vi.fn(),
  setContentInnerClassName: vi.fn(),
  kbTree: [] as never[],
  kbBindings: [] as never[],
  handleToggleBinding: vi.fn(),
  handleExpandKbFolder: vi.fn(),
  handleSetAddedKbs: vi.fn(),
  handleRemoveKb: vi.fn(),
  handleSelectAllInKb: vi.fn(),
  stagedAttachments: [] as never[],
  attachLocalFiles: vi.fn(),
  removeAttachment: vi.fn(),
  markPendingConsumed: vi.fn(),
  platform: {
    deleteFile: vi.fn(),
    revealInFinder: vi.fn(),
    isElectron: false,
    isMac: false,
  },
}));

const navigate = vi.fn();

vi.mock("react-router-dom", async (orig) => {
  const actual = await orig<typeof import("react-router-dom")>();
  return {
    ...actual,
    useParams: () => ({ id: h.currentId }),
    useNavigate: () => navigate,
    useLocation: () => ({
      pathname: `/projects/${h.currentId}`,
      search: h.currentSearch,
      hash: "",
      state: null,
      key: h.currentId,
    }),
  };
});

// Layout outlet — capture the right-panel setters as no-ops (the context panel
// is never actually rendered, which keeps the test light).
vi.mock("@valuz/app/layout", () => ({
  useProjectOutlet: () => ({
    setRightPanel: h.setRightPanel,
    setHeader: h.setHeader,
    setMainClassName: h.setMainClassName,
    setContentInnerClassName: h.setContentInnerClassName,
  }),
}));
vi.mock("@valuz/app/platform", () => ({
  usePlatform: () => h.platform,
}));
vi.mock("@valuz/app/hooks", () => ({
  useProjectKbBindings: () => ({
    kbTree: h.kbTree,
    bindings: h.kbBindings,
    handleToggleBinding: h.handleToggleBinding,
    handleExpandKbFolder: h.handleExpandKbFolder,
    handleSetAddedKbs: h.handleSetAddedKbs,
    handleRemoveKb: h.handleRemoveKb,
    handleSelectAllInKb: h.handleSelectAllInKb,
  }),
  useKbDocTree: () => ({ kbTree: [], loading: false, expandFolder: vi.fn() }),
}));
vi.mock("@valuz/app/components", () => ({
  ActivityFeedList: () => {
    const sessions = h.sessions
      .filter((s) => (s as SessionListItem).project_id === h.currentId)
      .map((s) => {
        const row = s as SessionListItem;
        return {
          id: row.id,
          title: row.name ?? row.last_user_message_text ?? row.id,
          kind: "chat",
          status: row.status,
          sortAt: row.updated_at,
        };
      });
    const tasks = (h.tasksByProject.get(h.currentId) ?? []).map((t) => {
      const row = t as Task;
      return {
        id: row.id,
        title: row.title,
        kind: "task",
        status: row.status,
        sortAt: row.updated_at,
      };
    });
    return (
      <div>
        {[...sessions, ...tasks]
          .sort((a, b) => b.sortAt - a.sortAt)
          .map((item) => (
            <div
              key={`${item.kind}-${item.id}`}
              data-anchor-key={`${item.kind}-${item.id}`}
            >
              {item.title} {item.status}
            </div>
          ))}
      </div>
    );
  },
  BindChatDialog: (props: { onBound: () => void | Promise<void> }) => (
    <button
      type="button"
      data-testid="refresh-chat-bindings"
      onClick={() => void props.onBound()}
    />
  ),
  CreateAutomationDialog: () => null,
  DeployAgentsDialog: () => null,
  RenameInput: () => null,
  RowActionsMenu: () => null,
  formatCreatedAt: (ms: number) => String(ms),
}));
vi.mock("../lib/agent-skill-items", () => ({ resolveAgentSkillItems: () => [] }));
vi.mock("../lib/file-tree", () => ({ toFileTree: () => [] }));
vi.mock("../components/AttachmentParsingDialog", () => ({
  AttachmentParsingDialog: () => null,
}));

// Stub the heavy Composer; keep every other @valuz/ui primitive real.
vi.mock("@valuz/ui", async (orig) => {
  const actual = await orig<typeof import("@valuz/ui")>();
  return {
    ...actual,
    Composer: (props: { selectedAgentSlug?: string | null }) => (
      <div data-testid="composer" data-agent={props.selectedAgentSlug ?? ""} />
    ),
  };
});

// Core source modules shared by both the page (via the barrel) and the real
// hooks (via relative imports). Mocking them here makes the page deterministic
// and keeps the auto-refresh poller pointed at controllable data.
vi.mock("../../../core/src/api/tasks-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    tasksApi: {
      ...(actual as { tasksApi: object }).tasksApi,
      listTasks: vi.fn(async (pid: string) => ({
        tasks: h.tasksByProject.get(pid) ?? [],
      })),
      kickoff: vi.fn(),
    },
  };
});
vi.mock("../../../core/src/api/sessions-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    sessionsApi: {
      ...(actual as { sessionsApi: object }).sessionsApi,
      list: vi.fn(async (pid?: string) => ({
        sessions: h.sessions.filter(
          (s) => (s as SessionListItem).project_id === pid,
        ),
      })),
    },
  };
});

vi.mock("../../../core/src/api/projects-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    projectsApi: {
      ...(actual as { projectsApi: object }).projectsApi,
      get: vi.fn(async () => ({ id: h.currentId, name: "Proj", instructions_md: "" })),
      listFiles: vi.fn(async () => ({ files: [] })),
      getMcpServers: vi.fn(async () => ({ slugs: [] })),
    },
  };
});
vi.mock("../../../core/src/api/providers-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    providersApi: {
      ...(actual as { providersApi: object }).providersApi,
      list: vi.fn(async () => ({ providers: [] })),
    },
  };
});
vi.mock("../../../core/src/api/automations-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    automationsApi: {
      ...(actual as { automationsApi: object }).automationsApi,
      listGroups: vi.fn(async () => ({ groups: [] })),
    },
  };
});
vi.mock("../../../core/src/api/connectors-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    connectorsApi: {
      ...(actual as { connectorsApi: object }).connectorsApi,
      list: vi.fn(async () => ({ connectors: [] })),
    },
  };
});
vi.mock("../../../core/src/api/agents-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    agentsApi: {
      ...(actual as { agentsApi: object }).agentsApi,
      listMembers: vi.fn(async () => ({ agents: h.members })),
      listAgents: vi.fn(async () => ({ agents: [] })),
    },
  };
});
vi.mock("../../../core/src/api/channels-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    channelsApi: {
      ...(actual as { channelsApi: object }).channelsApi,
      listChatBindings: vi.fn(async () => h.chatBindings),
      deleteFeishuChat: vi.fn(async (externalChatId: string) => {
        h.chatBindings = h.chatBindings.filter(
          (chat) => chat.external_chat_id !== externalChatId,
        );
      }),
      unbindChat: vi.fn(async () => undefined),
      feishuChatLink: vi.fn(async () => null),
    },
  };
});
vi.mock("../../../core/src/api/skills-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    skillsApi: {
      ...(actual as { skillsApi: object }).skillsApi,
      list: vi.fn(async () => ({ skills: [] })),
    },
  };
});
vi.mock("../../../core/src/api/memory-api", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    memoryApi: {
      ...(actual as { memoryApi: object }).memoryApi,
      getMemory: vi.fn(async () => ({ entries: { project: [] } })),
    },
  };
});
vi.mock("../../../core/src/hooks/use-model-defaults", () => ({
  useModelDefaults: () => ({ defaults: null, loading: false }),
}));
vi.mock("../../../core/src/hooks/use-project-last-used", () => ({
  useProjectLastUsed: () => ({ pick: null, loading: false }),
}));
vi.mock("../../../core/src/hooks/use-session-attachments", () => ({
  useSessionAttachments: () => ({
    attachments: h.stagedAttachments,
    hasParsing: false,
    attachLocalFiles: h.attachLocalFiles,
    remove: h.removeAttachment,
    markPendingConsumed: h.markPendingConsumed,
  }),
}));
vi.mock("../../../core/src/hooks/use-activity-feed", () => ({
  useActivityFeed: () => ({
    items: [],
    loading: false,
    loadingMore: false,
    hasMore: false,
    loadMore: vi.fn(),
    refresh: vi.fn(),
  }),
}));
vi.mock("../../../core/src/hooks/use-runtimes", () => ({
  useRuntimes: () => ({ runtimes: [] }),
}));

import { ProjectDetailPage } from "./ProjectDetailPage";

const task = (over: Partial<Task>): Task => ({
  id: "t1",
  project_id: "A",
  title: "Alpha",
  goal: "g",
  status: "active",
  created_by: "u1",
  lead_agent_slug: "lead",
  current_holder: "lead",
  file_path: "/p",
  created_at: 10,
  updated_at: 10,
  ...over,
});

const session = (over: Partial<SessionListItem>): SessionListItem => ({
  id: "s1",
  project_id: "A",
  name: "Chat One",
  status: "idle",
  origin: "user",
  last_user_message_text: null,
  locked_model_id: null,
  locked_provider_id: null,
  runtime_provider: "claude_agent",
  permission_mode: "full_access",
  effort: null,
  task_id: null,
  updated_at: 5,
  ...over,
});

function renderPage(entry = `/projects/${h.currentId}`) {
  h.currentSearch = entry.includes("?") ? entry.slice(entry.indexOf("?")) : "";
  // Wrap in an explicit scroll container so the anchor hook's
  // ``findScrollParent`` resolves to a real scroller (plan review P2 wiring).
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <div style={{ overflowY: "auto" }}>
        <ProjectDetailPage />
      </div>
    </MemoryRouter>,
  );
}

const anchorKeys = (root: HTMLElement, prefix: string): string[] =>
  Array.from(root.querySelectorAll("[data-anchor-key]"))
    .map((el) => el.getAttribute("data-anchor-key") ?? "")
    .filter((k) => k.startsWith(prefix));

type RightPanelProps = {
  chatBindings: Array<{ id: string; name: string }>;
  onDeleteChat: (chatId: string) => void;
};

const rightPanelProps = (): RightPanelProps | null =>
  h.rightPanel &&
  typeof h.rightPanel === "object" &&
  "props" in h.rightPanel
    ? (h.rightPanel as { props: RightPanelProps }).props
    : null;

describe("ProjectDetailPage auto-refresh wiring", () => {
  beforeEach(() => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });
    h.currentId = "A";
    h.currentSearch = "";
    h.tasksByProject = new Map([["A", [task({ id: "t1", title: "Alpha" })]]]);
    h.sessions = [session({ id: "s1", project_id: "A" })];
    h.members = [];
    h.chatBindings = [
      {
        channel_instance_id: "channel-1",
        external_chat_id: "chat-1",
        external_chat_name: "Alpha group",
        platform: "feishu_bot",
        project_id: "A",
        created_by_valuz: false,
        needs_join: false,
      },
    ];
    h.rightPanel = null;
    h.setRightPanel.mockReset();
    h.setRightPanel.mockImplementation((panel: unknown) => {
      h.rightPanel = panel;
    });
    useSessionStore.setState({
      sessions: [session({ id: "s1", project_id: "A" })],
    });
    navigate.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders rows with data-anchor-key on all three tabs", async () => {
    const { container, getByRole } = renderPage();
    await waitFor(() =>
      expect(container.querySelector('[data-anchor-key="task-t1"]')).toBeTruthy(),
    );
    // All tab: both the session and the task carry anchor keys.
    expect(anchorKeys(container, "task-")).toContain("task-t1");
    expect(anchorKeys(container, "chat-")).toContain("chat-s1");

    // Chat tab.
    fireEvent.click(getByRole("tab", { name: /chat|对话/i }));
    await waitFor(() =>
      expect(container.querySelector('[data-anchor-key="chat-s1"]')).toBeTruthy(),
    );

    // Tasks tab.
    fireEvent.click(getByRole("tab", { name: /task|任务/i }));
    await waitFor(() =>
      expect(container.querySelector('[data-anchor-key="task-t1"]')).toBeTruthy(),
    );
  });

  it("refreshes the right panel when the add-group dialog changes bindings", async () => {
    renderPage();
    await waitFor(() =>
      expect(rightPanelProps()?.chatBindings.map((chat) => chat.id)).toEqual([
        "chat-1",
      ]),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    h.chatBindings = [
      ...h.chatBindings,
      {
        channel_instance_id: "channel-2",
        external_chat_id: "chat-2",
        external_chat_name: "Beta group",
        platform: "feishu_bot",
        project_id: "A",
        created_by_valuz: true,
        needs_join: true,
      },
    ];
    h.setRightPanel.mockClear();
    vi.mocked(channelsApi.listChatBindings).mockClear();

    fireEvent.click(screen.getByTestId("refresh-chat-bindings"));

    await waitFor(() =>
      expect(channelsApi.listChatBindings).toHaveBeenCalledWith("A"),
    );
    await waitFor(() =>
      expect(rightPanelProps()?.chatBindings.map((chat) => chat.id)).toEqual([
        "chat-1",
        "chat-2",
      ]),
    );
  });

  it("removes a deleted group from the right panel", async () => {
    renderPage();
    await waitFor(() =>
      expect(rightPanelProps()?.chatBindings.map((chat) => chat.id)).toEqual([
        "chat-1",
      ]),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    h.setRightPanel.mockClear();

    await act(async () => {
      rightPanelProps()?.onDeleteChat("chat-1");
    });
    fireEvent.click(screen.getByRole("button", { name: /delete|删除/i }));

    await waitFor(() =>
      expect(channelsApi.deleteFeishuChat).toHaveBeenCalledWith("chat-1"),
    );
    await waitFor(() => expect(h.setRightPanel).toHaveBeenCalled());
    await waitFor(() =>
      expect(rightPanelProps()?.chatBindings).toEqual([]),
    );
  });

  it("defaults the composer to the requested Lead agent from team activation", async () => {
    h.members = [
      {
        member: {
          id: "pm-lead",
          project_id: "A",
          agent_slug: "lead-agent",
          source_agent_slug: "lead-agent",
        },
        agent: {
          id: "a-lead",
          name: "Lead Agent",
          model: "claude-sonnet-4",
          runtime_provider: "claude_agent",
          instructions: "",
          skills: [],
          connectors: [],
          provider_id: null,
          effort: null,
        },
      },
      {
        member: {
          id: "pm-member",
          project_id: "A",
          agent_slug: "member-agent",
          source_agent_slug: "member-agent",
        },
        agent: {
          id: "a-member",
          name: "Member Agent",
          model: "claude-sonnet-4",
          runtime_provider: "claude_agent",
          instructions: "",
          skills: [],
          connectors: [],
          provider_id: null,
          effort: null,
        },
      },
    ];

    renderPage("/projects/A?agent=lead-agent");

    await waitFor(() =>
      expect(screen.getByTestId("composer").getAttribute("data-agent")).toBe(
        "lead-agent",
      ),
    );
  });

  it("auto-refresh adds a newly-appearing task without duplicating existing rows", async () => {
    const { container, rerender } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Alpha"));

    // A new task appears elsewhere; the poller's online catch-up pulls it in.
    h.tasksByProject.set("A", [
      task({ id: "t1", title: "Alpha" }),
      task({ id: "t2", title: "Beta", created_at: 20, updated_at: 20 }),
    ]);
    fireEvent(window, new Event("online"));
    rerender(
      <MemoryRouter initialEntries={[`/projects/${h.currentId}`]}>
        <div style={{ overflowY: "auto" }}>
          <ProjectDetailPage />
        </div>
      </MemoryRouter>,
    );

    await waitFor(() => expect(container.textContent).toContain("Beta"));
    // No duplicate rows for the pre-existing task.
    expect(
      container.querySelectorAll('[data-anchor-key="task-t1"]').length,
    ).toBe(1);
  });

  it("reflects a task status change in place and re-sorts the all tab (no dup)", async () => {
    const { container, rerender } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Alpha"));

    // Status flips running→completed and updated_at bumps → all-tab reorders.
    h.tasksByProject.set("A", [
      task({ id: "t1", title: "Alpha", status: "completed", updated_at: 99 }),
    ]);
    fireEvent(window, new Event("online"));
    rerender(
      <MemoryRouter initialEntries={[`/projects/${h.currentId}`]}>
        <div style={{ overflowY: "auto" }}>
          <ProjectDetailPage />
        </div>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(container.textContent?.toLowerCase()).toContain("complete"),
    );
    expect(
      container.querySelectorAll('[data-anchor-key="task-t1"]').length,
    ).toBe(1);
  });

  it("discards project A's data after switching to project B (A→B late return)", async () => {
    const { container, rerender } = renderPage();
    await waitFor(() => expect(container.textContent).toContain("Alpha"));

    // Switch to project B with its own task; A's rows must not survive.
    h.currentId = "B";
    h.tasksByProject.set("B", [
      task({ id: "tb", project_id: "B", title: "BravoTask" }),
    ]);
    h.sessions = [];
    rerender(
      <MemoryRouter initialEntries={[`/projects/${h.currentId}`]}>
        <div style={{ overflowY: "auto" }}>
          <ProjectDetailPage />
        </div>
      </MemoryRouter>,
    );

    await waitFor(() => expect(container.textContent).toContain("BravoTask"));
    expect(container.querySelector('[data-anchor-key="task-t1"]')).toBeNull();
    expect(container.textContent).not.toContain("Alpha");
  });
});
