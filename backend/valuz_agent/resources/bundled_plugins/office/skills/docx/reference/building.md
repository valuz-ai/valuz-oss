# Building documents with python-docx and docx_helpers

`scripts/docx_helpers.py` adds what python-docx 1.2 lacks. Import it at the top
of the build script, before `Document(...)`, so existing footnote/endnote parts
load as editable XML:

```python
import sys
sys.path.insert(0, "/abs/path/to/this/skill/scripts")
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Mm, Pt, RGBColor
from docx_helpers import *
```

Lengths are python-docx lengths (`Cm`, `Mm`, `Pt`, `Inches`); colours are
`"RRGGBB"` strings; font sizes are points (numbers) or lengths.

## Plain python-docx you will use anyway

```python
doc = Document()                                  # or Document("template.docx")
doc.add_heading("Title text", level=1)            # Heading 1..9; level=0 is "Title"
p = doc.add_paragraph("Body text", style="Normal")
r = p.add_run(" bold red"); r.bold = True; r.font.color.rgb = RGBColor(0xC0, 0, 0)
p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
pf = p.paragraph_format
pf.space_before, pf.space_after = Pt(0), Pt(6)
pf.line_spacing = 1.15                            # or Pt(18) for exact
pf.first_line_indent = Cm(0.74)                   # two CJK characters at 10.5 pt
pf.keep_with_next = True                          # keep a caption with its table
doc.add_page_break()
section = doc.sections[0]
section.header.paragraphs[0].text = "Company - Confidential"
section.different_first_page_header_footer = True # then section.first_page_header
doc.settings.odd_and_even_pages_header_footer = True  # then section.even_page_header
```

python-docx 1.2 can also add comments while building:
`doc.add_comment(runs=[run], text="Check this figure", author="Valuz", initials="V")`.
For comments and revisions on existing files use `docx_review.py`.

## Page setup and sections

| Helper | Does |
|---|---|
| `set_page_layout(section, "A4"\|"Letter"\|"A3"\|"A5"\|"Legal", "portrait"\|"landscape", margins=Cm(2.5) or (top, right, bottom, left))` | paper, orientation, margins |
| `text_width(section)` / `text_height(section)` | usable area, for widths and tab stops |
| `add_section(doc, "new_page"\|"continuous"\|"odd_page"\|"even_page", orientation=None, columns=None)` | starts a section for the content added next and returns it |
| `set_orientation(section, "landscape")` | swaps width and height |
| `set_columns(section, 2, space=Cm(1), separator=False)` | newspaper columns |
| `add_column_break(paragraph)` | that paragraph starts at the top of the next column |
| `set_page_number_start(section, start=1, fmt="lowerRoman")` | restart numbering / roman front matter |

python-docx's default template is US Letter with 1-inch margins; set the page
explicitly. A new section copies the previous one's page setup and columns and
links its headers/footers to the previous section. Typical column layout:

```python
add_section(doc, "continuous", columns=2)        # two columns from here
...paragraphs...
add_section(doc, "continuous", columns=1)        # back to one column
add_section(doc, "new_page", orientation="landscape")   # wide table on its own page
add_section(doc, "new_page", orientation="portrait")
```

At a continuous section break Word and LibreOffice balance the columns, so a
short passage is spread over all columns. To decide where each column starts,
use `add_column_break`, or end the column section with a `"new_page"` section.

## Fonts and styles

```python
set_document_fonts(doc, latin="Calibri", east_asia="SimSun", size=10.5, east_asia_lang="zh-CN")
set_style_font(doc, "Heading 1", latin="Arial", east_asia="SimHei", size=16, bold=True,
               color="1F3864", space_before=Pt(12), space_after=Pt(6))
set_style_font(doc, "Title", east_asia="SimHei", size=24)
set_run_font(run, latin="Times New Roman", east_asia="KaiTi", size=12, italic=True)
```

`set_document_fonts` writes the document defaults and removes theme-font
references from every style, so headings, tables and captions follow your
fonts. Word picks the font per character: Latin text uses `ascii`/`hAnsi`,
CJK text uses `eastAsia`; setting only `run.font.name` leaves CJK characters
in another font. `east_asia_lang` marks CJK text as Chinese/Japanese/Korean
(punctuation spacing, line breaking).

Custom styles: `doc.styles.add_style("Note", WD_STYLE_TYPE.PARAGRAPH)` then set
`style.base_style`, `style.font`, `style.paragraph_format`.

## Lists

```python
bullets = create_list(doc, "bullet")              # •, ◦, ▪ by level
add_list_item(doc, "First point", bullets)
add_list_item(doc, "Detail", bullets, level=1)
steps = create_list(doc, "number")                # 1. / a. / i. by level
add_list_item(doc, "Prepare", steps)
clauses = create_list(doc, "outline")             # 1. / 1.1. / 1.1.1. for contracts
add_list_item(doc, "Definitions", clauses)
add_list_item(doc, "Affiliate means ...", clauses, level=1)
```

