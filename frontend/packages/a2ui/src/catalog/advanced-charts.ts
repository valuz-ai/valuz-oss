import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import { DynamicBooleanSchema, DynamicNumberSchema, DynamicStringSchema, DynamicValueSchema, chartCommonProps, chartPaletteSchema, dynamicChartSeriesSchema, commonProps } from "./primitives";

const frameProps = {
  ...commonProps,
  title: DynamicStringSchema.optional(),
  description: DynamicStringSchema.optional(),
  height: z.number().int().min(120).max(720).default(280).optional(),
  palette: chartPaletteSchema.default("ocean").optional(),
};

export const TimeSeriesChartApi = {
  name: "TimeSeriesChart",
  schema: z.object({ ...chartCommonProps, xKey: z.string(), series: dynamicChartSeriesSchema, showAxes: DynamicBooleanSchema.default(true).optional(), normalize: DynamicBooleanSchema.default(false).optional(), referenceValue: DynamicNumberSchema.optional() }).strict()
    .describe('Use for time-indexed actual, estimate, benchmark, or market-price series. Use this component, not LineChart, for relative performance: set normalize:true and referenceValue:100 when comparing series from a common base. Series entries use {key,label?,role?}; use label, never name. When a live slot provides both data and series, bind both properties to that slot so refreshes cannot change field keys independently.'),
} satisfies ComponentApi;

export const CandlestickChartApi = {
  name: "CandlestickChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, timeKey: z.string().default("time").optional(), openKey: z.string().default("open").optional(), highKey: z.string().default("high").optional(), lowKey: z.string().default("low").optional(), closeKey: z.string().default("close").optional(), volumeKey: z.string().optional(), showVolume: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Use only with real OHLC observations for market price analysis; market direction styling comes from the host theme."),
} satisfies ComponentApi;

export const WaterfallChartApi = {
  name: "WaterfallChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, nameKey: z.string(), valueKey: z.string(), totalKey: z.string().optional(), showValues: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Use for additive positive and negative contributors that reconcile an opening reference to a closing total."),
} satisfies ComponentApi;

export const RangeChartApi = {
  name: "RangeChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, categoryKey: z.string(), minKey: z.string(), maxKey: z.string(), valueKey: z.string().optional(), targetKey: z.string().optional() }).strict()
    .describe("Use when each category has a meaningful low-to-high interval and optional current value or target."),
} satisfies ComponentApi;

export const HistogramChartApi = {
  name: "HistogramChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, valueKey: z.string().optional(), bins: z.number().int().min(4).max(40).default(12).optional(), showCurve: DynamicBooleanSchema.default(false).optional() }).strict()
    .describe("Use for a sufficiently large set of raw numeric observations whose distribution and tails matter."),
} satisfies ComponentApi;

export const BoxPlotChartApi = {
  name: "BoxPlotChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, categoryKey: z.string(), minKey: z.string(), q1Key: z.string(), medianKey: z.string(), q3Key: z.string(), maxKey: z.string() }).strict()
    .describe("Compare distributions by minimum, quartiles, median, and maximum."),
} satisfies ComponentApi;

export const BulletChartApi = {
  name: "BulletChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, labelKey: z.string(), valueKey: z.string(), targetKey: z.string(), maxKey: z.string().optional(), unit: DynamicStringSchema.optional() }).strict()
    .describe("Use for compact actual-versus-target comparison when both values share the same scale and unit."),
} satisfies ComponentApi;

export const CalendarHeatmapChartApi = {
  name: "CalendarHeatmapChart",
  schema: z.object({ ...frameProps, data: DynamicValueSchema, dateKey: z.string(), valueKey: z.string(), min: DynamicNumberSchema.optional(), max: DynamicNumberSchema.optional(), weeks: z.number().int().min(4).max(53).default(26).optional() }).strict()
    .describe("Use for dated daily observations where intensity, persistence, and gaps across weeks matter."),
} satisfies ComponentApi;

export const NetworkGraphApi = {
  name: "NetworkGraph",
  schema: z.object({ ...frameProps, data: DynamicValueSchema.describe("Object with nodes [{id,label,value?,group?}] and links [{source,target,weight?}]."), labelKey: z.string().default("label").optional(), valueKey: z.string().default("value").optional(), groupKey: z.string().default("group").optional(), palette: chartPaletteSchema.default("vivid").optional(), showLabels: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Use when entities and weighted links are themselves the analysis; do not use it as decorative navigation."),
} satisfies ComponentApi;

export const advancedChartApis = [TimeSeriesChartApi, CandlestickChartApi, WaterfallChartApi, RangeChartApi, HistogramChartApi, BoxPlotChartApi, BulletChartApi, CalendarHeatmapChartApi, NetworkGraphApi] as const;
