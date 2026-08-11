import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  DynamicBooleanSchema,
  DynamicNumberSchema,
  chartCommonProps,
  chartSeriesSchema,
} from "./primitives";

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
      donut: DynamicBooleanSchema.default(true).optional(),
      showLabels: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Show a small part-to-whole comparison as a pie or donut chart."),
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
  RadarChartApi,
  RadialChartApi,
  ScatterChartApi,
] as const;