Each `create_list` call is a new list whose numbering starts at `start`
(default 1). `add_list_item` works on any container (document, table cell,
header) and returns the paragraph, so you can add runs. For a single simple list
the built-in "List Bullet" / "List Number" styles also work, but all
"List Number" paragraphs share one counter.

## Tables

```python
rows = [["Region", "Revenue ($m)", "YoY"], ["Asia", "1,234", "12.0%"], ["Europe", "987", "-3.5%"]]
t = add_table(doc, rows, col_widths=[Cm(7), Cm(4), Cm(3)], align=["left", "right", "right"],
              font_size=10, header_fill="D9E2F3", style="Table Grid")
t.cell(1, 1).merge(t.cell(1, 2))                  # merge (python-docx); text is concatenated
shade_cell(t.cell(2, 0), "FFF2CC")
set_cell_borders(t.cell(2, 0), bottom={"sz": 12, "color": "C00000"}, top=None)
set_table_borders(t, size=4, color="808080", inside=False)
set_col_widths(t, [Cm(6), Cm(5), Cm(3)])          # resize later; merged cells get the sum
set_repeat_header(t.rows[0]); keep_row_together(t.rows[3])
```

- `add_table` makes the first row a bold, shaded header that repeats on every
  page, fixes widths (default: equal columns across the text width) and aligns
  columns. Right-align numbers.
- Vertical alignment: `cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER`
  (`docx.enum.table`). Table position: `t.alignment = WD_TABLE_ALIGNMENT.CENTER`.
- Border `sz` is in eighths of a point (4 = 0.5 pt, 12 = 1.5 pt); `None` removes
  an edge.
- Widths must fit `text_width(section)`; a wide table goes in a landscape section.

## Pictures

```python
add_picture_fit(doc, "chart.png")                         # fit text width and 85% of the height
add_picture_fit(cell, "logo.png", max_width=Cm(3))        # in a table cell or header: give a size
doc.add_paragraph("Figure 1: Revenue by quarter", style="Caption")
```

The picture keeps its aspect ratio and is never enlarged past its native size
(pixels / dpi). Save charts at 150-200 dpi with the size you want on the page.

## Links, bookmarks, footnotes

```python
add_hyperlink(p, "investor site", "https://example.com/ir")      # blue, underlined
add_bookmark(heading_paragraph, "risk_factors")
add_internal_link(p, "see Risk factors", "risk_factors")
p = doc.add_paragraph("Revenue grew 12%")
add_footnote(p, "Source: company filings.")        # reference goes at the current end of p
p.add_run(" in 2025.")
add_endnote(p, "Methodology in the appendix.")
```

Build a paragraph left to right: runs, then the note, then more runs. Notes can
only be referenced from the main body (not headers). `add_footnote` returns the
note's paragraph if you want to format it.

## Tab stops

```python
p = doc.add_paragraph("Revenue")
add_tab_stop(p)                                    # right-aligned at the right margin, dot leader
p.add_run("\t1,234")
add_tab_stop(p, Cm(8), align="decimal", leader=None)
```

Leaders: `"dot"`, `"hyphen"`, `"underscore"`, `"middle_dot"`, `None`.
python-docx's own `paragraph_format.tab_stops.add_tab_stop` also works.

## Fields, page numbers, table of contents

```python
add_page_number_footer(section, "Page {PAGE} of {NUMPAGES}", align="center", size=9)
add_page_number_footer(section, "第 {PAGE} 页，共 {NUMPAGES} 页")
add_page_number_footer(section, "{PAGE}", first_page=True)  # needs different_first_page_header_footer
add_field(p, 'DATE \\@ "yyyy-MM-dd"', placeholder="2026-10-04")
add_field(p, "SECTIONPAGES", placeholder="1")
add_toc(doc, "1-3", title="Contents")             # placeholder field; fill with docx_toc.py
update_fields_on_open(doc)                         # Word offers to refresh all fields on open
```

- `PAGE`, `NUMPAGES`, `SECTIONPAGES` are recomputed by Word, LibreOffice and
  `dsoffice` every time they lay the page out; the placeholder only matters to
  tools that do not lay out pages.
- `add_toc` inserts a TOC field (`\o "1-3" \h \z \u`) with a placeholder.
  After saving run `valuz-python <skill>/scripts/docx_toc.py draft.docx final.docx`.
  It bookmarks each heading, writes the entries (hyperlinked, dot leaders,
  `PAGEREF` page numbers) inside the field and fills the page numbers from a
  `dsoffice` layout pass; its JSON lists the entries and pages. Without
  `dsoffice` the page numbers stay empty and the JSON says so. It also refreshes
  a stale TOC in an existing Word file. Only heading styles / outline levels in
  the `\o` range are collected (custom-style `\t` switches are not read).
- Word displays the stored TOC until the user updates it; LibreOffice and
  `dsoffice` always display the stored entries. Re-run `docx_toc.py` after
  editing headings or content.

## Replacing text without revision marks

```python
count = replace_text(doc, "FY2025", "FY2026")      # body, tables, headers, footers
```

Keeps the formatting of the run where each match starts, also across runs.
Use `docx_review.py` instead when the change must be visible as a revision.
