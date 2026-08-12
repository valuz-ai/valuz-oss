import { describe, expect, it } from "vitest";

import {
  VALUZ_BASE_CATALOG_ID,
  valuzBaseComponentApis,
  valuzBaseComponentNames,
} from "./index";
import { createValuzMessageProcessor } from "../react/catalog";
import { valuzBaseComponents } from "../react/catalog";

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
});
