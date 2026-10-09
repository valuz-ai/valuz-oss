---
name: docx
description: Create, read, edit, review, convert and check Word documents (.docx, also .doc/.odt input) - reports, letters, contracts and formatted tables, with headings, lists, tables, images, footnotes, hyperlinks, multi-column sections, page numbers and tables of contents, Chinese/Japanese/Korean text, tracked changes (insert/delete/replace with author and date, accept or reject all) and comments. Use when a Word file is an input or the requested deliverable. Runs on the bundled valuz-python and dsoffice commands; do not search for another Python or LibreOffice.
---

# Word documents

Use `python-docx` for creating documents and ordinary edits, and this skill's
scripts for what python-docx cannot do. Follow an explicit user or AGENTS.md
requirement for a project environment or another library when there is one.

In this file `<skill>` means this skill's directory; the scripts are in
`<skill>/scripts/`. Keep source scripts, intermediate files and final documents
in the task workspace; the runtime and the skill directory are read-only. Use the
user's language and keep an existing document's design unless a redesign is
requested. Save to a new file unless the user asks for an in-place edit.

## Environment

- `valuz-python` is the bundled Python 3.12 with python-docx 1.2, lxml, Pillow,
  openpyxl, pandas. Run every script and snippet with it:
  `valuz-python build.py`, `valuz-python - <<'PY' ... PY`. It is read-only; if a
  task really needs another package, make a task venv that keeps the bundled
  libraries: `valuz-python -m venv --system-site-packages .venv && .venv/bin/pip install <pkg>`.
  If `valuz-python` is not on PATH, install what it provides and use that
  interpreter wherever this file says `valuz-python`: Python 3.12 with
  python-docx, lxml, Pillow, openpyxl and pandas
  (plan the installation yourself, e.g. a venv).
- `dsoffice` is the bundled LibreOffice engine: `convert`, `render`,
  `capabilities`. Outputs must be new paths/directories. Its JSON result lists
  `missingFonts`. Never search for or call a system LibreOffice, `soffice`,
  `pdftoppm` or a downloaded converter unless the user explicitly asks; if
  `dsoffice` fails, report that.
  If `dsoffice` is not on PATH, install it: it is the `dsoffice` command of
  the npm package `@deepseek-ai/libreoffice-kit@0.1.5` (Node.js 22.19 or
  newer; plan the installation yourself).

## Choose the workflow

| Task | Tool |
|---|---|
| Read, summarize, extract | `docx_inspect.py text` |
| See revisions / comments | `docx_inspect.py changes` / `comments` |
| `.doc` / `.odt` input | `dsoffice convert` to `.docx` first |
| New document | python-docx + `docx_helpers.py` ([reference/building.md](reference/building.md)) |
| Change wording or formatting, no revision marks | python-docx run-level edits, `replace_text` |
| Tracked changes, comments, accept/reject all | `docx_review.py` ([reference/review.md](reference/review.md)) |
| Table of contents | `add_toc` in the build script, then `docx_toc.py` |
| Something python-docx cannot reach | `docx_pack.py` unpack, edit XML, pack ([reference/xml-editing.md](reference/xml-editing.md)) |
| PDF or page images | `dsoffice convert` / `dsoffice render` |

## Read

```sh
valuz-python <skill>/scripts/docx_inspect.py text report.docx            # outline
valuz-python <skill>/scripts/docx_inspect.py text report.docx --view markup
valuz-python <skill>/scripts/docx_inspect.py changes report.docx
valuz-python <skill>/scripts/docx_inspect.py comments report.docx
```

`text` prints the body in reading order as a light Markdown outline: `#`
headings, list items with their numbers, `| table | rows |`, `[image: alt]`,
footnote markers `[^1]` with the notes listed after the body, external links
as `[text](url)`, page-number fields as `{PAGE}`, fields without a stored
result as `{CODE}`, and headers/footers per section.
Tracked changes are shown accepted (`--view final`); `--view original` shows
them rejected and `--view markup` shows `{+inserted+}` and `{-deleted-}`.
`changes` lists type, author, date, part, paragraph number and text of every
revision; `comments` lists author, date, the anchored text, the comment, reply
parent and resolved state. Add `--json` for machine-readable output. Copy
search text for edits from this output: it is exactly what the review script
matches against.

