import { z } from "zod/v4";

export const CompositeCardSchema = z.object({
  title: z.string(),
  eyebrow: z.string().optional(),
  value: z.string().optional(),
  clickable: z.boolean().optional(),
  children: z.array(z.unknown()).optional(),
  icon: z.string().optional(),
});
