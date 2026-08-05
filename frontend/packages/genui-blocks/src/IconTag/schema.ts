import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

export const IconSizeSchema = z.enum(["xs", "s", "m", "l", "xl"]);
export type IconSize = z.infer<typeof IconSizeSchema>;

export const IconTagSchema = z.object({
  icon: z.string(),
  size: IconSizeSchema.optional(),
  tone: ToneSchema.optional(),
});

export const IconTextSchema = z.object({
  icon: z.string(),
  text: z.string(),
  description: z.string().optional(),
  tone: ToneSchema.optional(),
  layout: z.enum(["horizontal", "vertical"]).optional(),
});
