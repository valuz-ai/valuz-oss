# Slide design guide

Read this before building a new deck or restyling one. When the user supplies
a template or an existing deck, its design wins: reuse its layouts, fonts and
colours and apply only the rules about content, sizing and alignment.

All numbers below assume a 16:9 slide of 13.333 x 7.5 in.

## 1. Plan the story before the slides

- Write the slide list first: one message per slide, in order. A reader who
  only reads the titles should get the argument.
- Titles state the takeaway, not the topic: "APAC drove 60% of growth" rather
  than "Regional results". Keep a title to one line (about 55 Latin characters
  or 25 CJK characters at 30 pt).
- Budget words: about 40-60 words of body text per slide (CJK: about 80-120
  characters). Move detail into speaker notes or an appendix slide.
- Decide what each slide shows: a number, a comparison, a trend, a process, a
  list. That decides the layout (section 5).

## 2. Palette

Pick a small palette and use it everywhere:

| role | use | example (navy) |
|---|---|---|
| background | slide background, mostly white or near-white | `FFFFFF` |
| text | titles and body | `1B2433` |
| muted | captions, axis labels, sources | `5B6575` |
| primary | the one brand/subject colour: header fills, key numbers, markers | `1F3A5F` |
| highlight | emphasis that must also work as text: a delta, a date, the focus bar | `C2410C` |
| surface | light tint of primary or neutral for cards and bands | `F1F4F9` |
| accents | chart series, 3-5 colours, primary first | `1F3A5F 3B7DD8 8FB3E8 D2601F` |
| rule | hairlines, gridlines, table rules | `D5DBE3` |

How to choose:

- From a brand: use the brand colour as primary; make the surface a 90-95%
  tint of it (mix with white); keep text a near-black neutral; add one
  contrasting accent (a warm colour next to a cool primary, or the reverse).
- From the subject when there is no brand: finance/corporate navy, health or
  science teal, sustainability green, premium/legal burgundy, editorial
  charcoal with one orange accent, Chinese corporate red with greys. The
  helper module ships these as `PALETTES`.
- Contrast: body text at least 4.5:1 against what is behind it, large text
  (18 pt+, or 14 pt+ bold) at least 3:1. White text on a light accent and grey
  text on a grey card are the usual failures; `content_qa.py` flags them.
  Light accents and tints are for fills and chart series, not for text; for
  coloured text, and for fills that carry white text, use `primary` or
  `highlight` (both at least 4.5:1 against the background in `PALETTES`).
- Proportion: mostly background and text, the primary for structure, the
  accent for the one thing that should catch the eye. A slide where
  everything is coloured has no emphasis.
- Dark decks (light text on a dark background) suit keynote-style talks; use
  them for the whole deck or only for cover and section slides, not randomly.
- Finance charts: in mainland China up/gain is red and down/loss is green; in
  most other markets it is the reverse. Follow the audience's convention and
  say so in a legend when it matters.

## 3. Type

One sans-serif family for the deck (Latin: Calibri or Arial; CJK: Microsoft
YaHei), set through the theme fonts so charts and tables follow. Sizes:

| element | size | notes |
|---|---|---|
| cover title | 40-48 pt bold | up to two lines |
| slide title | 28-32 pt bold | one line, same position on every slide |
| subtitle / lead line | 16-20 pt | muted colour |
| body, bullets | 16-20 pt | never below 14 pt |
| key number (KPI) | 36-54 pt bold | primary colour |
| table text, chart labels | 11-14 pt | never below 10 pt |
| source, footnote | 9-10 pt | muted, bottom-left |

Line spacing 1.1-1.2 for Latin text, 1.2-1.3 for CJK. Left-align body text;
centre only short items (a KPI number, a label under an icon). Bold is for a
few words, not whole paragraphs. Do not italicise CJK text.

## 4. Grid and spacing

- Margins: 0.6 in left, right and bottom. Nothing touches the slide edge
  except deliberate full-bleed images or colour bands.
- Title block: top at 0.45 in, 0.75 in tall, so content starts at about
  1.55 in (1.85 in when a subtitle line sits under the title).
- Gutters: 0.3 in between columns and between stacked blocks; 0.15-0.25 in
  padding inside cards.
- Divide the content area (12.13 x 5.35 in under a title) into 2, 3 or 4
  equal columns, or 2:1 / 1:2 splits. `columns()` and `rows()` in the helper
  module do the arithmetic.
- Align edges: shapes in a row share a top edge and height; columns share a
  left edge. Repeated elements (cards, steps) have identical size and spacing.
