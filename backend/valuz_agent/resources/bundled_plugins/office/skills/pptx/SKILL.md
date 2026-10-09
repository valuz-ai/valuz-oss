---
name: pptx
description: Create, read, edit and check PowerPoint presentations (.pptx/.potx) - new decks with native charts, tables and images; filling and rearranging templates (duplicate, delete, reorder slides); extracting slide text, tables and speaker notes; thumbnail overviews; content and visual QA; conversion to PDF or slide images. Use whenever a .pptx or .potx file is an input or a requested deliverable, including "make a deck/slides/presentation" requests that should produce a PowerPoint file.
---

# PowerPoint presentations

`<skill>` below is the directory that contains this SKILL.md. Its scripts are
read-only resources: run them from the task workspace and write every output
(scripts, decks, renders) into the workspace. Match the language of the request
in slide text, and the design of a supplied deck or template when editing it.

## Environment

- **`valuz-python`** runs every script and snippet: bundled CPython 3.12 with
  python-pptx 1.0, Pillow, lxml, openpyxl, pandas. Use `valuz-python file.py` or
  `valuz-python - <<'PY' ... PY`. It is read-only; if a task truly needs another
  package, make a task venv that keeps the bundled libraries:
  `valuz-python -m venv --system-site-packages .venv && .venv/bin/pip install <pkg>`.
  If `valuz-python` is not on PATH, install what it provides and use that
  interpreter wherever this file says `valuz-python`: Python 3.12 with
  python-pptx, Pillow, lxml, openpyxl and pandas
  (plan the installation yourself, e.g. a venv).
  Follow an explicit user or AGENTS.md environment requirement when there is one.
- **`dsoffice`** is the bundled LibreOffice engine: `capabilities`, `convert`,
  `render`. Outputs must be new files/directories (it never overwrites). Never
  search for or call a system LibreOffice, `soffice` or `pdftoppm` unless the
  user explicitly asks; if `dsoffice` fails, report that.
  If `dsoffice` is not on PATH, install it: it is the `dsoffice` command of
  the npm package `@deepseek-ai/libreoffice-kit@0.1.5` (Node.js 22.19 or
  newer; plan the installation yourself).

## Scripts

| script | does |
|---|---|
| `outline.py deck.pptx [--layouts] [--geometry] [--json] [--slides 2,5-7]` | per-slide layout, title, text with levels, tables, chart data, pictures, notes; `--layouts` lists layouts and placeholder idx |
| `thumbnails.py deck.pptx --output-dir new-dir [--slides ..] [--cols 3]` | renders with dsoffice and writes labelled grid images `grid-NN.png` plus one PNG per slide |
| `slides.py duplicate\|delete\|move\|arrange in.pptx out.pptx ...` | copy (with charts, notes, media), delete, move, or rebuild the slide order (`arrange --order 1,3,3,5`) |
| `content_qa.py deck.pptx [--json] [--strict]` | leftover template/sample text, empty placeholders, likely overflow, off-slide shapes, tiny text, low contrast, overlaps, dense slides; exit 1 on errors |
| `pptx_helpers.py` (import) | 16:9 deck setup, palettes and theme fonts, text boxes, real bullets, cards, grid, native charts and tables, image fit, `set_text`/`replace_text`/`replace_image` for templates |
| `check_office.py deck.pptx [--count N] [--contains TEXT]` | package integrity (ZIP, XML, relationships), slide count, required text |

Run them as `valuz-python <skill>/scripts/<name>.py ...`; each prints usage
with `--help`. `outline.py`, `slides.py`, `content_qa.py` and `thumbnails.py`
also accept a `.potx` template; in your own code open one with
`open_presentation()` from the helpers (plain python-pptx rejects .potx) and
save the result as `.pptx`.

## Choose the workflow

- **Read or summarise a deck**: `outline.py` (add `--json` to process it). Add
  `thumbnails.py` when the look matters. Speaker notes are in the outline.
- **Small edit to an existing deck**: python-pptx on a copy; change only the
  requested content and keep its formatting (see "Editing" below).
- **Deck from a template, or restructuring a deck**: follow
  [reference/templates.md](reference/templates.md) - inventory, map, `arrange`,
  fill, QA.
- **New deck from scratch**: read [reference/design.md](reference/design.md)
  first, then build with the helper module (next section).

Every workflow that writes a deck ends with the QA loop and delivery.

## New deck from scratch

1. Plan in text before code: audience, the slide list with one takeaway title
   per slide, and the layout pattern for each slide (cover, KPI row, chart +
   takeaways, comparison, table, process, closing). Vary the patterns.
2. Pick a palette from the brand or subject (`PALETTES` or your own `Theme`)
   and keep the type scale and grid from design.md.
3. Build with python-pptx through the helpers. Native charts and tables, real
   bullets, explicit box sizes, speaker notes for detail:

