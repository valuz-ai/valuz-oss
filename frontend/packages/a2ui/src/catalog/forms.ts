import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  ActionSchema,
  ChildListSchema,
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringListSchema,
  DynamicStringSchema,
  commonProps,
  fieldProps,
  optionSchema,
} from "./primitives";

export const FormApi = {
  name: "Form",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      submit: ActionSchema.optional(),
      submitLabel: DynamicStringSchema.optional(),
      disabled: DynamicBooleanSchema.default(false).optional(),
      layout: z.enum(["vertical", "inline"]).default("vertical").optional(),
    })
    .strict()
    .describe("Group editable controls and optionally submit their bound data as one action."),
} satisfies ComponentApi;

export const InputApi = {
  name: "Input",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringSchema,
      placeholder: DynamicStringSchema.optional(),
      type: z.enum(["text", "email", "password", "search", "tel", "url"]).default("text").optional(),
      autocomplete: z.string().optional(),
    })
    .strict()
    .describe("Collect one line of text with two-way A2UI data binding and validation."),
} satisfies ComponentApi;

export const TextAreaApi = {
  name: "TextArea",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringSchema,
      placeholder: DynamicStringSchema.optional(),
      rows: z.number().int().min(2).max(20).default(4).optional(),
      maxLength: z.number().int().positive().optional(),
    })
    .strict()
    .describe("Collect longer text with two-way A2UI data binding and validation."),
} satisfies ComponentApi;

export const SelectApi = {
  name: "Select",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringSchema,
      options: z.array(optionSchema).min(1),
      placeholder: DynamicStringSchema.optional(),
    })
    .strict()
    .describe("Choose one value from a compact list of labelled options."),
} satisfies ComponentApi;

export const RadioGroupApi = {
  name: "RadioGroup",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringSchema,
      options: z.array(optionSchema).min(1),
      orientation: z.enum(["vertical", "horizontal"]).default("vertical").optional(),
    })
    .strict()
    .describe("Choose exactly one visible option when comparison context matters."),
} satisfies ComponentApi;

export const CheckboxGroupApi = {
  name: "CheckboxGroup",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringListSchema,
      options: z.array(optionSchema).min(1),
      orientation: z.enum(["vertical", "horizontal"]).default("vertical").optional(),
    })
    .strict()
    .describe("Choose any number of independent options with a bound string-list value."),
} satisfies ComponentApi;

export const SliderApi = {
  name: "Slider",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicNumberSchema,
      min: z.number().default(0).optional(),
      max: z.number().default(100).optional(),
      step: z.number().positive().default(1).optional(),
      showValue: DynamicBooleanSchema.default(true).optional(),
      unit: DynamicStringSchema.optional(),
    })
    .strict()
    .describe("Select a numeric value from a bounded range with immediate visual feedback."),
} satisfies ComponentApi;

export const DatePickerApi = {
  name: "DatePicker",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringSchema,
      min: z.string().optional(),
      max: z.string().optional(),
      precision: z.enum(["date", "month", "datetime-local"]).default("date").optional(),
    })
    .strict()
    .describe("Choose a date, month, or local date-time using a native accessible control."),
} satisfies ComponentApi;

export const SwitchGroupApi = {
  name: "SwitchGroup",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringListSchema,
      options: z.array(optionSchema).min(1),
    })
    .strict()
    .describe("Turn several independent settings on or off with a bound string-list value."),
} satisfies ComponentApi;

export const ToggleGroupApi = {
  name: "ToggleGroup",
  schema: z
    .object({
      ...fieldProps,
      value: DynamicStringListSchema,
      options: z.array(optionSchema).min(1),
      multiple: DynamicBooleanSchema.default(false).optional(),
      fullWidth: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Select one or more compact options using a segmented control."),
} satisfies ComponentApi;

export const formApis = [
  FormApi,
  InputApi,
  TextAreaApi,
  SelectApi,
  RadioGroupApi,
  CheckboxGroupApi,
  SliderApi,
  DatePickerApi,
  SwitchGroupApi,
  ToggleGroupApi,
] as const;
