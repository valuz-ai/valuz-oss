import { z } from "zod/v4";

export const ContextCardSchema = z.object({
  title: z.string(),
  body: z.string(),
  source: z.string().optional(),
  icon: z.string().optional(),
});
