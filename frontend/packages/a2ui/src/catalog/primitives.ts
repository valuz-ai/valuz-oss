import {
  AccessibilityAttributesSchema,
  ActionSchema,
  CheckableSchema,
  ChildListSchema,
  ComponentIdSchema,
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringListSchema,
  DynamicStringSchema,
  DynamicValueSchema,
} from "@a2ui/web_core/v0_9";
import { z } from "zod";

export {
  ActionSchema,
  CheckableSchema,
  ChildListSchema,
  ComponentIdSchema,
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringListSchema,
  DynamicStringSchema,
  DynamicValueSchema,
};

export const commonProps = {
  accessibility: AccessibilityAttributesSchema.optional(),
  weight: z
    .number()
    .positive()
    .optional()
    .describe("Relative flex weight when this component is a direct child of a layout."),
};

export const toneSchema = z
  .enum(["neutral", "brand", "info", "success", "warning", "danger"])
  .default("neutral")
  .optional();

export const sizeSchema = z.enum(["sm", "md", "lg"]).default("md").optional();

export const gapSchema = z.enum(["none", "xs", "sm", "md", "lg", "xl"])
  .default("md")
  .optional();

export const alignSchema = z.enum(["start", "center", "end", "stretch"])
  .default("stretch")
  .optional();

export const justifySchema = z
  .enum(["start", "center", "end", "spaceBetween", "spaceAround", "spaceEvenly"])
  .default("start")
  .optional();

export const optionSchema = z
  .object({
    label: DynamicStringSchema,
    value: z.string(),
    description: DynamicStringSchema.optional(),
    disabled: DynamicBooleanSchema.optional(),
  })
  .strict();

export const fieldProps = {
  ...commonProps,
  ...CheckableSchema.shape,
  label: DynamicStringSchema,
  description: DynamicStringSchema.optional(),
  disabled: DynamicBooleanSchema.default(false).optional(),
  required: DynamicBooleanSchema.default(false).optional(),
};

export const chartSeriesSchema = z
  .object({
    key: z.string(),
    label: DynamicStringSchema.optional(),
    color: z.string().optional(),
    stack: z.string().optional(),
    curve: z.enum(["linear", "monotone", "step"]).default("monotone").optional(),
  })
  .strict();

export const chartCommonProps = {
  ...commonProps,
  title: DynamicStringSchema.optional(),
  description: DynamicStringSchema.optional(),
  data: DynamicValueSchema.describe(
    "Array of records, or a data-model binding resolving to an array of records.",
  ),
  height: z.number().int().min(160).max(720).default(300).optional(),
  showLegend: DynamicBooleanSchema.default(true).optional(),
  showGrid: DynamicBooleanSchema.default(true).optional(),
  showTooltip: DynamicBooleanSchema.default(true).optional(),
};
