import { describe, expect, it } from "vitest";

import { parseOperationToolOutput } from "./operations-api";

const operationResult = {
  ok: true,
  action: "create",
  operation: {
    id: "op-1",
    state: "awaiting_confirmation",
  },
};

describe("parseOperationToolOutput", () => {
  it("parses the raw operation JSON returned by the MCP server", () => {
    expect(parseOperationToolOutput(JSON.stringify(operationResult))).toEqual(
      operationResult,
    );
  });

  it("parses an MCP structured-content envelope", () => {
    const envelope = [
      { type: "text", text: JSON.stringify(operationResult) },
    ];
    expect(parseOperationToolOutput(JSON.stringify(envelope))).toEqual(
      operationResult,
    );
  });

  it("rejects unrelated and malformed tool output", () => {
    expect(parseOperationToolOutput('{"ok":true}')).toBeNull();
    expect(parseOperationToolOutput("not json")).toBeNull();
  });
});
