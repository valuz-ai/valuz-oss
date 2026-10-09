import { describe, expect, it } from "vitest";
import { adaptSlotProps, parseJsonText, toolOutputViews } from "./convert";

describe("tool card props", () => {
  it("parses JSON text arguments and leaves other values alone", () => {
    expect(parseJsonText('{"symbol": "MSFT"}')).toEqual({ symbol: "MSFT" });
    expect(parseJsonText("plain")).toBe("plain");
    expect(parseJsonText("{broken")).toBe("{broken");
    expect(parseJsonText({ a: 1 })).toEqual({ a: 1 });
  });

  it("reads MCP content blocks handed over as a JSON string", () => {
    const raw = JSON.stringify([
      { type: "text", text: '{\n  "symbol": "MSFT",\n  "price": 428.1\n}' },
      { type: "text", text: "[note] verified" },
    ]);
    const views = toolOutputViews(raw);
    expect(views.outputJson).toEqual({ symbol: "MSFT", price: 428.1 });
    expect(views.outputText).toContain("[note] verified");
  });

  it("prefers structuredContent and handles plain strings and empty output", () => {
    expect(
      toolOutputViews({ content: [{ type: "text", text: "x" }], structuredContent: { ok: 1 } })
        .outputJson,
    ).toEqual({ ok: 1 });
    expect(toolOutputViews("Exit code 1")).toEqual({ outputText: "Exit code 1", outputJson: null });
    expect(toolOutputViews(null)).toEqual({ outputText: "", outputJson: null });
  });

  it("adapts the tool-card slot props", () => {
    const props = adaptSlotProps("conversation.tool-card.get_quote", {
      tool: { id: "t1", name: "mcp__acme-data__get_quote", input: '{"symbol":"NVDA"}' },
      toolUseId: "t1",
      status: "success",
      input: '{"symbol":"NVDA"}',
      output: JSON.stringify([{ type: "text", text: '{"price": 131.9}' }]),
    });
    expect(props.input).toEqual({ symbol: "NVDA" });
    expect(props.outputJson).toEqual({ price: 131.9 });
    expect((props.tool as { input: unknown }).input).toEqual({ symbol: "NVDA" });
  });
});