## Convert

```sh
dsoffice convert --input old.doc --output old.docx       # .doc/.odt -> .docx, then work on the .docx
dsoffice convert --input report.docx --output report.pdf
dsoffice render  --input report.docx --output-dir pages-v1 --pages 1,3 --dpi 144
```

`render` writes one PNG per page plus `manifest.json` (page count, image paths,
missing fonts). Output files and directories must be new; use `-v2`, `-v3` names
after changes. Check a converted `.doc` with `docx_inspect.py text` before
editing it, and deliver `.docx` unless the user asks for another format.

## Create

Write a build script; import the helpers before creating or opening documents:

```python
import sys
sys.path.insert(0, "<skill>/scripts")          # this skill's absolute path
from docx import Document
from docx.shared import Cm, Pt
from docx_helpers import *

doc = Document()
set_page_layout(doc.sections[0], "A4", margins=Cm(2.5))   # python-docx defaults to US Letter
set_document_fonts(doc, latin="Calibri", east_asia="Microsoft YaHei", size=11, east_asia_lang="zh-CN")
set_style_font(doc, "Heading 1", latin="Arial", east_asia="SimHei", size=16, color="1F3864")
add_page_number_footer(doc.sections[0], "第 {PAGE} 页 / 共 {NUMPAGES} 页")

doc.add_paragraph("2026 年度报告", style="Title")
add_toc(doc, "1-3", title="目录")
doc.add_page_break()
doc.add_heading("1 概述 Overview", level=1)
p = doc.add_paragraph("Revenue grew 12%")
add_footnote(p, "Source: company filings, 2026-09-30.")
p.add_run(". Details: ")
add_hyperlink(p, "investor site", "https://example.com/ir")
items = create_list(doc, "bullet")
for text in ("Revenue: 1.2 bn", "Margin: 41%"):
    add_list_item(doc, text, items)
add_table(doc, [["Region", "Revenue"], ["Asia", "1,234"]], col_widths=[Cm(8), Cm(4)], align=["left", "right"])
add_picture_fit(doc, "chart.png")
doc.save("draft.docx")
```

```sh
valuz-python <skill>/scripts/docx_toc.py draft.docx report.docx   # only when the document has add_toc
```

Rules:

- Use paragraph styles for headings (`add_heading` / `Heading N`) and body text;
  the TOC, the navigation pane and outline readers depend on them.
- Lists must be real Word lists (`create_list` + `add_list_item`, or the
  "List Bullet"/"List Number" styles). Never type "•", "-" or "1." into the text.
  Each `create_list` call starts a new list, so numbering restarts.
- Set page size and margins explicitly. Fix table column widths
  (`add_table(col_widths=...)` / `set_col_widths`) and repeat header rows on
  long tables. Fit pictures to the text area (`add_picture_fit`).
- CJK text needs an East Asian font (`w:eastAsia`) in addition to the Latin
  font: `set_document_fonts(..., east_asia=...)` for the whole document,
  `set_run_font(run, east_asia=...)` for single runs. Prefer the fonts Word users
  have: SimSun/宋体, SimHei/黑体, Microsoft YaHei/微软雅黑, KaiTi/楷体, FangSong/仿宋;
  `dsoffice` maps these to installed equivalents when it renders. For other
  fonts, check `missingFonts` in the `dsoffice` result.
- Built-in heading/title styles use theme fonts and colours; `set_style_font`
  and `set_document_fonts` remove those theme references so your values apply.
