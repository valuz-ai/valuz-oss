import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-lang", () => ({
  Renderer: (props: { response: string; isStreaming?: boolean }) => (
    <div
      data-testid="renderer"
      data-streaming={props.isStreaming ? "true" : "false"}
    >
      {props.response}
    </div>
  ),
}));
vi.mock("@openuidev/react-ui", () => ({
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
// The OpenUI Lang branch renders against the merged OpenUI + Valuz library.
// Stubbing the factory keeps this file about protocol dispatch; the merge is
// covered by @valuz/genui-blocks' own tests and by
// GenerativeUICard.blocks.test.tsx, which use the real parser.
vi.mock("@valuz/genui-blocks", () => ({
  createValuzLibrary: () => ({}),
}));
vi.mock("./A2UIRenderer", () => ({
  A2UIRenderer: ({ body }: { body: string }) => (
    <div data-testid="a2ui-renderer">{body}</div>
  ),
}));

import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

describe("GenerativeUIRenderer", () => {
  it("renders OpenUI Lang through the OpenUI renderer", () => {
    render(
      <GenerativeUIRenderer payload={"Chart\n  data: 1"} status="running" />,
    );

    const renderer = screen.getByTestId("renderer");
    expect(renderer.textContent).toBe("Chart\n  data: 1");
    expect(renderer.getAttribute("data-streaming")).toBe("true");
  });

  it("parses an A2UI protocol envelope", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9",
        createSurface: { surfaceId: "s1", catalogId: "openui" },
      }),
    ].join("\n");

    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "a2ui-json", content: messages }),
      ),
    ).toEqual({ protocol: "a2ui-json", body: messages });
  });

  it("renders A2UI payloads through the A2UI renderer", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9",
        createSurface: { surfaceId: "dashboard", catalogId: "openui" },
      }),
      JSON.stringify({
        version: "v0.9",
        updateComponents: {
          surfaceId: "dashboard",
          components: [
            { id: "root", component: "TextContent", text: "Revenue" },
          ],
        },
      }),
    ].join("\n");

    render(
      <GenerativeUIRenderer
        payload={JSON.stringify({ protocol: "a2ui-json", content: messages })}
      />,
    );

    expect(screen.queryByTestId("renderer")).toBeNull();
    expect(screen.getByTestId("a2ui-renderer").textContent).toBe(messages);
  });
});
