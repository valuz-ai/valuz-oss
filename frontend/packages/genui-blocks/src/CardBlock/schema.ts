import { z } from "zod/v4";

export const SmallCardBlockSchema = z.object({
  children: z.array(z.unknown()),
});

export const MediumCardBlockSchema = z.object({
  children: z.array(z.unknown()),
});