```python
import sys
sys.dont_write_bytecode = True               # the skill directory is read-only
sys.path.insert(0, "<skill>/scripts")
from pptx_helpers import *

theme = PALETTES["navy"]                       # or Theme(primary="0B5CAD", ...)
prs = new_presentation(theme=theme)            # 16:9, theme colours + Latin/CJK fonts

s = blank_slide(prs, theme)                    # cover
add_box(s, 0, 0, 0.18, 7.5, fill=theme.primary)
add_text(s, 0.9, 2.3, 8, 1.6, "2025 年度经营回顾", theme=theme, size=44, bold=True, anchor="bottom")
add_text(s, 0.9, 4.0, 8, 0.5, "FY2025 Business Review · 2026-01", theme=theme, size=18, color=theme.muted)

s = content_slide(prs, "Revenue grew 18% on APAC demand", theme)
chart_box, notes_box = columns(theme.content_box(), 2, weights=[2, 1])
add_chart(s, "column", ["2022", "2023", "2024", "2025"], {"Revenue (USD m)": [120, 138, 163, 192]},
          *chart_box, theme=theme, data_labels=True, number_format="#,##0", legend=None)
add_bullets(s, *notes_box, ["APAC +32% YoY", "Gross margin 41.5%", "Two new plants"], theme=theme)
set_notes(s, "Source: management accounts, unaudited.")

prs.save("deck.pptx")
```

   Helper reference (all positions in inches, colours "RRGGBB"):
   - `add_text(slide, x, y, w, h, content, size=, color=, bold=, align=, anchor=)`
   - `add_bullets(slide, x, y, w, h, items, numbered=False)`; items are str,
     `(text, level)` or dict(text, level, bold, color, size)
   - `add_box(slide, x, y, w, h, fill=, line=, kind="rect"|"rounded"|"oval", text=)`
   - `add_chart(slide, kind, categories, series, x, y, w, h, number_format=,
     data_labels=, legend=, colors=)`; kinds: column, stacked_column, bar,
     stacked_bar, line, line_plain, area, pie, doughnut, scatter
   - `add_table(slide, rows, x, y, w, col_widths=, align=, zebra=, bold_last_row=)`
   - `add_image(slide, path, x, y, w, h, mode="contain"|"cover")`
   - `columns(box, n, gap, weights)`, `rows(...)`, `theme.content_box(subtitle=False)`
   - `set_background`, `set_notes`, `set_font(font, size=, color=, name=, ea=)`

   Raw python-pptx recipes and limits: [reference/python-pptx.md](reference/python-pptx.md).
4. Rules that keep decks clean:
   - 16:9 (13.333 x 7.5 in) unless the user or template says otherwise.
   - Text boxes: word wrap on, autofit off, a size that fits the text at the
     intended font size. Do not rely on shrink-to-fit.
   - One title per slide in the same place; use the title placeholder
     (`content_slide`) so titles show in PowerPoint's outline.
   - Bullets come from paragraph formatting, never typed "•" or "-".
   - Charts and tables are native objects with the real data; images are local
     files placed without distortion.
   - Set an East Asian font for CJK text (the theme does this via `new_presentation(theme=...)`).

## Editing an existing deck

- Inspect first (`outline.py --geometry`), then open with python-pptx, change
  what was asked, and save to a new file unless the user asked for an in-place
  edit.
- Keep run formatting: change `run.text`, or use `set_text(shape, ...)` and
  `replace_text(prs_or_slide_or_shape, old, new)` from the helpers. Assigning
  `shape.text_frame.text` drops formatting.
- Slide copy/delete/reorder: `scripts/slides.py` (python-pptx has no API for it).
- Rebuilding shapes loses what python-pptx does not model (animations,
  SmartArt, some effects); edit in place instead.
- Notes: `slide.notes_slide.notes_text_frame.text = "..."`.

## QA loop (required before delivery)

1. Structure: `valuz-python <skill>/scripts/check_office.py deck.pptx --count N`
   (add `--contains "..."` for must-have text). It must pass.
2. Content: `valuz-python <skill>/scripts/content_qa.py deck.pptx`. Fix every
   error. Treat warnings as items to confirm on the render; fix the real ones.
   Text-size estimates are heuristics in both directions.
3. Visual (when the current model accepts images):
   `valuz-python <skill>/scripts/thumbnails.py deck.pptx --output-dir qa-v1`,
   then open each `grid-NN.png` with your file/image reading tool. For a close
   look, open the per-slide PNGs listed under `slide_images` in its JSON, or
   render chosen slides larger:
   `dsoffice render --input deck.pptx --output-dir qa-v1-detail --pages 3,5 --dpi 110`
   (files are numbered in output order - `page-0001.png` is slide 3 here;
   `manifest.json` maps each image to its slide in `page`).
   Check: text clipped or overflowing, overlaps, uneven alignment or spacing,
   low contrast, empty areas or crowding, chart labels and values, wrong or
   missing fonts (`missingFonts` in the JSON), leftover sample content.
4. Fix and repeat with a new output directory (`qa-v2`, ...) until steps 1-3
   are clean. Re-render only the slides you changed, plus a final full grid.

If images cannot be read by the current model, finish steps 1-2 and say that
visual layout was not inspected. Previews come from LibreOffice: they do not
certify pixel-identical PowerPoint output, animations or media. Fonts that are
not installed are substituted in the preview (see `missingFonts`), so leave a
little slack in tight boxes. One known difference: a text box with word wrap
off renders wrapped in the preview but as one long line in PowerPoint -
`content_qa.py` reports that case.

## Convert and deliver

- PDF: `dsoffice convert --input deck.pptx --output deck.pdf`.
- Slide images: `dsoffice render --input deck.pptx --output-dir slides-png [--pages 1,3] [--dpi 144]`
  (PNG per slide plus `manifest.json`; max 144 dpi, 100 slides per call).
- Other: pptx/odp conversions via `dsoffice convert`; `dsoffice capabilities`
  lists them.

Deliver the final files with the session tool `deliver_artifacts`, using
absolute paths inside the working directory, for example
`deliver_artifacts({"attachments":[{"filePath":"/abs/path/deck.pptx"}]})`. Add
the PDF when it was requested. Do not deliver QA renders, outlines or scratch
files unless asked. If `deliver_artifacts` is not available, give the final
path.
