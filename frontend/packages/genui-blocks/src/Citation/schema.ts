import { z } from "zod/v4";

/**
 * Citations and sources.
 *
 * `index` is a number everywhere: the marker in the prose and the row in the
 * list are the same reference, and keeping one type for both is what lets the
 * model line them up without a rule telling it to.
 */

export const CitationSchema = z.object({
  index: z.number(),
  title: z.string().optional(),
  url: z.string().optional(),
});

export const SourceItemSchema = z.object({
  index: z.number(),
  title: z.string(),
  url: z.string().optional(),
  snippet: z.string().optional(),
  siteName: z.string().optional(),
  faviconUrl: z.string().optional(),
});

export const SourceListSchema = z.object({
  children: z.array(z.unknown()),
});

/*
 * `children` leads, as it does in OpenUI's own `Card(children, variant?)`.
 * Key order is the positional-argument order in the generated signature, so
 * putting the optional label first would make every call site spell out a
 * label it does not want.
 */
export const CondensedSourcesSchema = z.object({
  children: z.array(z.unknown()),
  label: z.string().optional(),
});
