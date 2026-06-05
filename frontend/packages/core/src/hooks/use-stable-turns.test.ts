/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  ConversationBlock,
  ConversationTurn,
  PrototypeToolCall,
} from "@valuz/shared";
import { useStableTurns } from "./use-stable-turns";

const toolBlock = (
  over: Partial<PrototypeToolCall> = {},
): ConversationBlock => ({
  kind: "tool",
  tool: {
    id: "t1",
    kind: "kb",
    title: "search",
    status: "running",
    ...over,
  },
});
const thinking = (text: string): ConversationBlock => ({
  kind: "thinking",
  text,
});
const assistant = (text: string): ConversationBlock => ({
  kind: "assistant",
  text,
});

const turn = (blocks: ConversationBlock[]): ConversationTurn => ({
  id: "turn-1",
  userMessageSeq: 1,
  userText: "hi",
  blocks,
  failedMessage: null,
});

describe("useStableTurns", () => {
  it("should return a fresh turn reference when a non-last block changes", () => {
    // Tool block at index 0 (NOT last — a thinking block already follows it)
    // transitions running→success. This is the case the old last-block-only
    // diff missed, freezing the view until the turn ended.
    const v1 = turn([toolBlock({ status: "running" }), thinking("reasoning…")]);
    const { result, rerender } = renderHook(
      ({ turns }) => useStableTurns(turns),
      { initialProps: { turns: [v1] } },
    );
    expect(result.current[0]).toBe(v1);

    const v2 = turn([
      toolBlock({ status: "success", output: "done" }),
      thinking("reasoning…"),
    ]);
    rerender({ turns: [v2] });
    expect(result.current[0]).toBe(v2);
  });

  it("should reuse the old reference when no block changed", () => {
    const v1 = turn([
      toolBlock({ status: "success", output: "done" }),
      thinking("x"),
    ]);
    const { result, rerender } = renderHook(
      ({ turns }) => useStableTurns(turns),
      { initialProps: { turns: [v1] } },
    );
    // Structurally identical but freshly built objects (mimics buildTurns
    // rebuilding from the events array on every render).
    const same = turn([
      toolBlock({ status: "success", output: "done" }),
      thinking("x"),
    ]);
    rerender({ turns: [same] });
    expect(result.current[0]).toBe(v1);
  });

  it("should return a fresh reference when the last block text grows", () => {
    const v1 = turn([assistant("hel")]);
    const { result, rerender } = renderHook(
      ({ turns }) => useStableTurns(turns),
      { initialProps: { turns: [v1] } },
    );
    const v2 = turn([assistant("hello")]);
    rerender({ turns: [v2] });
    expect(result.current[0]).toBe(v2);
  });
});
