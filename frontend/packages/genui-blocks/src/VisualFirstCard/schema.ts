import { z } from "zod/v4";

export const VisualFirstCardSchema = z.object({
  imageUrl: z.string(),
  title: z.string(),
  body: z.string().optional(),
  imageAlt: z.string().optional(),
});
