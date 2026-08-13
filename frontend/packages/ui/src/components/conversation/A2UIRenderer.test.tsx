import { createComponentImplementation, registerA2UIComponents, resetA2UIComponentsForTests, type ComponentApi, z } from "@valuz/a2ui";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";
import { registerGenUIDataHost, unregisterGenUIDataHost } from "./genui-channel";

const stream = (component: Record<string, unknown>, data?: Record<string, unknown>) => [
  JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
  ...(data ? [JSON.stringify({ version: "v0.9.1", updateDataModel: { surfaceId: "s", path: "/", value: data } })] : []),
  JSON.stringify({ version: "v0.9.1", updateComponents: { surfaceId: "s", components: [component] } }),
].join("\n");

describe("A2UIRenderer", () => {
  afterEach(() => {
    unregisterGenUIDataHost();
    resetA2UIComponentsForTests();
  });

  it("renders strict base A2UI components", () => {
    render(<A2UIRenderer body={stream({ id: "root", component: "TextContent", text: "Revenue", variant: "h2" })} />);
    expect(screen.getByText("Revenue")).toBeTruthy();
  });

  it("renders edition components registered in the A2UI catalog", () => {
    const api: ComponentApi = { name: "TestEditionCard", schema: z.object({ title: z.string() }).strict() };
    registerA2UIComponents("test", [
      createComponentImplementation(api, ({ props }) => <strong>{props.title}</strong>),
    ]);
    render(<A2UIRenderer body={stream({ id: "root", component: "TestEditionCard", title: "Edition" })} />);
    expect(screen.getByText("Edition")).toBeTruthy();
  });

  it("keeps valid siblings visible when one model-authored component is invalid", () => {
    const api: ComponentApi = {
      name: "StrictEditionCard",
      schema: z.object({ title: z.string(), sections: z.array(z.string()) }).strict(),
    };
    registerA2UIComponents("strict-edition", [
      createComponentImplementation(api, ({ props }) => <strong>{props.title}</strong>),
    ]);
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "s",
          components: [
            { id: "root", component: "Stack", children: ["valid", "invalid"] },
            { id: "valid", component: "TextContent", text: "Still visible" },
            { id: "invalid", component: "StrictEditionCard", title: "Missing sections" },
          ],
        },
      }),
    ].join("\n");

    render(<A2UIRenderer body={body} />);

    expect(screen.getByText("Still visible")).toBeTruthy();
    expect(screen.queryByText("Missing sections")).toBeNull();
  });

  it("normalizes weighted child objects emitted by a model", () => {
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "s",
          components: [
            { id: "root", component: "Grid", children: [{ id: "left", weight: 1 }, { id: "right", weight: 2 }] },
            { id: "left", component: "TextContent", text: "Left" },
            { id: "right", component: "TextContent", text: "Right" },
          ],
        },
      }),
    ].join("\n");

    render(<A2UIRenderer body={body} />);

    expect(screen.getByText("Left")).toBeTruthy();
    expect(screen.getByText("Right")).toBeTruthy();
  });

  it("starts the edition data host for declared refs", () => {
    let requested = "";
    registerGenUIDataHost(({ surfaceId }) => {
      requested = surfaceId;
      return { stop: () => undefined };
    });
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({ version: "v0.9.1", updateDataModel: { surfaceId: "s", path: "/refs/quote", value: { source: "finance.market.daily" } } }),
      JSON.stringify({ version: "v0.9.1", updateComponents: { surfaceId: "s", components: [{ id: "root", component: "TextContent", text: "Live" }] } }),
    ].join("\n");
    render(<A2UIRenderer body={body} />);
    expect(requested).toBe("s");
  });
});
