import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringSchema,
  DynamicValueSchema,
  chartCommonProps,
  chartPaletteSchema,
  chartSeriesSchema,
  commonProps,
} from "./primitives";

const chartFrameProps = {
  ...commonProps,
  title: DynamicStringSchema.optional(),
  description: DynamicStringSchema.optional(),
  height: z.number().int().min(64).max(720).optional(),
  palette: chartPaletteSchema.default("ocean").optional(),
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
    .describe("Use for one or more numeric trends across an ordered axis; assign semantic series roles when actuals, estimates, or benchmarks differ."),
} satisfies ComponentApi;

export const AreaChartApi = {
  name: "AreaChart",
  schema: z
    .object({
      ...cartesianProps,
      stacked: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Use when both magnitude and ordered trend matter; stack only when the filled series form a meaningful whole."),
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
    .describe("Use for categorical magnitude comparisons; stack only for additive parts of the same total."),
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
    .describe("Use for ranked or long-labelled categorical comparisons where exact ordering matters."),
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
    .describe("Use for one small part-to-whole set with few distinct categories; prefer a table or bars when precise comparison matters."),
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
    .describe("Use for one small part-to-whole set when a meaningful total belongs in the center; avoid many or near-equal categories."),
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
    .describe("Use for related bar, line, or area measures on one ordered axis; enable a right axis only for a clearly different unit."),
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
    .describe("Use for a numeric matrix across two categorical dimensions with a meaningful shared intensity scale."),
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
    .describe("Use for one genuinely bounded value against a meaningful minimum and maximum, not for an uncalibrated confidence score."),
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
    .describe("Use for observations with two numeric dimensions to reveal correlation, clusters, and outliers; use size only for a third quantitative measure."),
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
