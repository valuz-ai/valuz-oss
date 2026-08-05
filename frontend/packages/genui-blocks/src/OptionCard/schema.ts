import { z } from "zod/v4";

export const OptionCardSchema = z.object({
  title: z.string(),
  description: z.string().optional(),
  selected: z.boolean().optional(),
});

export const OptionCardsSchema = z.object({
  children: z.array(z.unknown()),
});
