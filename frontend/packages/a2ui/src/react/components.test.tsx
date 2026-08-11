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

  it("renders bar and line series together in a combo chart", () => {
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
            { key: "revenue", label: "Revenue", type: "bar" },
            { key: "margin", label: "Margin", type: "line", axis: "right" },
          ],
          rightAxis: true,
          height: 240,
        },
      ],
      {
        series: [
          { period: "Q1", revenue: 32, margin: 18 },
          { period: "Q2", revenue: 41, margin: 21 },
          { period: "Q3", revenue: 52, margin: 24 },
        ],
      },
    );
    const surface = processor.model.getSurface("component-test")!;
    const { container } = render(<ValuzA2UISurface surface={surface} />);

    expect(screen.getByText("Revenue and margin")).toBeTruthy();
    expect(container.querySelector(".recharts-bar-rectangle")).toBeTruthy();
    expect(container.querySelector(".recharts-line")).toBeTruthy();
  });
});
