import { z } from "zod/v4";

import { ToneSchema, TrendSchema } from "../lib/schema";

export const StatsCardSchema = z.object({
  label: z.string(),
  value: z.string(),
  description: z.string().optional(),
  delta: z.string().optional(),
  trend: TrendSchema.optional(),
  icon: z.string().optional(),
  tone: ToneSchema.optional(),
});
