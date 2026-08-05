import type { PromptOptions } from "@openuidev/react-lang";
import { openuiPromptOptions } from "@openuidev/react-ui/genui-lib";

/**
 * Prompt material for the blocks in this package.
 *
 * The component `description` strings tell the model what each block *is*;
 * these examples tell it what a correct call looks like. Both end up in the
 * generated system prompt, and the examples do most of the work — a model that
 * has seen `MiniCardBlock([a, b, c])` written out reaches for it far more
 * reliably than one that has only read a sentence about it.
 */

export const blockExamples: string[] = [
  `Example — KPI strip (prefer this over a row of Cards):

root = Stack([title, strip])
title = TextContent("Q4 performance", "large-heavy")
strip = MiniCardBlock([rev, margin, heads])
rev = MiniCard("Revenue", "$4.2M", "+12.4%", "up")
margin = MiniCard("Gross margin", "38%", "-1.2pp", "down")
heads = MiniCard("Headcount", "184")`,

  `Example — a cited answer:

root = Stack([body, sources])
body = MarkDownRenderer("Renewals carried the year, not new logos.")
sources = CondensedSources([s1, s2])
s1 = SourceItem(1, "FY26 annual report", "https://example.com/fy26", "Renewal rate reached 94%.")
s2 = SourceItem(2, "Q4 earnings call", "https://example.com/q4-call")`,

  `Example — a report document:

root = ReportDocument([cover, toc, page1])
cover = ReportFrontPage("Annual Review", "Fiscal 2026", "Research")
toc = ReportTocPage([{ label: "Summary", page: 2 }, { label: "Outlook", page: 8 }])
page1 = ReportPage([summary])
summary = ReportSection("Summary", [headline, kpis])
headline = ReportHeadline("Revenue grew for a fourth straight quarter")
kpis = MiniCardBlock([m1, m2])
m1 = MiniCard("Revenue", "$4.2M", "+12.4%", "up")
m2 = MiniCard("Margin", "38%")`,
];

export const blockAdditionalRules: string[] = [
  "Three or more single-figure metrics side by side is a MiniCardBlock of MiniCards, never a row of Cards.",
  "Every claim taken from a source gets a Citation; close the answer with CondensedSources listing them.",
  "Report pages are filled with the ordinary shared blocks (MiniCardBlock, TextContent, charts) — there is no report-specific twin of any content block.",
  "Mermaid displays diagram source as text, not a drawn picture. Only use it when the source itself is worth showing.",
  "OptionCard, TileOption and the selected flag are presentational: nothing rendered here is clickable, so never present them as a choice the user can make.",
  "Never paste an emoji into a label, title or heading to stand in for an icon. Blocks that can carry one take an `icon` prop naming any lucide-react icon; a block without that prop is a block meant to have no icon.",
];

/**
 * OpenUI's prompt options with this package's material appended.
 *
 * OpenUI's own examples and rules come first so the model reads the base
 * vocabulary before the additions — the blocks are an extension of that
 * vocabulary, not a replacement for it.
 */
export const valuzPromptOptions: PromptOptions = {
  examples: [...(openuiPromptOptions.examples ?? []), ...blockExamples],
  additionalRules: [
    ...(openuiPromptOptions.additionalRules ?? []),
    ...blockAdditionalRules,
  ],
};
