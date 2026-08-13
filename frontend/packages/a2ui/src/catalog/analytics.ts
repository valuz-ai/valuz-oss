import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  ActionSchema,
  ChildListSchema,
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringSchema,
  DynamicValueSchema,
  commonProps,
  toneSchema,
} from "./primitives";

const trendSchema = z.enum(["up", "down", "flat"]);
const metricSchema = z.object({
  label: DynamicStringSchema,
  value: DynamicStringSchema,
  url: DynamicStringSchema.optional(),
  delta: DynamicStringSchema.optional(),
  trend: trendSchema.optional(),
  description: DynamicStringSchema.optional(),
  tone: toneSchema,
}).strict();

export const MetricApi = {
  name: "Metric",
  schema: z.object({ ...commonProps, ...metricSchema.shape }).strict()
    .describe("Show one primary value with a label, optional change, direction, and context."),
} satisfies ComponentApi;

export const MetricGroupApi = {
  name: "MetricGroup",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), metrics: z.array(metricSchema).min(1).max(12), columns: z.number().int().min(1).max(6).default(4).optional() }).strict()
    .describe("Compare a cohesive set of headline metrics in a responsive grid."),
} satisfies ComponentApi;

const dataColumnSchema = z.object({
  key: z.string(),
  label: DynamicStringSchema,
  align: z.enum(["left", "center", "right"]).default("left").optional(),
  format: z.enum(["text", "number", "percent", "currency", "date", "change"]).default("text").optional(),
  width: z.number().int().min(56).max(640).optional(),
}).strict();

export const DataTableApi = {
  name: "DataTable",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), columns: z.array(dataColumnSchema).min(1).max(24), rows: DynamicValueSchema, linkKey: z.string().optional(), caption: DynamicStringSchema.optional(), density: z.enum(["compact", "comfortable"]).default("comfortable").optional(), stickyHeader: DynamicBooleanSchema.default(false).optional(), maxHeight: z.number().int().min(120).max(960).optional() }).strict()
    .describe("Display dense analytical records with explicit formats and stable alignment. Set linkKey when the first visible cell names a navigable entity."),
} satisfies ComponentApi;

export const ComparisonTableApi = {
  name: "ComparisonTable",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), subjectKey: z.string(), columns: z.array(dataColumnSchema).min(2).max(16), rows: DynamicValueSchema, linkKey: z.string().optional(), highlightKey: z.string().optional() }).strict()
    .describe("Compare peers or alternatives across the same metrics, with an optional highlighted subject. Set linkKey when each subject navigates to its entity page."),
} satisfies ComponentApi;

export const MatrixTableApi = {
  name: "MatrixTable",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), rowKey: z.string(), columns: z.array(dataColumnSchema).min(2).max(16), rows: DynamicValueSchema, linkKey: z.string().optional(), min: DynamicNumberSchema.optional(), max: DynamicNumberSchema.optional(), showValues: DynamicBooleanSchema.default(true).optional() }).strict()
    .describe("Show a two-dimensional numeric matrix with comparable color intensity and optional values. Set linkKey when row labels navigate to an entity page."),
} satisfies ComponentApi;

const descriptionItemSchema = z.object({ label: DynamicStringSchema, value: DynamicStringSchema, description: DynamicStringSchema.optional(), tone: toneSchema }).strict();
export const DescriptionListApi = {
  name: "DescriptionList",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), items: z.array(descriptionItemSchema).min(1).max(24), columns: z.number().int().min(1).max(4).default(2).optional() }).strict()
    .describe("Present compact label-value facts with optional explanatory detail."),
} satisfies ComponentApi;

const timelineItemSchema = z.object({ time: DynamicStringSchema, title: DynamicStringSchema, url: DynamicStringSchema.optional(), description: DynamicStringSchema.optional(), status: z.enum(["past", "current", "future", "warning"]).default("past").optional(), meta: DynamicStringSchema.optional() }).strict();
export const TimelineApi = {
  name: "Timeline",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), items: z.array(timelineItemSchema).min(1).max(40), compact: DynamicBooleanSchema.default(false).optional() }).strict()
    .describe("Explain a chronological sequence of events, changes, or future milestones."),
} satisfies ComponentApi;

