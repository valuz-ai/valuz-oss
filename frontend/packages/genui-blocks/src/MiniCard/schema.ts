import { z } from "zod/v4";

import { ToneSchema, TrendSchema } from "../lib/schema";

export const MiniCardSchema = z.object({
  label: z.string(),
  value: z.string(),
  delta: z.string().optional(),
  trend: TrendSchema.optional(),
  tone: ToneSchema.optional(),
});

export const MiniCardBlockSchema = z.object({
  children: z.array(z.unknown()),
});
