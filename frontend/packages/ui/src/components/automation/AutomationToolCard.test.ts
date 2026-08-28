import { describe, expect, it } from "vitest";

import { parseAutomationToolOutput } from "./AutomationToolCard";

describe("parseAutomationToolOutput", () => {
  it("parses bare JSON results", () => {
    const result = parseAutomationToolOutput(
      JSON.stringify({ action: "create", ok: false, message: "nope" }),
    );
    expect(result?.ok).toBe(false);
    expect(result?.action).toBe("create");
  });

  it("unwraps the kernel content-block envelope", () => {
    // The Valuz/DeepAgents runtime delivers the payload one level down; a
    // bare JSON.parse returns the ARRAY and the ok:false result was being
    // mistaken for "no result" — which rendered a confirmable card from the
    // unvalidated input.
    const payload = { action: "create", ok: false, message: "trigger is required" };
    const wrapped = JSON.stringify([
      { type: "text", text: JSON.stringify(payload) },
    ]);
    const result = parseAutomationToolOutput(wrapped);
    expect(result?.ok).toBe(false);
    expect(result?.message).toBe("trigger is required");
  });

  it("unwraps nested envelopes", () => {
    const payload = { action: "create", ok: true, message: "ok", proposal: null };
    const wrapped = JSON.stringify([
      { type: "text", text: JSON.stringify([{ type: "text", text: JSON.stringify(payload) }]) },
    ]);
    expect(parseAutomationToolOutput(wrapped)?.ok).toBe(true);
  });

  it("returns null for prose output", () => {
    expect(parseAutomationToolOutput("the tool is still running")).toBeNull();
    expect(parseAutomationToolOutput(undefined)).toBeNull();
  });
});