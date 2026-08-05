import { z } from "zod/v4";

import { TrendSchema } from "../lib/schema";

/** A quote figure: `3830.84` and `"3,830.84"` are both what models emit. */
const FigureSchema = z.union([z.string(), z.number()]);

/*
 * One quote. `looseObject` because this is also the element type of
 * `MarketIndexGrid.indices`: a strict object would strip the alias keys the
 * component reads (`change_pct`, `symbol`, `price`) while parsing the array,
 * and the card would render with its change line missing.
 *
 * Key order is the positional call signature — name, then identity, then the
 * figures in the order a quote is read aloud.
 */
export const MarketIndexCardSchema = z.looseObject({
  name: z.string(),
  code: z.string().optional(),
  latest: FigureSchema.optional(),
  change: FigureSchema.optional(),
  changePct: z.string().optional(),
  turnover: z.string().optional(),
  source: z.string().optional(),
  asOf: z.string().optional(),
  trend: TrendSchema.optional(),
});

export const MarketIndexGridSchema = z.object({
  indices: z.array(MarketIndexCardSchema),
  children: z.array(z.unknown()).optional(),
  title: z.string().optional(),
  description: z.string().optional(),
});
