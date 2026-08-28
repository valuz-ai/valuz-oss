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

  it("keeps scanning past a non-object block to the real payload", () => {
    // A text block whose JSON is valid but not an object ("prose", 42) must
    // not short-circuit the scan — the payload block follows it.
    const payload = { action: "create", ok: false, message: "nope" };
    const wrapped = JSON.stringify([
      { type: "text", text: '"interim prose"' },
      { type: "text", text: JSON.stringify(payload) },
    ]);
    const result = parseAutomationToolOutput(wrapped);
    expect(result?.ok).toBe(false);
    expect(result?.message).toBe("nope");
  });

  it("recovers the payload from a legacy Python-repr envelope", () => {
    // Older kernel output: ``[{'type': 'text', 'text': '{...}'}]`` — not
    // valid JSON (single quotes), so the scan fallback must find the object.
    const repr =
      "[{'type': 'text', 'text': '{\"action\": \"create\", \"ok\": false, \"message\": \"agent_slug is required\"}'}]";
    const result = parseAutomationToolOutput(repr);
    expect(result?.ok).toBe(false);
    expect(result?.message).toBe("agent_slug is required");
  });

  it("returns null for prose output", () => {
    expect(parseAutomationToolOutput("the tool is still running")).toBeNull();
    expect(parseAutomationToolOutput(undefined)).toBeNull();
  });
});