import { z } from "zod/v4";

import { TrendSchema } from "../lib/schema";

/**
 * A figure the model writes as often as a number as it does as a string —
 * `rank: 1` and `value: 924.24` are both routine. Declaring the union keeps
 * those payloads valid instead of silently falling back to the raw props.
 */
const FigureSchema = z.union([z.string(), z.number()]);

/*
 * Row shape. `looseObject`, not `object`: this schema is also the element type
 * of `DataList.items`, and a strict object would *strip* the alias keys the
 * component reads (`name`, `change_pct`, `pct`) on the way through — the block
 * would then render blank rows from a payload that carried every field.
 *
 * Key order is the positional call signature (OpenUI Lang binds arguments in
 * zod key order), so it reads the way a row is spoken: what it is, what it is
 * worth, how it moved.
 */
export const DataListItemSchema = z.looseObject({
  title: z.string(),
  value: FigureSchema.optional(),
  meta: z.string().optional(),
  description: z.string().optional(),
  rank: FigureSchema.optional(),
  trend: TrendSchema.optional(),
});

export const DataListSchema = z.object({
  items: z.array(DataListItemSchema),
  children: z.array(z.unknown()).optional(),
  title: z.string().optional(),
  description: z.string().optional(),
});
