# Working from a template or an existing deck

Use this when the user gives a .pptx/.potx to follow, or asks to update,
reorder or extend an existing deck. The template's design (layouts, theme
colours, fonts, spacing) is the spec: do not restyle it unless asked.

`<skill>` below means this skill's directory.

## 1. Inventory the template

```bash
valuz-python <skill>/scripts/outline.py template.pptx --layouts --geometry > template-outline.txt
valuz-python <skill>/scripts/thumbnails.py template.pptx --output-dir template-thumbs
```

Open the grid image(s) and the outline together and write down, per template
slide: what kind of slide it is (cover, section, 3-column, chart, table,
quote, closing), how much text each box holds, and which shapes are sample
content. The scripts accept a .potx as well; in Python open it with
`open_presentation("template.potx")` from the helpers and save as .pptx
(`slides.py arrange template.potx work.pptx ...` does the same).

If the template has layouts but few or no example slides, build slides from
its layouts (`prs.slides.add_slide(prs.slide_layouts[i])`) and fill the
placeholders by idx (listed by `--layouts`).

## 2. Map content to template slides

Write the mapping before editing, e.g.

```
new 1  <- template 1  cover
new 2  <- template 3  agenda (4 items; template holds 5 -> delete the 5th row)
new 3  <- template 6  chart + text
new 4  <- template 6  chart + text (second chart)
new 5  <- template 8  3 columns
new 6  <- template 12 closing
```

Pick the template slide whose structure fits the content (3 points -> the
3-column slide, not the 4-column one with a column left empty). Use a variety
of slide types.

## 3. Build the slide structure

```bash
valuz-python <skill>/scripts/slides.py arrange template.pptx work.pptx --order 1,3,6,6,8,12
valuz-python <skill>/scripts/outline.py work.pptx --geometry
```

`arrange` keeps the listed slides in the listed order, duplicates repeats
(charts, notes and per-slide parts are copied; images are shared) and deletes
the rest. Single operations: `duplicate --slide N [--to M] [--copies K]`,
`delete --slides 2,5-7`, `move --slide N --to M`. Each writes a new file and
prints which source slide each output slide came from.

## 4. Fill the content

Edit with python-pptx, addressing shapes by name or placeholder idx from the
outline. Keep the template's formatting:

```python
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, "<skill>/scripts")
from pptx import Presentation
from pptx_helpers import set_text, replace_text, replace_image

prs = Presentation("work.pptx")          # open_presentation(...) for a .potx
s = prs.slides[0]
set_text(s.shapes.title, "2025 年度经营回顾")                  # keeps the title's font, size, colour
body = next(sh for sh in s.placeholders if sh.placeholder_format.idx == 1)
set_text(body, ["Revenue +18%", ("APAC +32%", 1), "Margin 21.4%"])  # (text, level) for sub-points
replace_text(prs, "{{company}}", "Valuz Robotics")              # tokens anywhere, even split across runs
pic = next(sh for sh in prs.slides[2].shapes if sh.shape_type == 13)  # MSO_SHAPE_TYPE.PICTURE
replace_image(pic, "assets/factory.jpg")                          # same frame, cropped to fit
prs.save("filled.pptx")
```

- Charts: `shape.chart.replace_data(chart_data)` swaps the data and keeps the
  styling; update the series names and number format too. Check the value
  axis afterwards: with automatic scaling a bar chart may no longer start at
  zero (`chart.value_axis.minimum_scale = 0`).
- Tables: write each cell with `set_text(cell, "...")` to keep the cell
  style. To add a row, copy the last row:
  `tr = copy.deepcopy(table._tbl.tr_lst[-1]); table._tbl.append(tr)`, then set
  its text; to delete a row, `table._tbl.remove(table._tbl.tr_lst[i])`. The
  frame height is not updated automatically - check the render.
- Remove what you do not use: an unused third column, a sample photo, an empty
  placeholder (`el = shape._element; el.getparent().remove(el)`). Never leave
  sample text or empty placeholders behind.
- When the new text is longer than the sample text, shorten it first; the
  template's boxes are sized for the sample. Only then consider a smaller font
  (not below the sizes in `reference/design.md`).
- Keep animations, transitions and SmartArt by editing text in place; do not
  delete and recreate the shapes that carry them.

## 5. Check

Run the QA loop from SKILL.md. `content_qa.py` reports leftover sample text
(including "Click to add ..." prompts and "Lorem ipsum"), empty placeholders
and text that no longer fits. Compare the new thumbnail grid with the
template grid: same margins, fonts and colours, no shifted boxes.
