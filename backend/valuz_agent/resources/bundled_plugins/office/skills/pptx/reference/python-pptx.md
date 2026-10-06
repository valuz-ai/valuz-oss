# python-pptx cookbook

Snippets for python-pptx 1.0 under `valuz-python`. The helper module
(`scripts/pptx_helpers.py`) wraps most of these; use the raw API when you need
something it does not cover.

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
```

## Units and slide size

- Lengths are EMU integers: `Inches(1) == 914400`, `Pt(1) == 12700`. Pass
  `Inches(...)`/`Pt(...)`/`Cm(...)`; read back with `.inches`, `.pt`.
- 16:9: `prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)`.
  Changing the size does not move layout placeholders; `new_presentation()`
  in the helpers rescales the default layouts for you.

## Text

```python
box = slide.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(6), Inches(2))
tf = box.text_frame
tf.word_wrap = True                    # a new text box does NOT wrap by default
tf.auto_size = MSO_AUTO_SIZE.NONE      # fixed box; size the text to fit yourself
tf.margin_left = tf.margin_right = Inches(0.05)
tf.vertical_anchor = MSO_ANCHOR.TOP
p = tf.paragraphs[0]                   # a text frame always has one paragraph
run = p.add_run(); run.text = "Revenue grew 18%"
run.font.size = Pt(18); run.font.bold = True
run.font.color.rgb = RGBColor.from_string("1B2433")
p2 = tf.add_paragraph(); p2.text = "Second paragraph"; p2.level = 1
p2.space_after = Pt(6); p2.line_spacing = 1.15; p2.alignment = PP_ALIGN.LEFT
```

- `font.name` sets only the Latin typeface. CJK text needs the East Asian
  typeface (`a:ea`): `set_font(run.font, ea="Microsoft YaHei")` from the
  helpers, or set the theme fonts with `apply_theme()`.
- Line breaks: in `tf.text`, `"\n"` starts a new paragraph and `"\v"` is a
  line break inside the paragraph (`a:br`); in `paragraph.text` both become
  `a:br`. Never put `"\n"`/`"\v"` in `run.text` (it is stored as `_x000B_`).
- `MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE` only writes a flag; PowerPoint shrinks the
  text when someone edits it, so the file opens overflowing. `tf.fit_text()`
  computes a size but needs font files. Prefer fixed sizes and shorter text.
- Bullets: a text box has no bullets. Use `add_bullets()` or set them in XML:

```python
from pptx.oxml.ns import qn
from lxml import etree
pPr = p._p.get_or_add_pPr()
pPr.set("marL", str(Pt(20))); pPr.set("indent", str(-Pt(20)))
etree.SubElement(pPr, qn("a:buFont"), typeface="Arial")
etree.SubElement(pPr, qn("a:buChar"), char="•")     # or a:buAutoNum type="arabicPeriod"
```

  Inside body placeholders of a template, bullets come from the layout: just
  set `paragraph.level`.

## Shapes, fills, lines

```python
from pptx.oxml.ns import qn
card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
card._element.remove(card._element.find(qn("p:style")))   # no theme shadow/outline/text colour
card.fill.solid(); card.fill.fore_color.rgb = RGBColor.from_string("F1F4F9")
card.line.fill.background()            # no outline
card.adjustments[0] = 0.08             # corner radius (fraction of the short side)
card.text_frame.text = "Label"         # shapes can hold text too
```

- Z-order follows creation order; draw backgrounds and cards first, text last.
  To move a shape to the back: `slide.shapes._spTree.remove(el); slide.shapes._spTree.insert(2, el)`.
- Delete a shape: `el = shape._element; el.getparent().remove(el)`.
- Slide background: `slide.background.fill.solid(); slide.background.fill.fore_color.rgb = ...`.

## Pictures

```python
pic = slide.shapes.add_picture("chart.png", Inches(1), Inches(1.6), width=Inches(5))  # height keeps ratio
pic.crop_left = 0.1                     # fractions of the original image
```

Give only width or only height to keep the aspect ratio, or use
`add_image(..., mode="contain"|"cover")`. Use local files; download nothing
at build time unless the user provided a URL and asked for it.
SVG is not supported by python-pptx; convert to PNG first (at 2x the
displayed size for sharpness).

## Charts

```python
data = CategoryChartData(number_format='#,##0')
data.categories = ["2022", "2023", "2024"]
data.add_series("Revenue", (120, 138, 163))
frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, data)
chart = frame.chart
chart.has_legend = False; chart.has_title = False
chart.font.size = Pt(12)
plot = chart.plots[0]; plot.gap_width = 70
plot.series[0].format.fill.solid(); plot.series[0].format.fill.fore_color.rgb = RGBColor.from_string("1F3A5F")
pt = plot.series[0].points[2]; pt.format.fill.solid(); pt.format.fill.fore_color.rgb = RGBColor.from_string("E07A3F")  # highlight one bar
plot.has_data_labels = True; plot.data_labels.number_format = '#,##0'; plot.data_labels.number_format_is_linked = False
va = chart.value_axis; va.has_major_gridlines = True; va.format.line.fill.background()
va.maximum_scale = 200; va.tick_labels.font.size = Pt(11)
```

- Types: COLUMN_CLUSTERED/STACKED/STACKED_100, BAR_*, LINE / LINE_MARKERS,
  PIE, DOUGHNUT, AREA, XY_SCATTER, BUBBLE, RADAR.
- Number formats: `'#,##0'`, `'0.0%'` (values as fractions), `'#,##0.0,"k"'`,
  `'"$"#,##0'`, `'0.0"x"'`.
- Replace data later: `chart.replace_data(new_chart_data)` (keeps formatting).
- No combo charts (bar + line on two axes) through the API. Use two charts
  side by side, or one chart plus data labels.
- Missing values: pass `None` in the series values.

## Tables

```python
frame = slide.shapes.add_table(rows, cols, x, y, cx, cy)
table = frame.table
table.columns[0].width = Inches(3)
cell = table.cell(0, 0); cell.text = "Region"
cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor.from_string("1F3A5F")
cell.margin_left = Inches(0.08); cell.vertical_anchor = MSO_ANCHOR.MIDDLE
cell.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
table.cell(1, 0).merge(table.cell(2, 0))      # merged cells
```

- The default table style is a blue banded style. `add_table()` in the
  helpers switches to "No Style, No Grid" and draws its own fills and rules.
- Row heights grow with content when rendered; `row.height` is a minimum.
- Cell borders need XML (`a:lnL/a:lnR/a:lnT/a:lnB` first in `a:tcPr`); see
  `_cell_border()` in the helpers.

## Notes, hyperlinks, sections

```python
slide.notes_slide.notes_text_frame.text = "Speaker notes"
run.hyperlink.address = "https://example.com"
shape.click_action.target_slide = prs.slides[3]   # jump to another slide
```

Sections (`p14:sectionLst`) have no API; `slides.py delete` keeps them
consistent when slides are removed.

## Placeholders and layouts

```python
for layout in prs.slide_layouts:
    print(layout.name, [(ph.placeholder_format.idx, ph.placeholder_format.type) for ph in layout.placeholders])
