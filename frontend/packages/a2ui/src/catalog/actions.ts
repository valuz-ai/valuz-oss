import type { ComponentApi } from "@a2ui/web_core/v0_9";
import { z } from "zod";

import {
  ActionSchema,
  ChildListSchema,
  DynamicBooleanSchema,
  DynamicStringSchema,
  commonProps,
} from "./primitives";

export const ButtonApi = {
  name: "Button",
  schema: z
    .object({
      ...commonProps,
      label: DynamicStringSchema,
      action: ActionSchema,
      variant: z.enum(["default", "outline", "ghost", "destructive", "link"])
        .default("default")
        .optional(),
      size: z.enum(["sm", "default", "icon"]).default("default").optional(),
      icon: z.string().optional(),
      disabled: DynamicBooleanSchema.default(false).optional(),
      fullWidth: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Trigger a clear user action using a semantic visual priority."),
} satisfies ComponentApi;

export const ButtonGroupApi = {
  name: "ButtonGroup",
  schema: z
    .object({
      ...commonProps,
      children: ChildListSchema,
      align: z.enum(["start", "center", "end", "stretch"]).default("start").optional(),
      attached: DynamicBooleanSchema.default(false).optional(),
    })
    .strict()
    .describe("Arrange a small set of related actions with consistent spacing."),
} satisfies ComponentApi;

const followUpItemSchema = z
  .object({
    label: DynamicStringSchema,
    description: DynamicStringSchema.optional(),
    action: ActionSchema,
    icon: z.string().optional(),
  })
  .strict();

export const FollowUpBlockApi = {
  name: "FollowUpBlock",
  schema: z
    .object({
      ...commonProps,
      title: DynamicStringSchema.optional(),
      items: z.array(followUpItemSchema).min(1).max(8),
      layout: z.enum(["list", "chips", "grid"]).default("list").optional(),
    })
    .strict()
    .describe("Offer concise, context-aware next actions or suggested questions."),
} satisfies ComponentApi;

export const actionApis = [ButtonApi, ButtonGroupApi, FollowUpBlockApi] as const;
