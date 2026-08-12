import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import { DynamicBooleanSchema, DynamicNumberSchema, DynamicStringSchema, DynamicValueSchema, chartCommonProps, chartSeriesSchema, commonProps } from "./primitives";

const frameProps = { ...commonProps, title: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), height: z.number().int().min(120).max(720).default(280).optional() };

export const TimeSeriesChartApi = {
  name: "TimeSeriesChart",
  schema: z.object({ ...chartCommonProps, xKey: z.string(), series: z.array(chartSeriesSchema).min(1).max(6), showAxes: DynamicBooleanSchema.default(true).optional(), normalize: DynamicBooleanSchema.default(false).optional(), referenceValue: DynamicNumberSchema.optional() }).strict()
    .describe("Plot one or more time-indexed series with optional rebasing and a reference level."),
} satisfies ComponentApi;

export const CandlestickChartApi = {
  name: "CandlestickChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, timeKey: z.string().default("time").optional(), openKey: z.string().default("open").optional(), highKey: z.string().default("high").optional(), lowKey: z.string().default("low").optional(), closeKey: z.string().default("close").optional(), volumeKey: z.string().optional(), showVolume: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Render OHLC candlesticks with an optional aligned volume band for market price analysis."),
} satisfies ComponentApi;

export const WaterfallChartApi = {
  name: "WaterfallChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, nameKey: z.string(), valueKey: z.string(), totalKey: z.string().optional(), showValues: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Explain how positive and negative contributors bridge an opening value to a closing total."),
} satisfies ComponentApi;

export const RangeChartApi = {
  name: "RangeChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, categoryKey: z.string(), minKey: z.string(), maxKey: z.string(), valueKey: z.string().optional(), targetKey: z.string().optional() }).strict()
    .describe("Compare values and targets within category-specific low-to-high ranges."),
} satisfies ComponentApi;

export const HistogramChartApi = {
  name: "HistogramChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, valueKey: z.string().optional(), bins: z.number().int().min(4).max(40).default(12).optional(), showCurve: DynamicBooleanSchema.default(false).optional() }).strict()
    .describe("Show the distribution of numeric observations across equal-width bins."),
} satisfies ComponentApi;

export const BoxPlotChartApi = {
  name: "BoxPlotChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, categoryKey: z.string(), minKey: z.string(), q1Key: z.string(), medianKey: z.string(), q3Key: z.string(), maxKey: z.string() }).strict()
    .describe("Compare distributions by minimum, quartiles, median, and maximum."),
} satisfies ComponentApi;

export const BulletChartApi = {
  name: "BulletChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, labelKey: z.string(), valueKey: z.string(), targetKey: z.string(), maxKey: z.string().optional(), unit: DynamicStringSchema.optional() }).strict()
    .describe("Compare actual values against targets in a compact, information-dense row."),
} satisfies ComponentApi;

export const CalendarHeatmapChartApi = {
  name: "CalendarHeatmapChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, dateKey: z.string(), valueKey: z.string(), min: DynamicNumberSchema.optional(), max: DynamicNumberSchema.optional(), weeks: z.number().int().min(4).max(53).default(26).optional() }).strict()
    .describe("Reveal daily intensity and persistence across a calendar-like weekly grid."),
} satisfies ComponentApi;

export const NetworkGraphApi = {
  name: "NetworkGraph",
  schema: z.object({ ...frameProps, data: DynamicValueSchema.describe("Object with nodes [{id,label,value?,group?}] and links [{source,target,weight?}]."), labelKey: z.string().default("label").optional(), valueKey: z.string().default("value").optional(), groupKey: z.string().default("group").optional(), showLabels: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Show entities and weighted relationships as a compact network graph."),
} satisfies ComponentApi;

export const advancedChartApis = [TimeSeriesChartApi, CandlestickChartApi, WaterfallChartApi, RangeChartApi, HistogramChartApi, BoxPlotChartApi, BulletChartApi, CalendarHeatmapChartApi, NetworkGraphApi] as const;
