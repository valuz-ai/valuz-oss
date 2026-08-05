import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Charts are stubbed to print the data they were handed. That is the whole
// point of this file: the defect was in the *data* a chart received, not in how
// it drew — so asserting on rendered plot DOM would test recharts, not us.
vi.mock("@openuidev/react-ui", () => ({
  HorizontalBarChart: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="horizontal-chart">{JSON.stringify(data)}</div>
  ),
  BarChartCondensed: ({ data }: { data: Record<string, unknown>[] }) => (
    <div data-testid="bar-chart">{JSON.stringify(data)}</div>
  ),
}));
vi.mock("@openuidev/react-ui/Modal", () => ({ Modal: () => null }));

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * Regression for a chart that rendered as a tall empty box.
 *
 * A2UI nests by id: the model emitted the sector chart as
 * `{ component: "HorizontalBarChart", labels: [...], children: ["sector-series"] }`
 * with the Series as a sibling component. Chart data is read out of props
 * rather than rendered, so the reference never resolved and the chart received
 * one row per label with no numeric key on any of them. That is worse than no
 * data: it cleared the empty-data guard, so the chart reserved a full-height
 * plot and drew nothing in it.
 *
 * The payload shape below is the one taken from the session that surfaced it.
 */

function a2ui(components: Record<string, unknown>[]): string {
  return [
    { version: "v0.9", createSurface: { surfaceId: "s", catalogId: "openui" } },
    { version: "v0.9", updateComponents: { surfaceId: "s", components } },
  ]
    .map((m) => JSON.stringify(m))
    .join("\n");
}

describe("A2UI charts whose series arrives by reference", () => {
  it("resolves a series named in children", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "HorizontalBarChart",
            labels: ["半导体", "新能源车", "军工"],
            children: ["sector-series"],
          },
          {
            id: "sector-series",
            component: "Series",
            category: "涨跌幅",
            values: [3.2, 2.1, -1.4],
          },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("horizontal-chart").textContent ?? "[]");
    expect(data).toEqual([
      { category: "半导体", 涨跌幅: 3.2 },
      { category: "新能源车", 涨跌幅: 2.1 },
      { category: "军工", 涨跌幅: -1.4 },
    ]);
  });

  it("still handles an inline series", () => {
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "BarChart",
            labels: ["Q1", "Q2"],
            series: [{ component: "Series", category: "Revenue", values: [10, 12] }],
          },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("bar-chart").textContent ?? "[]");
    expect(data).toEqual([
      { category: "Q1", Revenue: 10 },
      { category: "Q2", Revenue: 12 },
    ]);
  });

  it("accepts categories as the axis, not just labels", () => {
    // Taken from a dashboard where both charts came back as a bare heading:
    // the model named the axis `categories` and put the series in `children`,
    // and the renderer read only `labels`. An empty axis then met the
    // no-data guard, so the chart removed itself and left the heading orphaned.
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "HorizontalBarChart",
            categories: ["ARM", "AMAT", "INTC"],
            children: ["semi-series"],
          },
          {
            id: "semi-series",
            component: "Series",
            name: "涨幅 %",
            data: [17.36, 14.97, 10.84],
          },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("horizontal-chart").textContent ?? "[]");
    expect(data).toEqual([
      { category: "ARM", "涨幅 %": 17.36 },
      { category: "AMAT", "涨幅 %": 14.97 },
      { category: "INTC", "涨幅 %": 10.84 },
    ]);
  });

  it("follows references two levels down, to the points", () => {
    // The next payload from the same conversation nested one level further:
    // chart → children:[Series] → children:[Point{label,value}], with no axis
    // on the chart at all. Resolving only the first level left the series with
    // no points, so the chart vanished again — the axis lives on the points.
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "HorizontalBarChart", children: ["s"] },
          { id: "s", component: "Series", name: "涨幅 %", children: ["p1", "p2"] },
          { id: "p1", component: "Point", label: "ARM", value: 17.36 },
          { id: "p2", component: "Point", label: "AMAT", value: 14.97 },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("horizontal-chart").textContent ?? "[]");
    expect(data).toEqual([
      { category: "ARM", "涨幅 %": 17.36 },
      { category: "AMAT", "涨幅 %": 14.97 },
    ]);
  });

  it("follows references to any depth, not a fixed number of levels", () => {
    // Each payload so far nested one level deeper than the last, and each
    // presented identically: a chart that silently rendered nothing. Depth is
    // the model's choice, so resolution has to be recursive rather than
    // counted — this wraps the series in two extra layers it has never used.
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "HorizontalBarChart", children: ["g1"] },
          { id: "g1", component: "Group", children: ["g2"] },
          { id: "g2", component: "Group", children: ["s"] },
          { id: "s", component: "Series", name: "涨幅", children: ["p1"] },
          { id: "p1", component: "Point", label: "ARM", value: 17.36 },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("horizontal-chart").textContent ?? "[]");
    expect(data).toEqual([{ category: "ARM", 涨幅: 17.36 }]);
  });

  it("survives a reference cycle instead of hanging", () => {
    // Model output is untrusted; a self-referencing id must not spin forever.
    render(
      <A2UIRenderer
        body={a2ui([
          { id: "root", component: "HorizontalBarChart", children: ["loop"] },
          { id: "loop", component: "Series", name: "s", children: ["loop", "p"] },
          { id: "p", component: "Point", label: "A", value: 1 },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("horizontal-chart").textContent ?? "[]");
    expect(data).toEqual([{ category: "A", s: 1 }]);
  });

  it("still finds points when the props are named something new", () => {
    // The one class that cannot be solved by resolution: an unrecognised key.
    // The fallback reads any record pairing a label with a number, so a novel
    // spelling degrades to a flattened chart rather than to nothing.
    render(
      <A2UIRenderer
        body={a2ui([
          {
            id: "root",
            component: "BarChart",
            dataPoints: [
              { label: "ARM", value: 17.36 },
              { label: "AMAT", value: 14.97 },
            ],
          },
        ])}
      />,
    );
    const data = JSON.parse(screen.getByTestId("bar-chart").textContent ?? "[]");
    expect(data).toEqual([
      { category: "ARM", value: 17.36 },
      { category: "AMAT", value: 14.97 },
    ]);
  });

  it("renders no chart when the series is genuinely absent", () => {
    render(
      <A2UIRenderer
        body={a2ui([{ id: "root", component: "HorizontalBarChart", labels: ["A", "B"] }])}
      />,
    );
    expect(screen.queryByTestId("horizontal-chart")).toBeNull();
  });
});
