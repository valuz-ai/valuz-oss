import { z } from "zod/v4";

/**
 * Schema fragments shared across blocks.
 *
 * Keeping the vocabulary small and reused matters more here than it does in
 * ordinary code: every enum member below is copied verbatim into the LLM's
 * system prompt for every block that references it, so an extra synonym costs
 * prompt budget in proportion to how many blocks use it.
 */

/** Semantic colour role. Resolves through `toneVars()` in `lib/tone.ts`. */
export const ToneSchema = z.enum([
  "neutral",
  "brand",
  "success",
  "warning",
  "danger",
  "info",
]);
export type Tone = z.infer<typeof ToneSchema>;

/** Direction of a metric's change, for arrows and colour. */
export const TrendSchema = z.enum(["up", "down", "flat"]);
export type Trend = z.infer<typeof TrendSchema>;

/** Horizontal alignment shared by text-bearing blocks. */
export const AlignSchema = z.enum(["left", "center", "right"]);
export type Align = z.infer<typeof AlignSchema>;

/** Relative type scale. Slides read larger than inline blocks at every step. */
export const SizeSchema = z.enum(["small", "medium", "large"]);
export type Size = z.infer<typeof SizeSchema>;

/** Where an image sits relative to the text it accompanies. */
export const ImagePositionSchema = z.enum(["left", "right", "top", "bottom"]);
export type ImagePosition = z.infer<typeof ImagePositionSchema>;
