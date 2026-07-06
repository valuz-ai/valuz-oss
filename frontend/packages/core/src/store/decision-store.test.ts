/**
 * Decision store reset semantics (ADR-022 + the inbox-visibility fix).
 *
 * The load-bearing behavior under test: ``reset`` is the recovery path for
 * questions the live SSE stream missed (reconnect snapshot / poll backstop),
 * so entries appearing for the FIRST time in a later snapshot must gain
 * unread salience — while the initial page-load snapshot stays silent.
 */
import { beforeEach, describe, expect, it } from "vitest";

import type { DecisionEntry } from "../api/decisions-api";
import { useDecisionStore } from "./decision-store";

function entry(pendingId: string): DecisionEntry {
  return {
    pending_id: pendingId,
    session_id: "s1",
    source_kind: "task",
    task_id: "t1",
    project_id: null,
    project_title: null,
    project_emoji: null,
    task_title: "T",
    subtask_key: null,
    subtask_label: null,
    session_title: null,
    agent_slug: "a",
    question_payload: { questions: [] },
    raised_at: 1,
  };
}

beforeEach(() => {
  useDecisionStore.setState({
    pending: new Map(),
    unreadIds: new Set(),
    toastedIds: new Set(),
    isOpen: false,
    _inited: false,
    _everReset: false,
  });
});

describe("reset", () => {
  it("first snapshot stays silent (no unread, no toast pressure)", () => {
    useDecisionStore.getState().reset([entry("p1"), entry("p2")]);
    const s = useDecisionStore.getState();
    expect(s.pending.size).toBe(2);
    expect(s.unreadIds.size).toBe(0);
  });

  it("a later snapshot marks never-held entries unread (recovered question)", () => {
    useDecisionStore.getState().reset([entry("p1")]);
    useDecisionStore.getState().reset([entry("p1"), entry("p2")]);
    const s = useDecisionStore.getState();
    expect(s.pending.size).toBe(2);
    expect(s.unreadIds.has("p2")).toBe(true);
    expect(s.unreadIds.has("p1")).toBe(false);
  });

  it("retained entries keep their unread state across snapshots", () => {
    useDecisionStore.getState().reset([]);
    useDecisionStore.getState().add(entry("p1")); // live add → unread
    expect(useDecisionStore.getState().unreadIds.has("p1")).toBe(true);
    useDecisionStore.getState().reset([entry("p1")]); // reconnect snapshot
    expect(useDecisionStore.getState().unreadIds.has("p1")).toBe(true);
  });

  it("drops entries absent from the snapshot (resolved elsewhere)", () => {
    useDecisionStore.getState().reset([entry("p1"), entry("p2")]);
    useDecisionStore.getState().reset([entry("p2")]);
    const s = useDecisionStore.getState();
    expect(s.pending.has("p1")).toBe(false);
    expect(s.pending.has("p2")).toBe(true);
  });

  it("identical snapshot is a no-op (poll backstop must not re-render)", () => {
    useDecisionStore.getState().reset([entry("p1")]);
    const before = useDecisionStore.getState().pending;
    useDecisionStore.getState().reset([entry("p1")]);
    expect(useDecisionStore.getState().pending).toBe(before); // same ref
  });
});
