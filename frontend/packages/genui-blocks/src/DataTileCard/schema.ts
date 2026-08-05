import { z } from "zod/v4";

import { ToneSchema } from "../lib/schema";

export const DataTileCardSchema = z.object({
  value: z.string(),
  breakdown: z.string().optional(),
  label: z.string().optional(),
  icon: z.string().optional(),
  tone: ToneSchema.optional(),
});
