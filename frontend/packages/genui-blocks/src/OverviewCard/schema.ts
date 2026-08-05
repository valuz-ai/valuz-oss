import { z } from "zod/v4";

export const OverviewCardSchema = z.object({
  title: z.string(),
  body: z.string().optional(),
  children: z.array(z.unknown()).optional(),
  icon: z.string().optional(),
});
