import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VALUZ_BASE_CATALOG_ID } from "../catalog";
import { createValuzMessageProcessor } from "./catalog";
import { ValuzA2UISurface } from "./surface";

function createProcessor(components: Record<string, unknown>[], data: Record<string, unknown>) {
  const processor = createValuzMessageProcessor();
  processor.processMessages([
    {
      version: "v0.9.1",
      createSurface: { surfaceId: "component-test", catalogId: VALUZ_BASE_CATALOG_ID },
    },
    {
      version: "v0.9.1",
      updateDataModel: { surfaceId: "component-test", path: "/", value: data },
    },
    {
      version: "v0.9.1",
      updateComponents: { surfaceId: "component-test", components },
    },
  ]);
  return processor;
}

describe("Valuz A2UI base components", () => {
  it("writes form values back to the A2UI data model", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["query", "topics"] },
        {
          id: "query",
          component: "Input",
          label: "Research topic",
          description: "Describe the focus of this surface.",
          value: { path: "/query" },
        },
        {
          id: "topics",
          component: "CheckboxGroup",
          label: "Coverage",
          value: { path: "/topics" },
          options: [
            { label: "Company", value: "company" },
            { label: "Industry", value: "industry" },
          ],
        },
      ],
      { query: "AI infrastructure", topics: ["company"] },
    );
    const surface = processor.model.getSurface("component-test")!;
    render(<ValuzA2UISurface surface={surface} />);

    const input = screen.getByRole("textbox", { name: "Research topic" });
    expect(input).toHaveProperty("value", "AI infrastructure");
    const descriptionId = input.getAttribute("aria-describedby");
    expect(descriptionId).toBeTruthy();
    expect(document.getElementById(descriptionId!)?.textContent).toBe(
      "Describe the focus of this surface.",
    );
    fireEvent.change(input, { target: { value: "Semiconductor equipment" } });
    expect(surface.dataModel.get("/query")).toBe("Semiconductor equipment");

    fireEvent.click(screen.getByRole("checkbox", { name: "Industry" }));
    expect(surface.dataModel.get("/topics")).toEqual(["company", "industry"]);
  });

  it("renders a responsive chart from bound records", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["chart"] },
        {
          id: "chart",
          component: "LineChart",
          title: "Revenue trend",
          data: { path: "/series" },
          xKey: "period",
          series: [{ key: "revenue", label: "Revenue" }],
          height: 240,
        },
      ],
      {
        series: [
          { period: "Q1", revenue: 12 },
          { period: "Q2", revenue: 18 },
          { period: "Q3", revenue: 24 },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);

    expect(screen.getByText("Revenue trend")).toBeTruthy();
    expect(container.querySelector(".recharts-wrapper")).toBeTruthy();
    expect(container.querySelector(".recharts-line")).toBeTruthy();
  });

  it("renders C1-style area gradients from the selected palette", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["chart"] },
        {
          id: "chart",
          component: "AreaChart",
          title: "Demand trend",
          data: { path: "/series" },
          xKey: "period",
          palette: "orchid",
          series: [{ key: "demand", label: "Demand" }],
          height: 240,
        },
      ],
      {
        series: [
          { period: "Q1", demand: 12 },
          { period: "Q2", demand: 18 },
          { period: "Q3", demand: 24 },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);

    const stops = container.querySelectorAll("linearGradient stop");
    expect(stops).toHaveLength(2);
    expect(stops[0]?.getAttribute("stop-color"))
      .toBe("var(--va2-chart-orchid-6, #883BD5)");
    expect(stops[0]?.getAttribute("stop-opacity")).toBe("0.6");
    expect(stops[1]?.getAttribute("stop-opacity")).toBe("0");
    expect(container.querySelector(".recharts-area-area")?.getAttribute("fill"))
      .toMatch(/^url\(#/);
  });

  it("renders stacked bars and a line together in a combo chart", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["chart"] },
        {
          id: "chart",
          component: "ComboChart",
          title: "Revenue and margin",
          data: { path: "/series" },
          xKey: "period",
          series: [
            { key: "revenue", label: "Revenue", type: "bar", stack: "total" },
            { key: "cost", label: "Cost", type: "bar", stack: "total" },
            { key: "margin", label: "Margin", type: "line", axis: "right" },
          ],
          rightAxis: true,
          height: 240,
        },
      ],
      {
        series: [
          { period: "Q1", revenue: 32, cost: 14, margin: 18 },
          { period: "Q2", revenue: 41, cost: 17, margin: 21 },
          { period: "Q3", revenue: 52, cost: 20, margin: 24 },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);

    expect(screen.getByText("Revenue and margin")).toBeTruthy();
    const barGroups = [...container.querySelectorAll<SVGGElement>(".recharts-bar")];
    const bar = barGroups[0]?.querySelector<SVGElement>(".recharts-rectangle");
    expect(bar).toBeTruthy();
    expect(Number(bar?.getAttribute("width"))).toBeLessThanOrEqual(20);
    expect(bar?.getAttribute("fill")).toBe("var(--va2-chart-ocean-5, #2196F3)");
    expect(bar?.getAttribute("d") ?? "").not.toMatch(/A\s+4,4/);
    expect(barGroups[1]?.querySelector(".recharts-rectangle")?.getAttribute("d") ?? "")
      .toMatch(/A\s+4,4/);
    expect(container.querySelector(".recharts-line")).toBeTruthy();
  });

  it("rounds only the outer segment of each stacked bar", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["chart"] },
        {
          id: "chart",
          component: "BarChart",
          title: "Stacked revenue",
          data: { path: "/series" },
          xKey: "period",
          stacked: true,
          series: [
            { key: "base", label: "Base" },
            { key: "middle", label: "Middle" },
            { key: "top", label: "Top" },
          ],
          height: 240,
        },
      ],
      {
        series: [
          { period: "Q1", base: 20, middle: 10, top: 5 },
          { period: "Q2", base: 18, middle: 8, top: 0 },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);
    const groups = [...container.querySelectorAll<SVGGElement>(".recharts-bar")];

    const baseBars = [...groups[0]!.querySelectorAll<SVGPathElement>(".recharts-rectangle")];
    const middleBars = [...groups[1]!.querySelectorAll<SVGPathElement>(".recharts-rectangle")];
    const topBars = [...groups[2]!.querySelectorAll<SVGPathElement>(".recharts-rectangle")];
    expect(baseBars.every((bar) => !/A\s+4,4/.test(bar.getAttribute("d") ?? ""))).toBe(true);
    expect(middleBars[0]?.getAttribute("d") ?? "").not.toMatch(/A\s+4,4/);
    expect(middleBars[1]?.getAttribute("d") ?? "").toMatch(/A\s+4,4/);
    expect(topBars[0]?.getAttribute("d") ?? "").toMatch(/A\s+4,4/);
  });

  it("keeps waterfall columns narrow and exposes distinct total and direction semantics", () => {
    const processor = createProcessor(
      [
        { id: "root", component: "Stack", children: ["chart"] },
        {
          id: "chart",
          component: "WaterfallChart",
          title: "Cash flow bridge",
          data: { path: "/series" },
          nameKey: "name",
          valueKey: "value",
          totalKey: "total",
          height: 240,
        },
      ],
      {
        series: [
          { name: "Opening", value: 25, total: true },
          { name: "Operations", value: 62 },
          { name: "CapEx", value: -14 },
          { name: "Returns", value: -31 },
          { name: "Closing", value: 42, total: true },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);

    const bars = [...container.querySelectorAll<SVGRectElement>(".va2-waterfall__bar")];
    expect(bars).toHaveLength(5);
    expect(bars.every((bar) => Number(bar.getAttribute("width")) <= 40)).toBe(true);
    expect(container.querySelectorAll('[data-kind="reference"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-kind="total"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-kind="positive"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-kind="negative"]')).toHaveLength(2);
  });
});
