import { z } from "zod/v4";

import { AlignSchema, ImagePositionSchema, ToneSchema } from "../lib/schema";

/**
 * Props for the long-form report family.
 *
 * This family is the *document* layer only: the page canvas, the furniture that
 * lives on a page (running header/footer, page number, section titles, table of
 * contents) and the two or three blocks that exist nowhere but inside a report.
 * Everything a report shows *inside* a page — KPI strips, prose, charts, quotes
 * — is a shared block reused from its own family. There is deliberately no
 * `ReportMiniCard` / `ReportKeyMetrics` twin: a denser reading of a shared block
 * is a prop on that block, not a second component here.
 */

/**
 * Cover treatment. `standard` pairs the title with an image, `minimal` is
 * typography alone on a quiet page, `dramatic` runs the image full-bleed behind
 * the title.
 */
export const ReportCoverVariantSchema = z.enum(["standard", "minimal", "dramatic"]);
export type ReportCoverVariant = z.infer<typeof ReportCoverVariantSchema>;

/** How much of the page's text column a figure occupies. */
export const ReportImageWidthSchema = z.enum(["full", "half"]);
export type ReportImageWidth = z.infer<typeof ReportImageWidthSchema>;

/*
 * Key order below is the call signature, not a style choice: OpenUI Lang binds
 * arguments positionally in zod key order. With `children` after an optional
 * scalar, `ReportPage([...])` assigns the array to that scalar and leaves the
 * page empty — no parse error, no type error. Content slot first, optional
 * furniture after.
 */

export const ReportDocumentSchema = z.object({
  children: z.array(z.unknown()),
  title: z.string().optional(),
});

export const ReportPageSchema = z.object({
  children: z.array(z.unknown()),
  header: z.string().optional(),
  footer: z.string().optional(),
  pageNumber: z.number().optional(),
});

export const ReportFrontPageSchema = z.object({
  title: z.string(),
  subtitle: z.string().optional(),
  author: z.string().optional(),
  date: z.string().optional(),
  imageUrl: z.string().optional(),
  imageAlt: z.string().optional(),
  variant: ReportCoverVariantSchema.optional(),
  imagePosition: ImagePositionSchema.optional(),
});

export const ReportTocItemSchema = z.object({
  label: z.string(),
  page: z.number().optional(),
});

export const ReportTocPageSchema = z.object({
  items: z.array(ReportTocItemSchema),
  title: z.string().optional(),
});

export const ReportSectionSchema = z.object({
  title: z.string(),
  children: z.array(z.unknown()),
  eyebrow: z.string().optional(),
});

export const ReportHeadlineSchema = z.object({
  text: z.string(),
  kicker: z.string().optional(),
});

export const ReportKeyStatementSchema = z.object({
  text: z.string(),
  attribution: z.string().optional(),
  tone: ToneSchema.optional(),
});

export const ReportTableSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.string())),
  caption: z.string().optional(),
  align: z.array(AlignSchema).optional(),
});

export const ReportImageSchema = z.object({
  url: z.string(),
  alt: z.string().optional(),
  caption: z.string().optional(),
  width: ReportImageWidthSchema.optional(),
});
