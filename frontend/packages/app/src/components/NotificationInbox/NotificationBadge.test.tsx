/**
 * Badge display contract (docs/design/notifications.md):
 * - no notifications at all → renders nothing;
 * - items but zero unread → bare bell, no pill (history stays reachable);
 * - unread items → ONE brand pill showing the UNREAD count (mail-app
 *   convention) — never the total, and no extra corner dot (the old
 *   total-chip + dot pair double-signalled the same condition).
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useNotificationStore, type NotificationEntry } from "@valuz/core";

import { NotificationBadge } from "./NotificationBadge";

const entry = (
  id: string,
  overrides: Partial<NotificationEntry> = {},
): NotificationEntry => ({
  id,
  kind: "task_failed",
  title: `t-${id}`,
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
  ...overrides,
});

describe("NotificationBadge", () => {
  beforeEach(() => {
    useNotificationStore.getState().reset([]);
  });
  afterEach(cleanup);

  it("renders nothing when there are no notifications", () => {
    const { container } = render(<NotificationBadge />);
    expect(container.innerHTML).toBe("");
  });

  it("shows a bare bell without a pill when everything is read", () => {
    useNotificationStore
      .getState()
      .reset([entry("a", { read_at: 2 }), entry("b", { read_at: 2 })]);

    render(<NotificationBadge />);

    expect(screen.getByRole("button")).toBeTruthy();
    // No count pill at zero unread — and specifically not the total.
    expect(screen.queryByText("2")).toBeNull();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("shows the unread count — not the total — and no extra dot", () => {
    useNotificationStore
      .getState()
      .reset([
        entry("a"),
        entry("b", { read_at: 2 }),
        entry("c", { read_at: 2 }),
      ]);

    const { container } = render(<NotificationBadge />);

    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.queryByText("3")).toBeNull();
    // Exactly one signal: the pill. The old corner dot must not come back.
    expect(container.querySelectorAll("span.rounded-full").length).toBe(1);
  });
});
