import { describe, expect, it } from "vitest";

import {
  C1_CHART_PALETTES,
  CHART_PALETTES,
  getDistributedChartColors,
  resolveChartSeriesVisual,
  VALUZ_CHART_PALETTES,
} from "./chart-theme";

describe("analytical chart theme", () => {
  it("copies the six curated C1 palettes exactly", () => {
    expect(Object.keys(C1_CHART_PALETTES)).toEqual([
      "ocean",
      "orchid",
      "emerald",
      "spectrum",
      "sunset",
      "vivid",
    ]);
    expect(C1_CHART_PALETTES.ocean).toEqual([
      "#0D47A1", "#1565C0", "#1976D2", "#1E88E5", "#2196F3", "#42A5F5",
      "#64B5F6", "#90CAF9", "#BBDEFB", "#E3F2FD", "#EFF8FF",
    ]);
    expect(C1_CHART_PALETTES.vivid).toEqual([
      "#FF595E", "#FF924C", "#FFCA3A", "#C5CA30", "#8AC926", "#36949D",
      "#1982C4", "#4267AC", "#565AA0", "#6A4C93", "#63438F",
    ]);
    expect(Object.values(C1_CHART_PALETTES).every((palette) => palette.length === 11))
      .toBe(true);
  });

  it("adds steel and amber as two Valuz 11-color palettes", () => {
    expect(Object.keys(VALUZ_CHART_PALETTES)).toEqual(["steel", "amber"]);
    expect(Object.keys(CHART_PALETTES)).toHaveLength(8);
    expect(VALUZ_CHART_PALETTES.steel).toHaveLength(11);
    expect(VALUZ_CHART_PALETTES.amber).toHaveLength(11);
    expect(getDistributedChartColors("steel", 1)).toEqual([
      "var(--va2-chart-steel-6, #687B8F)",
    ]);
    expect(getDistributedChartColors("amber", 1)).toEqual([
      "var(--va2-chart-amber-6, #D88700)",
    ]);
  });

  it("distributes colors from the middle of a C1 palette", () => {
    expect(getDistributedChartColors("vivid", 0)).toEqual([]);
    expect(getDistributedChartColors("vivid", 1)).toEqual([
      "var(--va2-chart-vivid-6, #36949D)",
    ]);
    expect(getDistributedChartColors("vivid", 2)).toEqual([
      "var(--va2-chart-vivid-5, #8AC926)",
      "var(--va2-chart-vivid-7, #1982C4)",
    ]);
    expect(getDistributedChartColors("vivid", 4)).toEqual([
      "var(--va2-chart-vivid-5, #8AC926)",
      "var(--va2-chart-vivid-6, #36949D)",
      "var(--va2-chart-vivid-7, #1982C4)",
      "var(--va2-chart-vivid-8, #4267AC)",
    ]);
  });

  it("uses the selected palette when a series has no semantic role", () => {
    const colors = getDistributedChartColors("sunset", 3);
    expect(resolveChartSeriesVisual(undefined, 1, colors).color).toBe(
      "var(--va2-chart-sunset-6, #CC4678)",
    );
  });

  it("uses the selected palette for actual while preserving comparison semantics", () => {
    const colors = getDistributedChartColors("orchid", 3);
    expect(resolveChartSeriesVisual("actual", 1, colors)).toMatchObject({
      color: "var(--va2-chart-orchid-6, #883BD5)",
      strokeOpacity: 1,
    });
    expect(resolveChartSeriesVisual("estimate", 0)).toMatchObject({
      color: "var(--va2-chart-estimate)",
      strokeDasharray: "6 4",
    });
    expect(resolveChartSeriesVisual("benchmark", 0)).toMatchObject({
      color: "var(--va2-chart-benchmark)",
      strokeDasharray: "3 3",
    });
  });
});
