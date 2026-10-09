import { useRegistryStore } from "@valuz/core";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalTray } from "./ApprovalTray";
import type { PendingApprovalEntry } from "./useConversationHistory";

function entry(over: Partial<PendingApprovalEntry> = {}): PendingApprovalEntry {
  return {
    pendingId: "pending-0001-abcdef",
    subject: "shell_command",
    payload: { command: "ls -la" },
    availableDecisions: ["approve", "reject"],
    sessionRulePreviewDisplay: null,
    originalInput: null,
    ...over,
  };
}

function renderTray(
  entries: PendingApprovalEntry[],
  decide: (...args: unknown[]) => void = () => {},
) {
  return render(
    <ApprovalTray
      pendingApprovals={entries}
      autoApprovedNotices={[]}
      handleApprovalDecision={decide as never}
    />,
  );
}

const register = (
  name: string,
  id: string,
  component: (props: Record<string, unknown>) => React.ReactNode,
  key?: string,
) =>
  useRegistryStore
    .getState()
    .registerSlot(name, { id, component: component as never, key });

describe("ApprovalTray slots", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });
  afterEach(() => {
    // Unmount first: clearing the registry under a mounted slot host is a
    // store update the test would have to ``act`` around.
    cleanup();
    act(() => useRegistryStore.setState({ slots: {} }));
  });

  it("renders the default card with exactly its own buttons while the slots are empty", () => {
    renderTray([entry()]);
    expect(
      screen.getAllByRole("button").map((b) => b.textContent?.trim()),
    ).toEqual(["拒绝", "批准"]);
    expect(screen.getByText("ls -la")).toBeTruthy();
  });

  describe("conversation.approval.card (keyed by subject)", () => {
    it("takes over the card for the claimed subject, with entry and a bound decide", () => {
      const decide = vi.fn();
      register(
        "conversation.approval.card",
        "custom-shell",
        ({ entry: e, decide: d }) => (
          <button
            data-testid="custom-card"
            onClick={() =>
              (d as (v: string, o?: unknown) => void)("reject", {
                message: "no",
              })
            }
          >
            {(e as PendingApprovalEntry).pendingId}
          </button>
        ),
        "shell_command",
      );
      renderTray([entry()], decide);

      expect(screen.getByTestId("custom-card").textContent).toBe(
        "pending-0001-abcdef",
      );
      // The default card is gone for this subject.
      expect(screen.queryByText("ls -la")).toBeNull();

      fireEvent.click(screen.getByTestId("custom-card"));
      // ``decide`` is bound to the entry's pendingId.
      expect(decide).toHaveBeenCalledWith("pending-0001-abcdef", "reject", {
        message: "no",
      });
    });

    it("leaves other subjects on the default card", () => {
      register(
        "conversation.approval.card",
        "custom-file",
        () => <div data-testid="custom-card" />,
        "file_change",
      );
      renderTray([entry({ subject: "shell_command" })]);
      expect(screen.queryByTestId("custom-card")).toBeNull();
      expect(screen.getByText("ls -la")).toBeTruthy();
    });
  });

  describe("conversation.approval.actions", () => {
    it("adds the contribution to the default card's action row, before Reject", () => {
      const seen: Array<Record<string, unknown>> = [];
      register("conversation.approval.actions", "extra", (props) => {
        seen.push(props);
        return <button data-testid="ext-approval">more</button>;
      });
      renderTray([entry()]);

      const extra = screen.getByTestId("ext-approval");
      expect(extra.nextElementSibling?.textContent?.trim()).toBe("拒绝");
      expect(seen[0]).toMatchObject({
        pendingId: "pending-0001-abcdef",
        subject: "shell_command",
        payload: { command: "ls -la" },
      });
    });

    it("is not shown on a card a plugin has taken over", () => {
      register("conversation.approval.actions", "extra", () => (
        <span data-testid="ext-approval" />
      ));
      register(
        "conversation.approval.card",
        "custom-shell",
        () => <div data-testid="custom-card" />,
        "shell_command",
      );
      renderTray([entry()]);
      expect(screen.getByTestId("custom-card")).toBeTruthy();
      expect(screen.queryByTestId("ext-approval")).toBeNull();
    });
  });
});
