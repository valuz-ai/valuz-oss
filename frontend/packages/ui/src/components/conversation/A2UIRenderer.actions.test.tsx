import { resetA2UIComponentsForTests } from "@valuz/a2ui";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";
import {
  registerGenUIActionSink,
  unregisterGenUIActionSink,
} from "./genui-channel";

const body = [
  JSON.stringify({
    version: "v0.9.1",
    createSurface: {
      surfaceId: "s",
      catalogId: "https://valuz.io/a2ui/catalogs/base/v1",
    },
  }),
  JSON.stringify({
    version: "v0.9.1",
    updateComponents: {
      surfaceId: "s",
      components: [
        { id: "root", component: "Stack", children: ["cta"] },
        {
          id: "cta",
          component: "Button",
          label: "Ask the agent",
          action: { event: { name: "ask_agent" } },
        },
      ],
    },
  }),
].join("\n");

describe("A2UIRenderer action forwarding", () => {
  afterEach(() => {
    unregisterGenUIActionSink();
    resetA2UIComponentsForTests();
  });

  it("forwards component actions to the host sink with the render host identity", () => {
    const sink = vi.fn();
    registerGenUIActionSink(sink);

    render(<A2UIRenderer body={body} hostParams={{ symbol: "US:NVDA" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the agent" }));

    expect(sink).toHaveBeenCalledTimes(1);
    expect(sink).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "ask_agent",
        surfaceId: "s",
        sourceComponentId: "cta",
        host: { symbol: "US:NVDA" },
      }),
    );
  });

  it("omits host identity when the renderer was mounted without hostParams", () => {
    const sink = vi.fn();
    registerGenUIActionSink(sink);

    render(<A2UIRenderer body={body} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the agent" }));

    expect(sink).toHaveBeenCalledTimes(1);
    expect(sink.mock.calls[0][0]).not.toHaveProperty("host");
  });

  it("stays inert when no sink is registered", () => {
    render(<A2UIRenderer body={body} />);
    expect(() =>
      fireEvent.click(screen.getByRole("button", { name: "Ask the agent" })),
    ).not.toThrow();
  });
});
