import { cleanup, render, screen, within } from "@testing-library/react";
import type { ActivityFeed, ActivityItem } from "@valuz/core";
import { afterEach, describe, expect, it, vi } from "vitest";

// Expose each chat row's Fork wiring without opening the dropdown.
vi.mock("./RowActionsMenu", () => ({
  RowActionsMenu: ({ onFork }: { onFork?: () => void }) => (
    <i data-testid="row-actions" data-fork={onFork ? "yes" : "no"} />
  ),
}));

import { ActivityFeedList } from "./ActivityFeedList";

const chat = (id: string, over: Partial<ActivityItem> = {}): ActivityItem => ({
  kind: "chat",
  id,
  title: id,
  status: "idle",
  is_automation: false,
  project_id: "p",
  project_name: null,
  linked_session_id: null,
  sort_at: Date.now(),
  ...over,
});

function forkOffered(items: ActivityItem[]): Record<string, boolean> {
  const feed = {
    items,
    loading: false,
    loadingMore: false,
    hasMore: false,
    loadMore: () => undefined,
  } as unknown as ActivityFeed;
  render(
    <ActivityFeedList
      feed={feed}
      onOpenSession={() => undefined}
      onOpenTask={() => undefined}
      onRenameConfirm={() => undefined}
      onDeleteSession={() => undefined}
      onForkSession={() => undefined}
      emptyLabel="empty"
    />,
  );
  return Object.fromEntries(
    items.map((item) => {
      const row = screen.getByText(item.title).closest("li")!;
      const menu = within(row).getByTestId("row-actions");
      return [item.id, menu.dataset.fork === "yes"];
    }),
  );
}

describe("ActivityFeedList Fork gate", () => {
  afterEach(() => cleanup());

  it("offers Fork only on runtimes with a wired native fork", () => {
    expect(
      forkOffered([
        chat("codex", { runtime: "codex" }),
        chat("claude", { runtime: "claude_agent" }),
        chat("deep", { runtime: "deepagents" }),
        chat("dsh", { runtime: "deepseek_harness" }),
        chat("unknown"),
      ]),
    ).toEqual({
      codex: true,
      claude: true,
      deep: true,
      dsh: false,
      unknown: false,
    });
  });

  it("hides Fork on running and automation-fired chats", () => {
    expect(
      forkOffered([
        chat("running", { runtime: "codex", status: "running" }),
        chat("auto", { runtime: "codex", is_automation: true }),
      ]),
    ).toEqual({ running: false, auto: false });
  });
});