- Fields are computed by the program that displays the file. `PAGE`,
  `NUMPAGES` and `SECTIONPAGES` (from `add_page_number_footer`/`add_field`)
  are correct in Word, LibreOffice and `dsoffice` output without any update
  step. A table of contents is different: python-docx cannot paginate and
  LibreOffice does not rebuild a TOC field when it opens a file, so run
  `docx_toc.py` after saving. It writes one entry per heading inside the TOC
  field and stores the page numbers from a `dsoffice` layout pass. Word shows
  those stored entries as they are until someone updates the table (F9 /
  Update Table); `--update-on-open` makes Word offer to refresh all fields on
  open (it asks the user first). Re-run `docx_toc.py` after content changes.
- python-docx does not paginate or render. Keep the document's own structure:
  sections, headers/footers, numbering and styles are easier to keep than to
  rebuild.

See [reference/building.md](reference/building.md) for every helper
(sections and columns, orientation changes, tab stops with dot leaders,
bookmarks and internal links, cell shading/borders/merging, endnotes, fields)
with examples.

## Edit an existing document

Inspect paragraphs, runs, tables, sections, headers and footers before changing
the affected content. Assigning `paragraph.text` destroys run formatting; change
the relevant runs, or use `replace_text(doc, old, new)` which keeps the
formatting of the run where each match starts (body, tables, headers, footers).
Reconstructing a whole document loses features python-docx does not model, so
edit in place. When real revisions or features python-docx cannot reach matter
(content controls, fields, text boxes, SmartArt), keep their package parts
untouched and edit the XML directly: [reference/xml-editing.md](reference/xml-editing.md).

## Review: tracked changes and comments

Use real revisions only when the user asks for tracked changes, a redline or a
review; do not imitate them with coloured or struck-through text, and do not
turn ordinary edits into revisions.

```sh
S=<skill>/scripts
valuz-python $S/docx_review.py replace  in.docx out.docx --find "30 days" --with "45 days" --author "Jane Doe" --comment "Per legal"
valuz-python $S/docx_review.py delete   in.docx out.docx --find " in full"
valuz-python $S/docx_review.py insert   in.docx out.docx --anchor "Annex A" --text " and Annex B"
valuz-python $S/docx_review.py comment  in.docx out.docx --find "Section 4" --text "Please confirm the date."
valuz-python $S/docx_review.py apply    in.docx out.docx --author "Jane Doe" --ops edits.json
valuz-python $S/docx_review.py accept-all in.docx clean.docx      # or reject-all
```

Revisions are `w:ins`/`w:del` with author and date and keep the formatting of
the text they replace; comments are anchored to the matched range. Keep each
change as small as the meaning allows (replace "30 days", not the sentence), use
the user's name as author when known, and batch many edits with `apply`.
Paragraph-level operations and the ops file format are in
[reference/review.md](reference/review.md). Verify with
`docx_inspect.py changes`/`comments` and a render.

## Check and deliver

Run the checker with `valuz-python`:

```sh
valuz-python <skill>/scripts/check_office.py report.docx --out checks.json --contains "Revenue"
```

It checks ZIP/XML integrity and internal relationships, and reports paragraphs,
logical table dimensions and sections. `--contains TEXT` asserts required text.
A passing structural check does not verify pagination, clipping, fonts or visual
appearance. Compare the summary and the reopened document with the request,
including unchanged content that matters to an edit.

Render for a requested image/PDF deliverable or an actionable layout check.
Before generating images only for inspection, make sure the current model accepts
images and open the PNGs with your own file/image reading tool. If image input
is unavailable, finish the structural and content checks and say that visual
layout was not inspected. Choose the pages the request affects; inspect all
pages when whole-document layout matters. Check page breaks, clipped text,
headings, table widths, footnotes at page bottoms, columns and page numbers.
A direct `.docx` render also shows LibreOffice's non-printing marks (grey field
shading behind a TOC, thin frames around sections and pictures, comment
anchors and a comment margin); for the printed look, convert to PDF and render
the PDF. LibreOffice pagination can differ slightly from Microsoft Word. Reuse
images of an unchanged document; render again only after a change.

Deliver the final file with the session tool:
`deliver_artifacts({"attachments":[{"filePath":"/abs/path/report.docx"}]})`
(absolute path inside the working directory). If that tool is unavailable, give
the final path. Do not deliver drafts, renders or check reports unless asked.