slide = prs.slides.add_slide(layout)
slide.shapes.title.text = "Title"
slide.placeholders[1].text = "Body"            # by idx, not position in the list
pic = slide.placeholders[13].insert_picture("photo.jpg")   # picture placeholder (crops to fill)
chart_frame = slide.placeholders[14].insert_chart(XL_CHART_TYPE.PIE, data)
```

Placeholders inherit position, size and formatting from the layout; setting
`.left/.top/.width/.height` overrides that one slide. Remove placeholders you
do not use (an empty placeholder shows "Click to add text" in the editor).

## Editing existing text without losing formatting

- `shape.text_frame.text = ...` and `paragraph.text = ...` drop run
  formatting. Edit `run.text` instead, or use `set_text()` (keeps each
  paragraph's first-run formatting) and `replace_text()` (handles a match
  split across runs) from the helpers.
- Mixed formatting inside one paragraph (a bold word) lives in separate runs;
  change the text of each run rather than rebuilding the paragraph.
- Text in tables and groups is not under `slide.shapes` directly: walk
  `shape.table.rows[..].cells` and `group.shapes`.

## Things python-pptx cannot do

Copy or delete slides (use `scripts/slides.py`), create SmartArt, animations or
transitions, combo charts, embed fonts, or render anything. Editing a deck
keeps such content as long as you do not rebuild the shapes that hold it.
