import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("./A2UIBody", () => ({
  default: ({ body, status }: { body: string; status?: string }) => (
    <div data-testid="a2ui-renderer" data-status={status}>
      {body}
    </div>
  ),
}));

import { GenerativeUIRenderer } from "./GenerativeUIRenderer";
import { parseGenerativeUIPayload } from "./generative-ui-payload";

describe("GenerativeUIRenderer", () => {
  it("draws nothing for a payload that is not an A2UI stream", () => {
    // A2UI is the only protocol. Anything else — a plain-text error or unknown
    // structured result — has no renderer, and printing its source where a
    // rendered UI belongs reads as a bug in the answer.
    const { container } = render(
      <GenerativeUIRenderer payload={"root = Stack([])"} status="success" />,
    );

    expect(screen.queryByTestId("a2ui-renderer")).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("refuses a payload whose envelope names an unknown protocol", () => {
    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "legacy-json", content: "root = Stack([])" }),
      ),
    ).toBeNull();
  });

  it("parses an A2UI protocol envelope", () => {
    const messages = [
      JSON.stringify({
        version: "v0.9.1",
        createSurface: { surfaceId: "s1", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" },
      }),
    ].join("\n");

    expect(
      parseGenerativeUIPayload(
        JSON.stringify({ protocol: "a2ui-json", content: messages }),
      ),
    ).toEqual({ protocol: "a2ui-json", body: messages });
  });

  it("renders A2UI payloads through the A2UI renderer", async () => {
    const messages = [
      JSON.stringify({
        version: "v0.9.1",
        createSurface: { surfaceId: "dashboard", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" },
      }),
      JSON.stringify({
        version: "v0.9.1",
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

    expect((await screen.findByTestId("a2ui-renderer")).textContent).toBe(messages);
  });

  it("should keep showing the surface while a live run has written nothing yet", () => {
    // The model can reason for a minute before its first byte. Returning null
    // through all of it leaves the workbench — the very surface that is
    // supposed to be showing the generation — completely blank.
    render(<GenerativeUIRenderer payload="" status="running" />);

    expect(screen.getByTestId("a2ui-renderer").dataset.status).toBe("running");
  });

  it("should render nothing for an empty payload once the run is over", () => {
    const { container } = render(
      <GenerativeUIRenderer payload="" status="success" />,
    );

    expect(container.firstChild).toBe(null);
  });
});
