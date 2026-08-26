import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  ActionSchema,
  DynamicBooleanSchema,
  DynamicNumberSchema,
  DynamicStringSchema,
  DynamicValueSchema,
  commonProps,
  sizeSchema,
  toneSchema,
} from "./primitives";

export const TextContentApi = {
  name: "TextContent",
  schema: z
    .object({
      ...commonProps,
      text: DynamicStringSchema,
      variant: z
        .enum(["display", "h1", "h2", "h3", "h4", "body", "caption", "label"])
        .default("body")
        .optional(),
      tone: toneSchema,
      align: z.enum(["left", "center", "right"]).default("left").optional(),
      truncate: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe(
      "Render text with semantic hierarchy and restrained presentation controls.",
    ),
} satisfies ComponentApi;

export const MarkdownApi = {
  name: "Markdown",
  schema: z
    .object({
      ...commonProps,
      content: DynamicStringSchema,
      compact: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Render safe Markdown prose, lists, links, and inline code."),
} satisfies ComponentApi;

export const ImageApi = {
  name: "Image",
  schema: z
    .object({
      ...commonProps,
      src: DynamicStringSchema,
      alt: DynamicStringSchema,
      caption: DynamicStringSchema.optional(),
      fit: z.enum(["cover", "contain", "fill"]).default("cover").optional(),
      aspectRatio: z
        .enum(["auto", "square", "video", "portrait", "wide"])
        .default("auto")
        .optional(),
      radius: z
        .enum(["none", "sm", "md", "lg", "full"])
        .default("md")
        .optional(),
    })
    .strict()
    .describe(
      "Display an accessible image with predictable sizing and an optional caption.",
    ),
} satisfies ComponentApi;

const galleryImageSchema = z
  .object({
    src: z.string(),
    alt: z.string(),
    caption: z.string().optional(),
  })
  .strict();

export const ImageGalleryApi = {
  name: "ImageGallery",
  schema: z
    .object({
      ...commonProps,
      images: z.union([z.array(galleryImageSchema), DynamicValueSchema]),
      columns: z.number().int().min(1).max(6).default(3).optional(),
      aspectRatio: z
        .enum(["square", "video", "portrait", "auto"])
        .default("square")
        .optional(),
    })
    .strict()
    .describe("Show a responsive collection of related images."),
} satisfies ComponentApi;

const tagSchema = z
  .object({
    label: DynamicStringSchema,
    tone: toneSchema,
  })
  .strict();

export const TagBlockApi = {
  name: "TagBlock",
  schema: z
    .object({
      ...commonProps,
      tags: z.array(tagSchema).min(1),
      size: sizeSchema,
    })
    .strict()
    .describe("Display compact labels for status, category, or metadata."),
} satisfies ComponentApi;

const listItemSchema = z
  .object({
    title: DynamicStringSchema,
    description: DynamicStringSchema.optional(),
    value: DynamicStringSchema.optional(),
    icon: z.string().optional(),
    tone: toneSchema,
  })
  .strict();

export const ListBlockApi = {
  name: "ListBlock",
  schema: z
    .object({
      ...commonProps,
      items: z.union([z.array(listItemSchema), DynamicValueSchema]),
      ordered: DynamicBooleanSchema.default(false).optional(),
      divided: DynamicBooleanSchema.default(false).optional(),
      density: z
        .enum(["compact", "comfortable"])
        .default("comfortable")
        .optional(),
    })
    .strict()
    .describe(
      "Present a readable list of facts, entities, tasks, or ranked results.",
    ),
} satisfies ComponentApi;

const tableColumnSchema = z
  .object({
    key: z.string(),
    label: DynamicStringSchema,
    align: z.enum(["left", "center", "right"]).default("left").optional(),
    format: z
      .enum(["text", "number", "percent", "currency", "date"])
      .default("text")
      .optional(),
    width: z.number().int().min(60).max(640).optional(),
  })
  .strict();

export const TableApi = {
  name: "Table",
  schema: z
    .object({
      ...commonProps,
      columns: z.array(tableColumnSchema).min(1),
      rows: DynamicValueSchema,
      caption: DynamicStringSchema.optional(),
      striped: DynamicBooleanSchema.default(false).optional(),
      compact: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Compare structured records using aligned, scannable columns."),
} satisfies ComponentApi;

export const CodeBlockApi = {
  name: "CodeBlock",
  schema: z
    .object({
      ...commonProps,
      code: DynamicStringSchema,
      language: z.string().default("text").optional(),
      filename: DynamicStringSchema.optional(),
      showLineNumbers: DynamicBooleanSchema.default(false).optional(),
      wrap: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe(
      "Display code or machine-readable text in a copyable, readable panel.",
    ),
} satisfies ComponentApi;

export const CalloutApi = {
  name: "Callout",
  schema: z
    .object({
      ...commonProps,
      title: DynamicStringSchema.optional(),
      content: DynamicStringSchema,
      tone: toneSchema,
      icon: z.string().optional(),
    })
    .strict()
    .describe(
      "Emphasize a concise insight, warning, success, or contextual note.",
    ),
} satisfies ComponentApi;

export const AvatarApi = {
  name: "Avatar",
  schema: z
    .object({
      ...commonProps,
      src: DynamicStringSchema.optional(),
      name: DynamicStringSchema,
      description: DynamicStringSchema.optional(),
      size: z.enum(["xs", "sm", "md", "lg", "xl"]).default("md").optional(),
      shape: z.enum(["circle", "rounded"]).default("circle").optional(),
    })
    .strict()
    .describe("Identify a person, organization, agent, or entity."),
} satisfies ComponentApi;

export const ProgressApi = {
  name: "Progress",
  schema: z
    .object({
      ...commonProps,
      value: DynamicNumberSchema,
      max: DynamicNumberSchema.default(100).optional(),
      label: DynamicStringSchema.optional(),
      showValue: DynamicBooleanSchema.default(true).optional(),
      tone: toneSchema,
    })
    .strict()
    .describe("Show measurable completion or a bounded quantitative state."),
} satisfies ComponentApi;

export const SkeletonApi = {
  name: "Skeleton",
  schema: z
    .object({
      ...commonProps,
      variant: z.enum(["text", "rect", "circle"]).default("rect").optional(),
      width: z.union([z.number().positive(), z.string()]).optional(),
      height: z.union([z.number().positive(), z.string()]).optional(),
      lines: z.number().int().min(1).max(12).default(1).optional(),
    })
    .strict()
    .describe(
      "Reserve layout while streamed or remote content is still loading.",
    ),
} satisfies ComponentApi;

export const EmptyStateApi = {
  name: "EmptyState",
  schema: z
    .object({
      ...commonProps,
      title: DynamicStringSchema,
      description: DynamicStringSchema.optional(),
      icon: z.string().optional(),
      actionLabel: DynamicStringSchema.optional(),
      action: ActionSchema.optional(),
    })
    .strict()
    .describe(
      "Explain an intentional empty result and suggest what the user can do next; an optional action (with actionLabel) renders one follow-up button.",
    ),
} satisfies ComponentApi;

export const contentApis = [
  TextContentApi,
  MarkdownApi,
  ImageApi,
  ImageGalleryApi,
  TagBlockApi,
  ListBlockApi,
  TableApi,
  CodeBlockApi,
  CalloutApi,
  AvatarApi,
  ProgressApi,
  SkeletonApi,
  EmptyStateApi,
] as const;
