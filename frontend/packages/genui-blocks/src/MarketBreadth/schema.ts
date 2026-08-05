import { z } from "zod/v4";

/*
 * Advancers / decliners / unchanged.
 *
 * The three counts lead and are required: a breadth bar drawn from two of them
 * is a different, wrong picture, so there is no useful call that omits one.
 * `total` is optional because it is normally their sum — pass it only when the
 * universe is larger than the three counts describe.
 */
export const MarketBreadthSchema = z.object({
  up: z.number(),
  down: z.number(),
  flat: z.number(),
  title: z.string().optional(),
  total: z.number().optional(),
  source: z.string().optional(),
});
