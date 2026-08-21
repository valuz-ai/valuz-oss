import { describe, expect, it } from "vitest";

import {
  VALUZ_BASE_CATALOG_ID,
  valuzBaseComponentApis,
  valuzBaseComponentNames,
} from "./index";
import { createValuzMessageProcessor } from "../react/catalog";
import { valuzBaseComponents } from "../react/catalog";
import { TimeSeriesChartApi } from "./advanced-charts";
import { LineChartApi } from "./charts";
import { TagBlockApi } from "./content";
import { describeA2UIComponent } from "./describe";

describe("Valuz A2UI base catalog", () => {
  it("has a stable versioned id and unique component names", () => {
    expect(VALUZ_BASE_CATALOG_ID).toBe("https://valuz.io/a2ui/catalogs/base/v1");
    expect(new Set(valuzBaseComponentNames).size).toBe(valuzBaseComponentNames.length);
    expect(valuzBaseComponentNames).toEqual(
      expect.arrayContaining([
        "Stack",
        "Grid",
        "Card",
        "Tabs",
        "Accordion",
        "Steps",
        "Carousel",
        "TextContent",
        "Markdown",
        "ImageGallery",
        "Table",
        "CodeBlock",
        "Button",
        "FollowUpBlock",
        "Input",
        "Select",
        "CheckboxGroup",
        "SwitchGroup",
        "LineChart",
        "BarChart",
        "PieChart",
        "DonutChart",
        "ComboChart",
        "FunnelChart",
        "TreemapChart",
        "SankeyChart",
        "HeatmapChart",
        "GaugeChart",
        "SparklineChart",
        "ScatterChart",
        "MetricGroup",
        "DataTable",
        "ProvenanceBar",
        "TimeSeriesChart",
        "CandlestickChart",
        "WaterfallChart",
        "NetworkGraph",
      ]),
    );
    expect(valuzBaseComponentNames).toHaveLength(76);
    expect(valuzBaseComponents.map((component) => component.name)).toEqual(
      valuzBaseComponentNames,
    );
  });

  it("defines a strict schema and description for every component", () => {
    for (const component of valuzBaseComponentApis) {
      expect(component.schema.description, component.name).toBeTruthy();
      const result = component.schema.safeParse({ unexpected: true });
      expect(result.success, component.name).toBe(false);
    }
  });

  it("advertises the catalog as an inline A2UI v0.9.1 capability", () => {
    const capabilities = createValuzMessageProcessor().getClientCapabilities({
      includeInlineCatalogs: true,
    });
    const current = capabilities["v0.9.1"]!;

    expect(current.supportedCatalogIds).toEqual([VALUZ_BASE_CATALOG_ID]);
    expect(current.inlineCatalogs?.[0]?.catalogId).toBe(VALUZ_BASE_CATALOG_ID);
    expect(Object.keys(current.inlineCatalogs?.[0]?.components ?? {})).toHaveLength(
      valuzBaseComponentNames.length,
    );
  });

  it("accepts curated C1 palettes and semantic roles but rejects arbitrary colors", () => {
    const common = {
      data: [{ period: "Q1", actual: 1, estimate: 2 }],
      xKey: "period",
    };
    expect(LineChartApi.schema.safeParse({
      ...common,
      palette: "vivid",
      series: [
        { key: "actual", role: "actual" },
        { key: "estimate", role: "estimate" },
      ],
    }).success).toBe(true);
    expect(LineChartApi.schema.safeParse({
      ...common,
      palette: "custom-rainbow",
      series: [{ key: "actual" }],
    }).success).toBe(false);
    expect(LineChartApi.schema.safeParse({
      ...common,
      series: [{ key: "actual", color: "#ff00ff" }],
    }).success).toBe(false);
  });

  it("describes palette through the shared compiler vocabulary", () => {
    const description = describeA2UIComponent(LineChartApi);

    expect(description).toContain("palette?: palette");
    expect(description).toContain(
      "series: array<{key,label?,url?,role?,stack?,curve?}>",
    );
    expect(description).toContain("Series entries use label, never name");
    expect(description).not.toContain('palette?: "ocean"|');
  });

  it("describes TagBlock entries instead of an ambiguous array", () => {
    const description = describeA2UIComponent(TagBlockApi);

    expect(description).toContain("tags: array<{label,tone?}>");
  });

  it("allows a live slot to replace TimeSeriesChart data and series together", () => {
    expect(TimeSeriesChartApi.schema.safeParse({
      data: { path: "/data/kline/data" },
      xKey: "date",
      series: { path: "/data/kline/series" },
      normalize: true,
      referenceValue: 100,
    }).success).toBe(true);
  });
});