export const DiffViewApi = {
  name: "DiffView",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), beforeLabel: DynamicStringSchema.default("Before").optional(), afterLabel: DynamicStringSchema.default("After").optional(), before: DynamicStringSchema, after: DynamicStringSchema, mode: z.enum(["split", "unified"]).default("split").optional() }).strict()
    .describe("Compare two revisions of concise text or structured reasoning."),
} satisfies ComponentApi;

export const CitationApi = {
  name: "Citation",
  schema: z.object({ ...commonProps, index: z.union([z.string(), z.number()]), label: DynamicStringSchema, url: DynamicStringSchema.optional(), excerpt: DynamicStringSchema.optional() }).strict()
    .describe("Attach a numbered, inspectable source citation to a claim."),
} satisfies ComponentApi;

const sourceSchema = z.object({ title: DynamicStringSchema, publisher: DynamicStringSchema.optional(), url: DynamicStringSchema.optional(), date: DynamicStringSchema.optional(), type: DynamicStringSchema.optional() }).strict();
export const SourceListApi = {
  name: "SourceList",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), sources: z.array(sourceSchema).min(1).max(40), compact: DynamicBooleanSchema.default(false).optional() }).strict()
    .describe("List the documents, datasets, and links that support a result."),
} satisfies ComponentApi;

export const ProvenanceBarApi = {
  name: "ProvenanceBar",
  schema: z.object({ ...commonProps, source: DynamicStringSchema, asOf: DynamicStringSchema, basis: DynamicStringSchema.optional(), freshness: z.enum(["live", "recent", "stale", "unknown"]).default("unknown").optional() }).strict()
    .describe("State where data came from, when it was current, and the comparison basis."),
} satisfies ComponentApi;

export const DataStateApi = {
  name: "DataState",
  schema: z.object({ ...commonProps, state: z.enum(["loading", "empty", "partial", "stale", "error", "ready"]), title: DynamicStringSchema, description: DynamicStringSchema.optional(), progress: DynamicNumberSchema.optional() }).strict()
    .describe("Explain loading, empty, partial, stale, error, or ready data without inventing results."),
} satisfies ComponentApi;

const controlItemSchema = z.object({ label: DynamicStringSchema, value: z.string(), active: DynamicBooleanSchema.default(false).optional(), disabled: DynamicBooleanSchema.default(false).optional(), action: ActionSchema.optional() }).strict();
export const ControlBarApi = {
  name: "ControlBar",
  schema: z.object({ ...commonProps, label: DynamicStringSchema.optional(), items: z.array(controlItemSchema).min(1).max(12), align: z.enum(["start", "end", "spaceBetween"]).default("start").optional() }).strict()
    .describe("Offer compact time-range, scenario, or view controls above analytical content."),
} satisfies ComponentApi;

export const DataInspectorApi = {
  name: "DataInspector",
  schema: z.object({ ...commonProps, title: DynamicStringSchema.optional(), data: DynamicValueSchema, collapsedDepth: z.number().int().min(0).max(8).default(2).optional() }).strict()
    .describe("Inspect the structured data behind a generated component in a readable developer view."),
} satisfies ComponentApi;

export const TableChartToggleApi = {
  name: "TableChartToggle",
  schema: z.object({ ...commonProps, chartChild: z.string(), tableChild: z.string(), defaultView: z.enum(["chart", "table"]).default("chart").optional(), chartLabel: DynamicStringSchema.default("Chart").optional(), tableLabel: DynamicStringSchema.default("Table").optional() }).strict()
    .describe("Let users switch between a chart and its exact tabular data without duplication."),
} satisfies ComponentApi;

export const SynchronizedChartGroupApi = {
  name: "SynchronizedChartGroup",
  schema: z.object({ ...commonProps, children: ChildListSchema, title: DynamicStringSchema.optional(), columns: z.number().int().min(1).max(3).default(1).optional(), syncKey: z.string().optional() }).strict()
    .describe("Arrange related time-series panels that share an x-axis and analytical context."),
} satisfies ComponentApi;

export const analyticsApis = [MetricApi, MetricGroupApi, DataTableApi, ComparisonTableApi, MatrixTableApi, DescriptionListApi, TimelineApi, DiffViewApi, CitationApi, SourceListApi, ProvenanceBarApi, DataStateApi, ControlBarApi, DataInspectorApi, TableChartToggleApi, SynchronizedChartGroupApi] as const;
