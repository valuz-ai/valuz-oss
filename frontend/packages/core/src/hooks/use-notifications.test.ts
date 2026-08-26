import { beforeEach, describe, expect, it, vi } from "vitest";

const markReadMock = vi.fn((id: string) => {
  void id;
  return Promise.resolve({ ok: true });
});
const dismissMock = vi.fn((id: string) => {
  void id;
  return Promise.resolve({ ok: true });
});
const dismissAllMock = vi.fn(() => Promise.resolve({ ok: true }));
vi.mock("../api/notifications-api", () => ({
  notificationsApi: {
    markRead: (id: string) => markReadMock(id),
    dismiss: (id: string) => dismissMock(id),
    dismissAll: () => dismissAllMock(),
  },
}));

import type { NotificationEntry } from "../api/notifications-api";
import { useNotificationStore } from "../store/notification-store";
import {
  dismissAllNotifications,
  dismissNotification,
  markSessionNotificationsRead,
} from "./use-notifications";

const entry = (over: Partial<NotificationEntry>): NotificationEntry => ({
  id: "n1",
  kind: "question",
  title: "",
  body: "",
  route: null,
  action: "none",
  urgency: "info",
  task_id: null,
  project_id: null,
  session_id: null,
  pending_id: null,
  payload: {},
  created_at: 1,
  read_at: null,
  resolved_at: null,
  ...over,
});

const readAt = (id: string): number | null =>
  useNotificationStore.getState().entries.get(id)?.read_at ?? null;

beforeEach(() => {
  markReadMock.mockClear();
  dismissMock.mockClear();
  dismissAllMock.mockClear();
  useNotificationStore.setState({
    entries: new Map(),
    freshIds: new Set(),
    alertedIds: new Set(),
    _everReset: false,
    _inited: false,
  });
});

describe("markSessionNotificationsRead", () => {
  it("should mark a session's open unread notifications read when the conversation opens", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n1", session_id: "s1" })]);

    markSessionNotificationsRead("s1");

    expect(readAt("n1")).not.toBeNull(); // optimistic badge decrement
    expect(markReadMock).toHaveBeenCalledExactlyOnceWith("n1"); // persisted
  });

  it("should not touch notifications belonging to other sessions", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n2", session_id: "s2" })]);

    markSessionNotificationsRead("s1");

    expect(readAt("n2")).toBeNull();
    expect(markReadMock).not.toHaveBeenCalled();
  });

  it("should skip notifications already read (no redundant persist)", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n3", session_id: "s1", read_at: 123 })]);

    markSessionNotificationsRead("s1");

    expect(markReadMock).not.toHaveBeenCalled();
  });

  it("should no-op for an empty session id", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n4", session_id: "s1" })]);

    markSessionNotificationsRead("");

    expect(readAt("n4")).toBeNull();
    expect(markReadMock).not.toHaveBeenCalled();
  });
});

describe("dismissNotification", () => {
  it("should remove the entry from the store immediately and persist the dismiss", () => {
    useNotificationStore.getState().reset([entry({ id: "n1" })]);

    dismissNotification("n1");

    // Optimistic: gone before any network round-trip resolves.
    expect(useNotificationStore.getState().entries.has("n1")).toBe(false);
    expect(dismissMock).toHaveBeenCalledExactlyOnceWith("n1");
  });
});

describe("dismissAllNotifications", () => {
  it("should empty the open set immediately and persist one dismiss-all", () => {
    useNotificationStore
      .getState()
      .reset([entry({ id: "n1" }), entry({ id: "n2", kind: "task_failed" })]);

    dismissAllNotifications();

    expect(useNotificationStore.getState().entries.size).toBe(0);
    expect(dismissAllMock).toHaveBeenCalledOnce();
    expect(dismissMock).not.toHaveBeenCalled(); // one bulk call, not N singles
  });
});
