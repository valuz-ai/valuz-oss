import { z } from "zod/v4";

export const ValueCardSchema = z.object({
  label: z.string(),
  value: z.string(),
  description: z.string().optional(),
});
