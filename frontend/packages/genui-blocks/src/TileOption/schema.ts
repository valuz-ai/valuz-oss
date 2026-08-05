import { z } from "zod/v4";

export const TileOptionSchema = z.object({
  label: z.string(),
  description: z.string().optional(),
  selected: z.boolean().optional(),
});

export const TileOptionBlockSchema = z.object({
  children: z.array(z.unknown()),
});
