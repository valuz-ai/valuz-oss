import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

export const MermaidSchema = z.object({
  code: z.string(),
  title: z.string().optional(),
});

export const MermaidBadgeSchema = z.object({
  label: z.string(),
  tone: ToneSchema.optional(),
});
