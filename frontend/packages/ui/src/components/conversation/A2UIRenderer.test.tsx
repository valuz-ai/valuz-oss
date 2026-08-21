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

  it("extracts a component's named data input before rendering and starts the edition host", () => {
    let requested: unknown;
    registerGenUIDataHost(({ surfaceId, dataRefs }) => {
      requested = { surfaceId, dataRefs };
      return { stop: () => undefined };
    });
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "s",
          components: [{
            id: "root",
            component: "TextContent",
            text: "Live",
            dataRefs: { main: { source: "test.text", params: { id: "1" } } },
          }],
        },
      }),
    ].join("\n");
    render(<A2UIRenderer body={body} />);
    expect(screen.getByText("Live")).toBeTruthy();
    expect(requested).toEqual({
      surfaceId: "s",
      dataRefs: [{
        componentId: "root",
        component: "TextContent",
        inputKey: "main",
        ref: { source: "test.text", params: { id: "1" } },
      }],
    });
  });

  it("extracts every named input from component-owned dataRefs", () => {
    let requested: unknown;
    registerGenUIDataHost(({ surfaceId, dataRefs }) => {
      requested = { surfaceId, dataRefs };
      return { stop: () => undefined };
    });
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({
        version: "v0.9.1",
        updateComponents: {
          surfaceId: "s",
          components: [{
            id: "company",
            component: "CompanyResearchOverview",
            title: "Company",
            dataRefs: {
              quote: { source: "finance.market.quote", params: { symbol: "US:NVDA" } },
              documents: { source: "finance.company.docs", params: { symbol: "US:NVDA" } },
            },
          }],
        },
      }),
    ].join("\n");
    render(<A2UIRenderer body={body} />);
    expect(requested).toEqual({
      surfaceId: "s",
      dataRefs: [
        {
          componentId: "company",
          component: "CompanyResearchOverview",
          inputKey: "quote",
          ref: { source: "finance.market.quote", params: { symbol: "US:NVDA" } },
        },
        {
          componentId: "company",
          component: "CompanyResearchOverview",
          inputKey: "documents",
          ref: { source: "finance.company.docs", params: { symbol: "US:NVDA" } },
        },
      ],
    });
  });

  it("does not start the edition host for surface-global refs", () => {
    let calls = 0;
    registerGenUIDataHost(() => {
      calls += 1;
      return { stop: () => undefined };
    });
    const body = [
      JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" } }),
      JSON.stringify({ version: "v0.9.1", updateDataModel: { surfaceId: "s", path: "/refs/quote", value: { source: "finance.market.daily" } } }),
      JSON.stringify({ version: "v0.9.1", updateComponents: { surfaceId: "s", components: [{ id: "root", component: "TextContent", text: "Static" }] } }),
    ].join("\n");
    render(<A2UIRenderer body={body} />);
    expect(calls).toBe(0);
  });
});
