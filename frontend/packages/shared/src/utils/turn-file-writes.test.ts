import { describe, expect, it } from "vitest";

import { fileWritesInTurns } from "./turn-file-writes";
import type { ConversationTurn, PrototypeToolCall } from "../types/conversation";

function toolTurn(...tools: PrototypeToolCall[]): ConversationTurn {
  return {
    id: "turn-1",
    blocks: tools.map((tool) => ({ kind: "tool" as const, tool })),
  } as unknown as ConversationTurn;
}

function tool(over: Partial<PrototypeToolCall>): PrototypeToolCall {
  return {
    id: "call-1",
    kind: "file",
    title: "Edit",
    status: "success",
    ...over,
  };
}

describe("fileWritesInTurns", () => {
  it("reports the file each writing tool named", () => {
    const turns = [
      toolTurn(
        tool({
          id: "a",
          title: "Edit",
          input: JSON.stringify({ file_path: "/w/report.md" }),
        }),
        tool({
          id: "b",
          title: "Write",
          input: JSON.stringify({ file_path: "/w/new.md" }),
        }),
        tool({
          id: "c",
          title: "MultiEdit",
          input: JSON.stringify({ file_path: "/w/multi.md" }),
        }),
      ),
    ];
    expect(fileWritesInTurns(turns)).toEqual([
      { toolCallId: "a", path: "/w/report.md" },
      { toolCallId: "b", path: "/w/new.md" },
      { toolCallId: "c", path: "/w/multi.md" },
    ]);
  });

  it("keeps both writes when one file is edited twice", () => {
    // The whole point of carrying the tool id: a watcher that deduped on path
    // would refresh once and show the first edit's content.
    const turns = [
      toolTurn(
        tool({ id: "a", input: JSON.stringify({ file_path: "/w/same.md" }) }),
        tool({ id: "b", input: JSON.stringify({ file_path: "/w/same.md" }) }),
      ),
    ];
    expect(fileWritesInTurns(turns)).toEqual([
      { toolCallId: "a", path: "/w/same.md" },
      { toolCallId: "b", path: "/w/same.md" },
    ]);
  });

  it("spreads apply_patch over every file it touched", () => {
    const turns = [
      toolTurn(
        tool({
          id: "a",
          title: "apply_patch",
          input: JSON.stringify({
            changes: [{ path: "/w/one.ts" }, { path: "/w/two.ts" }],
          }),
        }),
      ),
    ];
    expect(fileWritesInTurns(turns).map((w) => w.path)).toEqual([
      "/w/one.ts",
      "/w/two.ts",
    ]);
  });

  it("waits for a running call and ignores a failed one", () => {
    const turns = [
      toolTurn(
        tool({
          id: "a",
          status: "running",
          input: JSON.stringify({ file_path: "/w/inflight.md" }),
        }),
        tool({
          id: "b",
          status: "error",
          input: JSON.stringify({ file_path: "/w/failed.md" }),
        }),
      ),
    ];
    expect(fileWritesInTurns(turns)).toEqual([]);
  });

  it("ignores tools that only read, and unparseable input", () => {
    const turns = [
      toolTurn(
        tool({
          id: "a",
          title: "Read",
          input: JSON.stringify({ file_path: "/w/read.md" }),
        }),
        tool({ id: "b", title: "Edit", input: "not json" }),
        tool({ id: "c", title: "Edit" }),
      ),
    ];
    expect(fileWritesInTurns(turns)).toEqual([]);
  });
});