- Keep a consistent footer zone (source line, page number) at 6.95-7.2 in.
- Balance the slide. Content should use most of the content area. When a
  layout leaves the lower third or more empty, enlarge the content within the
  type scale (bigger chart, taller rows), centre the group vertically, or add
  a supporting element that carries information (a takeaway bar, a small
  table, a KPI). Do not stretch a card around three short bullets either:
  size cards to their content.

## 5. Layout patterns

Vary layouts across the deck: three bullet slides in a row reads as a
document. Useful patterns, with positions for the 16:9 grid:

- **Cover**: title block left-aligned at x 0.6, y about 2.3, width 7.5 in;
  subtitle and date below it; a primary-coloured band or panel on the right
  third or along the left edge (a 0.12 in accent bar is enough).
- **Section divider**: primary background, white section title at 40 pt, a
  small number ("02") above it.
- **Chart + takeaways**: chart in the left 2/3 (about 7.9 in wide), 2-3 short
  takeaway points in the right 1/3. The title states the conclusion.
- **KPI row**: 3-4 equal cards across the content width; in each a big
  number, a one-line label, and a comparison line ("+18% YoY") in the
  highlight colour. Labels must fit on one line at 14 pt (about 2.6 in for a
  4-card row: short labels such as "经营现金流 OCF"). Optionally a chart,
  table or takeaway bullets below.
- **Two-column comparison**: two equal cards (before/after, option A/B,
  problem/action) with a coloured header strip each.
- **Table**: full content width, header row in the primary colour, numbers
  right-aligned, the row that matters bold or highlighted, 6-10 rows max.
- **Process / timeline**: 3-6 equal steps in a row with numbered markers and
  a short label and one or two lines of detail under each; a row of steps is
  short, so add an outcome bar below it or centre it vertically.
- **Big statement / quote**: one sentence at 32-40 pt with lots of space.
- **Next steps**: numbered list of actions with owner and date columns.

## 6. Charts, tables, images

- Use native charts and tables (editable, crisp at any size), not pictures of
  them. Pictures are for photos, screenshots, logos and diagrams.
- One message per chart; the slide title carries it. Remove chart titles,
  borders and heavy gridlines; keep light horizontal gridlines or none with
  data labels.
- Sort bars by value unless the order is meaningful (time, ranking). Bars
  start at zero. Put units in the title or axis ("Revenue, USD m").
- Highlight with colour: the series or bar the title talks about in the
  highlight colour, the rest in the primary, its tints or grey (pass
  `colors=[...]` to `add_chart`). The same series keeps the same colour on
  every slide.
- Data labels when there are few points (about 8 or fewer); otherwise axis
  labels plus gridlines.
- Pie or doughnut only for parts of one whole with 2-5 slices.
- Tables: keep cell text short; numbers right-aligned with consistent decimals;
  thin horizontal rules instead of a full grid; no more than about 7 columns.
- Images: local files only (no network URLs); keep aspect ratio (fit inside a
  box or crop to fill it, never stretch); give full-bleed photos a dark overlay
  or a solid panel before putting text on them.

## 7. Common mistakes

- Text that overflows its box or the slide, or is shrunk to fit (autofit) until
  unreadable. Shorten the text or split the slide.
- Tiny text: body under 14 pt, labels under 10 pt.
- Low contrast: light grey on white, white on pale accent, dark on a dark band.
- Too many words: paragraphs on slides, six bullets of two lines each.
- Uneven alignment: boxes a few pixels apart, cards of different heights,
  titles that move between slides.
- The default unstyled look: black Calibri on white with default blue charts
  and a centred title. Apply the theme and a layout pattern.
- Decorative clutter: clip art, random icons, gradients, shadows, 3D charts,
  borders around everything, more than two accent colours on a slide.
- Leftover template text ("Click to add title", "Lorem ipsum") and empty
  placeholders.
- Typed bullet characters ("• ", "- ") instead of real bullets.
- Mixed fonts or sizes for the same kind of element across slides.

## 8. CJK and mixed-language decks

- Set the East Asian font (theme `ea` font or `set_font(..., ea=...)`);
  otherwise PowerPoint falls back to a default CJK font that may not match.
- CJK text is denser: a 30 pt title fits about 25 characters across the slide;
  body text fits about 30 characters per line in a 6 in column at 18 pt.
- Use full-width punctuation in Chinese text (，。：；“”） and keep the style of
  numbers and units consistent (e.g. 1.2 亿元, 18%).
- Keep a number and its unit on one line: write "±5mm" / "1.5吨" without a
  space or give the box enough width, then check the wraps on the render;
  narrow centred captions break at awkward places.
- For bilingual slides, put the primary language first at full size and the
  second language smaller and muted, or use separate slides - not both at full
  size on every line.
