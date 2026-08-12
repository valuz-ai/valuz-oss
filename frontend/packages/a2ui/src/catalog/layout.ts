import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  ActionSchema,
  ChildListSchema,
  ComponentIdSchema,
  DynamicBooleanSchema,
  DynamicStringSchema,
  alignSchema,
  commonProps,
  gapSchema,
  justifySchema,
} from "./primitives";

export const StackApi = {
  name: "Stack",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      direction: z.enum(["vertical", "horizontal"]).default("vertical").optional(),
      gap: gapSchema,
      align: alignSchema,
      justify: justifySchema,
      wrap: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Arrange child components in a vertical or horizontal stack."),
} satisfies ComponentApi;

export const GridApi = {
  name: "Grid",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      columns: z.number().int().min(1).max(12).default(2).optional(),
      minItemWidth: z.number().int().min(120).max(800).optional(),
      gap: gapSchema,
      align: alignSchema,
    })
    .strict()
    .describe("Responsive grid for cards, metrics, media, and other repeated content."),
} satisfies ComponentApi;

export const CardApi = {
  name: "Card",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      title: DynamicStringSchema.optional(),
      subtitle: DynamicStringSchema.optional(),
      variant: z.enum(["default", "muted", "outlined", "elevated"])
        .default("default")
        .optional(),
      padding: z.enum(["none", "sm", "md", "lg"]).default("md").optional(),
    })
    .strict()
    .describe("A polished surface that groups related content and actions."),
} satisfies ComponentApi;

const tabSchema = z
  .object({
    label: DynamicStringSchema,
    value: z.string().optional(),
    child: ComponentIdSchema,
    disabled: DynamicBooleanSchema.optional(),
  })
  .strict();

export const TabsApi = {
  name: "Tabs",
  schema: z
    .object({
      ...commonProps,
      items: z.array(tabSchema).min(1),
      defaultValue: z.string().optional(),
      variant: z.enum(["underline", "pill"]).default("underline").optional(),
    })
    .strict()
    .describe("Switch between a small number of related content views."),
} satisfies ComponentApi;

const accordionItemSchema = z
  .object({
    title: DynamicStringSchema,
    description: DynamicStringSchema.optional(),
    child: ComponentIdSchema,
  })
  .strict();

export const AccordionApi = {
  name: "Accordion",
  schema: z
    .object({
      ...commonProps,
      items: z.array(accordionItemSchema).min(1),
      multiple: DynamicBooleanSchema.default(false).optional(),
      defaultOpen: z.array(z.number().int().nonnegative()).optional(),
    })
    .strict()
    .describe("Progressively disclose detailed sections without overwhelming the page."),
} satisfies ComponentApi;

const stepSchema = z
  .object({
    title: DynamicStringSchema,
    description: DynamicStringSchema.optional(),
    status: z.enum(["pending", "current", "complete", "error"]).default("pending").optional(),
    child: ComponentIdSchema.optional(),
  })
  .strict();

export const StepsApi = {
  name: "Steps",
  schema: z
    .object({
      ...commonProps,
      items: z.array(stepSchema).min(1),
      orientation: z.enum(["vertical", "horizontal"]).default("vertical").optional(),
    })
    .strict()
    .describe("Show progress through a sequence, workflow, or ordered explanation."),
} satisfies ComponentApi;

export const CarouselApi = {
  name: "Carousel",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      initialIndex: z.number().int().nonnegative().default(0).optional(),
      showIndicators: DynamicBooleanSchema.default(true).optional(),
    })
    .strict()
    .describe("Browse a compact sequence of media or cards one item at a time."),
} satisfies ComponentApi;

export const SeparatorApi = {
  name: "Separator",
  schema: z
    .object({
      ...commonProps,
      orientation: z.enum(["horizontal", "vertical"]).default("horizontal").optional(),
      label: DynamicStringSchema.optional(),
    })
    .strict()
    .describe("Visually separate nearby content groups."),
} satisfies ComponentApi;

export const ModalApi = {
  name: "Modal",
  schema: z
    .object({
      ...commonProps,
      triggerChild: ComponentIdSchema,
      contentChild: ComponentIdSchema,
      title: DynamicStringSchema.optional(),
      description: DynamicStringSchema.optional(),
      dismissible: DynamicBooleanSchema.default(true).optional(),
      onOpen: ActionSchema.optional(),
    })
    .strict()
    .describe("Open focused supplementary content without leaving the current surface."),
} satisfies ComponentApi;

export const layoutApis = [
  StackApi,
  GridApi,
  CardApi,
  TabsApi,
  AccordionApi,
  StepsApi,
  CarouselApi,
  SeparatorApi,
  ModalApi,
] as const;
