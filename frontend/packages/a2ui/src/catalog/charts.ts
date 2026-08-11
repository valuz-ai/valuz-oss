import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringSchema,
  DynamicValueSchema,
  chartCommonProps,
  chartSeriesSchema,
  commonProps,
} from "./primitives";

const chartFrameProps = {
  ...commonProps,
  title: DynamicStringSchema.optional(),
  description: DynamicStringSchema.optional(),
  height: z.number().int().min(64).max(720).optional(),
};

const cartesianProps = {
  ...chartCommonProps,
  xKey: z.string().describe("Record key used for the horizontal category or time axis."),
  series: z.array(chartSeriesSchema).min(1).max(8),
  showAxes: DynamicBooleanSchema.default(true).optional(),
};

export const LineChartApi = {
  name: "LineChart",
  schema: z
    .object({
      ...cartesianProps,
      showDots: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Compare one or more numeric series across an ordered horizontal axis."),
} satisfies ComponentApi;

export const AreaChartApi = {
  name: "AreaChart",
  schema: z
    .object({
      ...cartesianProps,
      stacked: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Show magnitude and trend over an ordered axis using translucent filled series."),
} satisfies ComponentApi;

export const BarChartApi = {
  name: "BarChart",
  schema: z
    .object({
      ...cartesianProps,
      stacked: DynamicBooleanSchema.default(false).optional(),
      barRadius: z.number().int().min(0).max(12).default(4).optional(),
    })
    .strict()
    .describe("Compare categorical values using vertical bars, optionally stacked."),
} satisfies ComponentApi;

export const HorizontalBarChartApi = {
  name: "HorizontalBarChart",
  schema: z
    .object({
      ...chartCommonProps,
      categoryKey: z.string(),
      series: z.array(chartSeriesSchema).min(1).max(8),
      stacked: DynamicBooleanSchema.default(false).optional(),
      showAxes: DynamicBooleanSchema.default(true).optional(),
      barRadius: z.number().int().min(0).max(12).default(4).optional(),
    })
    .strict()
    .describe("Compare ranked or long-labelled categories using horizontal bars."),
} satisfies ComponentApi;

export const PieChartApi = {
  name: "PieChart",
  schema: z
    .object({
      ...chartCommonProps,
      nameKey: z.string(),
      valueKey: z.string(),
      showLabels: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Show a small part-to-whole comparison as a filled pie chart."),
} satisfies ComponentApi;

export const DonutChartApi = {
  name: "DonutChart",
  schema: z
    .object({
      ...chartCommonProps,
      nameKey: z.string(),
      valueKey: z.string(),
      innerRadius: z.number().min(0.25).max(0.8).default(0.56).optional(),
      showLabels: DynamicBooleanSchema.default(false).optional(),
      centerLabel: DynamicStringSchema.optional(),
    })
    .strict()
    .describe("Show part-to-whole values as a donut with an optional center label."),
} satisfies ComponentApi;

const comboSeriesSchema = chartSeriesSchema.extend({
  type: z.enum(["bar", "line", "area"]),
  axis: z.enum(["left", "right"]).default("left").optional(),
});

export const ComboChartApi = {
  name: "ComboChart",
  schema: z
    .object({
      ...chartCommonProps,
      xKey: z.string(),
      series: z.array(comboSeriesSchema).min(2).max(8),
      showAxes: DynamicBooleanSchema.default(true).optional(),
      rightAxis: DynamicBooleanSchema.default(false).optional(),
      barRadius: z.number().int().min(0).max(12).default(4).optional(),
    })
    .strict()
    .describe("Overlay bar, line, and area series, with optional stacking and dual axes."),
} satisfies ComponentApi;

export const FunnelChartApi = {
  name: "FunnelChart",
  schema: z
    .object({
      ...chartCommonProps,
      nameKey: z.string(),
      valueKey: z.string(),
      showLabels: DynamicBooleanSchema.default(true).optional(),
    })
    .strict()
    .describe("Show sequential stages whose values narrow through a process."),
} satisfies ComponentApi;

export const TreemapChartApi = {
  name: "TreemapChart",
  schema: z
    .object({
      ...chartCommonProps,
      nameKey: z.string(),
      valueKey: z.string(),
    })
    .strict()
    .describe("Compare hierarchical or categorical magnitude using nested rectangles."),
} satisfies ComponentApi;

export const SankeyChartApi = {
  name: "SankeyChart",
  schema: z
    .object({
      ...chartFrameProps,
      data: DynamicValueSchema.describe(
        "Object with nodes [{name}] and links [{source,target,value}], or a binding resolving to it.",
      ),
      nodeWidth: z.number().int().min(4).max(40).default(12).optional(),
      nodePadding: z.number().int().min(4).max(48).default(18).optional(),
      showTooltip: DynamicBooleanSchema.default(true).optional(),
    })
    .strict()
    .describe("Show weighted flow between nodes as proportional connected bands."),
} satisfies ComponentApi;

export const HeatmapChartApi = {
  name: "HeatmapChart",
  schema: z
    .object({
      ...chartFrameProps,
      data: DynamicValueSchema.describe(
        "Array of records containing horizontal category, vertical category, and numeric value keys.",
      ),
      xKey: z.string(),
      yKey: z.string(),
      valueKey: z.string(),
      min: DynamicNumberSchema.optional(),
      max: DynamicNumberSchema.optional(),
      showValues: DynamicBooleanSchema.default(true).optional(),
    })
    .strict()
    .describe("Compare values across two categorical dimensions using color intensity."),
} satisfies ComponentApi;

export const GaugeChartApi = {
  name: "GaugeChart",
  schema: z
    .object({
      ...chartFrameProps,
      value: DynamicNumberSchema,
      min: DynamicNumberSchema.default(0).optional(),
      max: DynamicNumberSchema.default(100).optional(),
      unit: DynamicStringSchema.optional(),
      startAngle: z.number().min(-360).max(360).default(210).optional(),
      endAngle: z.number().min(-360).max(360).default(-30).optional(),
    })
    .strict()
    .describe("Show one bounded value against a minimum and maximum on a radial gauge."),
} satisfies ComponentApi;

export const SparklineChartApi = {
  name: "SparklineChart",
  schema: z
    .object({
      ...chartFrameProps,
      data: DynamicValueSchema.describe("Ordered records, or a binding resolving to them."),
      xKey: z.string(),
      series: z.array(chartSeriesSchema).min(1).max(4),
      showTooltip: DynamicBooleanSchema.default(true).optional(),
      showDots: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Show a compact trend without axes, grid, or other dashboard chrome."),
} satisfies ComponentApi;

export const RadarChartApi = {
  name: "RadarChart",
  schema: z
    .object({
      ...chartCommonProps,
      categoryKey: z.string(),
      series: z.array(chartSeriesSchema).min(1).max(8),
      domainMax: DynamicNumberSchema.optional(),
    })
    .strict()
    .describe("Compare multivariate profiles across a shared set of dimensions."),
} satisfies ComponentApi;

export const RadialChartApi = {
  name: "RadialChart",
  schema: z
    .object({
      ...chartCommonProps,
      nameKey: z.string(),
      valueKey: z.string(),
      max: DynamicNumberSchema.default(100).optional(),
      startAngle: z.number().min(-360).max(360).default(90).optional(),
      endAngle: z.number().min(-360).max(360).default(-270).optional(),
    })
    .strict()
    .describe("Show bounded progress or category values as concentric radial bars."),
} satisfies ComponentApi;

export const ScatterChartApi = {
  name: "ScatterChart",
  schema: z
    .object({
      ...chartCommonProps,
      xKey: z.string(),
      yKey: z.string(),
      sizeKey: z.string().optional(),
      seriesName: z.string().optional(),
    })
    .strict()
    .describe("Reveal correlation, clusters, and outliers between two numeric dimensions."),
} satisfies ComponentApi;

export const chartApis = [
  LineChartApi,
  AreaChartApi,
  BarChartApi,
  HorizontalBarChartApi,
  PieChartApi,
  DonutChartApi,
  ComboChartApi,
  FunnelChartApi,
  TreemapChartApi,
  SankeyChartApi,
  HeatmapChartApi,
  GaugeChartApi,
  SparklineChartApi,
  RadarChartApi,
  RadialChartApi,
  ScatterChartApi,
] as const;
