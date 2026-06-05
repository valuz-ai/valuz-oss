import { describe, expect, it } from "vitest";
import type {
  ConversationBlock,
  ConversationTurn,
  PrototypeToolCall,
} from "@valuz/shared";
import { aggregateTurnFileChanges } from "./diff-aggregator";

const makeTool = (
  overrides: Partial<PrototypeToolCall> & Pick<PrototypeToolCall, "title">,
): PrototypeToolCall => ({
  id: overrides.id ?? `tool-${Math.random().toString(36).slice(2, 8)}`,
  kind: overrides.kind ?? "file",
  title: overrides.title,
  status: overrides.status ?? "success",
  input: overrides.input,
  output: overrides.output,
});

const makeTurn = (blocks: ConversationBlock[]): ConversationTurn => ({
  id: "turn-1",
  userMessageSeq: 1,
  userText: "test",
  blocks,
  failedMessage: null,
});

describe("aggregateTurnFileChanges", () => {
  it("should return null when the turn has no file-touching tool calls", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Bash",
          input: JSON.stringify({ command: "ls" }),
        }),
      },
    ]);
    expect(aggregateTurnFileChanges(turn)).toBeNull();
  });

  it("should count additions when an Edit replaces a single line", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/foo.ts",
            old_string: "const x = 1;",
            new_string: "const x = 2;",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes).toHaveLength(1);
    expect(summary.changes[0]!.file_path).toBe("/repo/foo.ts");
    expect(summary.changes[0]!.additions).toBe(1);
    expect(summary.changes[0]!.deletions).toBe(1);
    expect(summary.total_additions).toBe(1);
    expect(summary.total_deletions).toBe(1);
  });

  it("should count multi-line replacements correctly", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/foo.ts",
            old_string: "a\nb\nc",
            new_string: "a\nB\nC\nD",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    // diffLines counts: "a" common, "b\nc" removed (2), "B\nC\nD" added (3)
    expect(summary.changes[0]!.additions).toBe(3);
    expect(summary.changes[0]!.deletions).toBe(2);
  });

  it("should fold MultiEdit entries into a single per-file row", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "MultiEdit",
          input: JSON.stringify({
            file_path: "/repo/bar.ts",
            edits: [
              { old_string: "foo", new_string: "FOO" },
              { old_string: "baz", new_string: "BAZ" },
            ],
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes).toHaveLength(1);
    expect(summary.changes[0]!.additions).toBe(2);
    expect(summary.changes[0]!.deletions).toBe(2);
  });

  it("should merge two separate Edit calls on the same file", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/baz.ts",
            old_string: "alpha",
            new_string: "ALPHA",
          }),
        }),
      },
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/baz.ts",
            old_string: "beta",
            new_string: "BETA",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes).toHaveLength(1);
    expect(summary.changes[0]!.additions).toBe(2);
    expect(summary.changes[0]!.deletions).toBe(2);
  });

  it("should treat Write as a brand-new file with zero deletions", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Write",
          input: JSON.stringify({
            file_path: "/repo/new.ts",
            content: "line1\nline2\nline3",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes[0]!.additions).toBe(3);
    expect(summary.changes[0]!.deletions).toBe(0);
  });

  it("should propagate has_error when a contributing tool failed", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          status: "error",
          input: JSON.stringify({
            file_path: "/repo/missing.ts",
            old_string: "x",
            new_string: "y",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes[0]!.has_error).toBe(true);
  });

  it("should silently skip blocks with malformed JSON input", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: "{not json",
        }),
      },
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/ok.ts",
            old_string: "old",
            new_string: "new",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes).toHaveLength(1);
    expect(summary.changes[0]!.file_path).toBe("/repo/ok.ts");
  });

  it("should return null when every tool block is skipped", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: "{not json",
        }),
      },
    ]);
    expect(aggregateTurnFileChanges(turn)).toBeNull();
  });

  it("should emit a unified-diff string per change row", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/foo.ts",
            old_string: "before",
            new_string: "after",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes[0]!.unified_diff).toContain("-before");
    expect(summary.changes[0]!.unified_diff).toContain("+after");
  });

  it("should preserve first-seen file order across mixed tools", () => {
    const turn = makeTurn([
      {
        kind: "tool",
        tool: makeTool({
          title: "Edit",
          input: JSON.stringify({
            file_path: "/repo/b.ts",
            old_string: "x",
            new_string: "y",
          }),
        }),
      },
      {
        kind: "tool",
        tool: makeTool({
          title: "Write",
          input: JSON.stringify({
            file_path: "/repo/a.ts",
            content: "hello",
          }),
        }),
      },
    ]);
    const summary = aggregateTurnFileChanges(turn)!;
    expect(summary.changes.map((c) => c.file_path)).toEqual([
      "/repo/b.ts",
      "/repo/a.ts",
    ]);
  });
});
