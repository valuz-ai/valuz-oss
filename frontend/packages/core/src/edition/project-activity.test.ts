import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import {
  resolveProjectActivity,
  setProjectActivitySource,
  type ProjectActivityPage,
} from "./project-activity";
import { useActivityFeed } from "../hooks/use-activity-feed";
import { activityApi, type ActivityItem } from "../api/activity-api";

function item(id: string, sortAt = 0): ActivityItem {
  return {
    kind: "chat",
    id,
    title: id,
    status: "completed",
    is_automation: false,
    project_id: "p1",
    project_name: null,
    sort_at: sortAt,
  } as ActivityItem;
}

afterEach(() => {
  setProjectActivitySource(null);
  vi.restoreAllMocks();
});

describe("project activity source", () => {
  it("resolves null with no source registered", () => {
    expect(
      resolveProjectActivity({
        projectId: "p1",
        tab: "all",
        limit: 20,
        cursor: null,
      }),
    ).toBeNull();
  });

  it("a source that declines (null) keeps the stock path", async () => {
    setProjectActivitySource(() => null);
    const list = vi
      .spyOn(activityApi, "list")
      .mockResolvedValue({ items: [item("stock")], next_cursor: null });

    const { result } = renderHook(() =>
      useActivityFeed({ projectId: "p1", tab: "all", pollMs: 0 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(list).toHaveBeenCalled();
    expect(result.current.items.map((i) => i.id)).toEqual(["stock"]);
  });

  it("a claiming source owns the project feed and its pagination", async () => {
    const pages: Record<string, ProjectActivityPage> = {
      head: { items: [item("mine-2", 2)], next_cursor: "older" },
      older: { items: [item("mine-1", 1)], next_cursor: null },
    };
    const source = vi.fn(({ cursor }: { cursor: string | null }) =>
      Promise.resolve(pages[cursor ?? "head"]),
    );
    setProjectActivitySource(source);
    const list = vi.spyOn(activityApi, "list");

    const { result } = renderHook(() =>
      useActivityFeed({ projectId: "p1", tab: "all", pollMs: 0 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items.map((i) => i.id)).toEqual(["mine-2"]);
    expect(result.current.hasMore).toBe(true);

    result.current.loadMore();
    await waitFor(() =>
      expect(result.current.items.map((i) => i.id)).toEqual([
        "mine-2",
        "mine-1",
      ]),
    );
    expect(result.current.hasMore).toBe(false);
    expect(list).not.toHaveBeenCalled();
    expect(source).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: "p1", cursor: "older" }),
    );
  });

  it("the global feed never consults the source", async () => {
    const source = vi.fn(() =>
      Promise.resolve({ items: [item("mine")], next_cursor: null }),
    );
    setProjectActivitySource(source);
    vi.spyOn(activityApi, "list").mockResolvedValue({
      items: [item("global")],
      next_cursor: null,
    });

    const { result } = renderHook(() =>
      useActivityFeed({ tab: "all", pollMs: 0 }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(source).not.toHaveBeenCalled();
    expect(result.current.items.map((i) => i.id)).toEqual(["global"]);
  });
});
