# Financial model conventions

Apply these when you build or edit a financial model: projections, budgets,
three-statement models, DCF or multiples valuations, LBO and scenario or
sensitivity sheets. A reader should be able to tell, without clicking into
cells, which numbers were typed, which were calculated, where each came from
and what unit it is in.

`$SKILL` below is this skill's directory.

## 1. An existing workbook's conventions win

Before editing a model, find out how it is already built:

- font family and size, header styles, column widths;
- how inputs and formulas are coloured (run
  `valuz-python $SKILL/scripts/model_audit.py model.xlsx` and read
  `colour_coding.observed`; a note appears when the file uses its own scheme);
- number formats, units, sign convention (are costs negative?), decimals;
- which columns hold which periods, where inputs live, named ranges.

Extend the file in that style. When you add a row or column, copy the style of
the neighbouring cell instead of applying this guide's defaults:

```python
from copy import copy

def copy_style(source, target):
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.alignment = copy(source.alignment)
    target.number_format = source.number_format
```

Apply the scheme below to new workbooks, or to an existing one only when the
user asks for it to be reformatted.

## 2. Layout

- Keep inputs apart from calculations: an `Inputs` (or `Assumptions`) sheet, or
  a marked input block at the top of each sheet.
- Put time across columns, one period per column, and use the same column for
  the same period on every sheet, so `=IS!E12` and `=BS!E12` are the same year.
- One line item per row with its label in column A. Calculations flow left to
  right and top to bottom; totals sit below the items they add up.
- Mark historical and projected periods in the header (`FY2024A`, `FY2025E`).
- Freeze panes below the header row and right of the labels.

## 3. Colour coding (font colour)

| Cell content | Font colour | RGB |
|---|---|---|
| Typed number: input, assumption, historical figure | blue | `0000FF` |
| Formula that calculates from cells on the same sheet | black | `000000` |
| Formula that reads another sheet of this workbook | green | `008000` |
| Formula that links to another workbook file | red | `FF0000` |
| Key assumption to review first (optional) | light yellow fill | `FFF2CC` |

- Colour the font, not the cell; text labels stay black. Use fills sparingly:
  key assumptions and check cells only.
- A formula that mixes this sheet and another sheet counts as reading another
  sheet (green). A tidy pattern is to bring each assumption into the model
  sheet once through a green link row, then calculate in black from that row.
- Avoid links to other workbooks: they break when the file moves and cannot be
  refreshed by `dsoffice`. If one is unavoidable, colour it red and name the
  source file in a note.

## 4. Number formats

| Content | Shows | `number_format` |
|---|---|---|
| Amounts | `1,234.5` `(1,234.5)` `-` | `#,##0.0_);(#,##0.0);"-"_)` |
| Whole amounts | `1,235` `(1,235)` `-` | `#,##0_);(#,##0);"-"_)` |
| Per-share values | `1.25` `(0.40)` `-` | `#,##0.00_);(#,##0.00);"-"_)` |
| Percentages | `8.0%` `(3.5%)` `-` | `0.0%_);(0.0%);"-"_)` |
| Multiples | `8.5x` | `0.0"x"_)` |
| Dates | `2025-12-31` | `yyyy-mm-dd` |

- The four sections are `positive;negative;zero;text`. `_)` leaves a space as
  wide as `)` so positive numbers line up with bracketed negatives, and `"-"`
  shows a dash for zero.
- Put the unit in the header or label, once: `Revenue (US$ mm)`,
  `收入（人民币百万元）`. Store values in that unit (1,250 means US$1.25bn when
  the unit is US$ mm) and never mix units in one column.
- Store percentages as fractions (`0.08`), not `8`. Use the same number of
  decimals for figures of the same kind.
- Years and periods are labels: write `"FY2025E"` or the string `"2025"`. A
  year stored as a number with a thousands format shows as `2,025`.
- Keep the currency symbol in the header rather than in every cell.

## 5. Formula rules

- **Every assumption in its own cell.** Formulas reference it; they do not
  contain it. `=C4*1.08` hides the growth rate; `=C4*(1+$B$3)` shows it.
  Structural constants such as the `1` in `1+g` are fine; unit conversions
  (1,000, 12 months) go in a labelled input cell.
- **One formula shape per row.** Write the first projection period, then the
  same relative formula across the row (in Python, generate it in a loop).
  Deliberate exceptions (first projection year, terminal year) should be
  visibly different: a separate row, or a comment explaining why.
- **Anchor on purpose.** `$B$3` for one assumption, `B$3` for a row of yearly
  assumptions read down a column, `$B3` for a column read across a row.
- **No circular references** unless the model needs one (interest on average
  debt is the usual case). Then add a 1/0 switch cell that breaks the loop,
  turn on iteration with `wb.calculation.iterate = True` (and
  `iterateCount = 100`, `iterateDelta = 0.001`), and tell the user. An
  unintended loop shows `#VALUE!` in every cell of the loop after
  recalculation; `recalc_check.py` lists them under `circular_references`.
- **Readable formulas.** Split long formulas into helper rows. Prefer
  `INDEX`/`MATCH`, `SUMIFS` and `IF` over volatile `OFFSET`, `INDIRECT`,
  `TODAY` and `RAND`, which hide dependencies and recalculate constantly.
- **Handle edge cases explicitly.** Guard known cases (`=IF(B4=0,0,C4/B4-1)`
  for growth on a zero base) instead of wrapping everything in `IFERROR`,
  which also hides real mistakes.
- **One sign convention.** Decide whether costs are negative or positive, keep
  it everywhere, and say it in labels (`Less: net debt`).
- **Checks.** Add check rows where totals must agree (assets minus liabilities
  and equity, sum of segments minus total) and make a non-zero result visible.
- **Document typed inputs.** Give every hard-coded figure its source and date,
  in a `Source` column next to it or in a cell comment:

  ```python
  from openpyxl.comments import Comment
  cell.comment = Comment("Source: FY2024 annual report p.45, retrieved 2025-03-01", "Analyst")
  ```

- **Scenarios and sensitivities.** A scenario selector is an input cell
  (Base/Bull/Bear, with data validation) that `CHOOSE` or `INDEX` uses to pick
  each assumption. Build sensitivity grids from formulas that read their row
  and column headers; openpyxl cannot create Excel What-If data tables.

## 6. Worked example

A five-year revenue and EBITDA projection with an EV/EBITDA valuation. It uses
one font, the colour code, the number formats above, text year labels, inputs
with sources, and formulas only for anything that should stay live.

```python
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"
BLUE, BLACK, GREEN = "0000FF", "000000", "008000"
KEY = PatternFill("solid", fgColor="FFF2CC")
NUM = '#,##0.0_);(#,##0.0);"-"_)'
PCT = '0.0%_);(0.0%);"-"_)'
MULT = '0.0"x"_)'
YEARS = ["FY2024A", "FY2025E", "FY2026E", "FY2027E", "FY2028E", "FY2029E"]
COLS = [get_column_letter(index) for index in range(2, 2 + len(YEARS))]  # B..G


def put(ws, ref, value, color=BLACK, fmt=None, bold=False, fill=None):
    """Write one cell with the workbook font, so every cell shares one family."""
    cell = ws[ref]
    cell.value = value
    cell.font = Font(name=FONT, size=10, color=color, bold=bold)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    return cell


wb = Workbook()
inputs = wb.active
inputs.title = "Inputs"
model = wb.create_sheet("Model")

# Inputs: one assumption per cell, typed in blue, each with its source and date.
put(inputs, "A1", "Assumption", bold=True)
put(inputs, "B1", "Value", bold=True)
put(inputs, "C1", "Source (retrieved)", bold=True)
assumptions = [
    ("Revenue FY2024A (US$ mm)", 1250, NUM, "FY2024 annual report p.45 (2025-03-01)", False),
    ("Revenue growth p.a.", 0.08, PCT, "Management guidance, Q4 call (2025-02-12)", True),
    ("EBITDA margin", 0.22, PCT, "FY2022A-FY2024A average (2025-03-01)", True),
    ("EV / EBITDA multiple", 8.5, MULT, "Peer median (2025-03-01)", True),
    ("Net debt FY2024A (US$ mm)", 320, NUM, "FY2024 balance sheet (2025-03-01)", False),
]
for row, (label, value, fmt, source, key) in enumerate(assumptions, start=2):
    put(inputs, f"A{row}", label)
    put(inputs, f"B{row}", value, color=BLUE, fmt=fmt, fill=KEY if key else None)
    put(inputs, f"C{row}", source)
REVENUE0, GROWTH, MARGIN, MULTIPLE, NET_DEBT = (f"Inputs!$B${row}" for row in range(2, 7))

# Model: periods across columns, years as text, the unit in the title.
put(model, "A1", "Revenue and EBITDA (US$ mm)", bold=True)
put(model, "A2", "Fiscal year", bold=True)
for col, year in zip(COLS, YEARS):
    header = put(model, f"{col}2", year, bold=True)
    header.alignment = Alignment(horizontal="right")
    header.border = Border(bottom=Side(style="thin"))
for row, label in ((3, "Revenue growth"), (4, "Revenue"), (5, "EBITDA margin"), (6, "EBITDA")):
    put(model, f"A{row}", label)
put(model, "B4", f"={REVENUE0}", color=GREEN, fmt=NUM)  # link to another sheet: green
for index, col in enumerate(COLS):
    if index:  # projection years: the same relative formula in every column
        previous = COLS[index - 1]
        put(model, f"{col}3", f"={GROWTH}", color=GREEN, fmt=PCT)
        put(model, f"{col}4", f"={previous}4*(1+{col}3)", fmt=NUM)  # same-sheet calc: black
    put(model, f"{col}5", f"={MARGIN}", color=GREEN, fmt=PCT)
    put(model, f"{col}6", f"={col}4*{col}5", fmt=NUM)

put(model, "A8", "Valuation on FY2025E EBITDA", bold=True)
put(model, "A9", "EV / EBITDA")
put(model, "C9", f"={MULTIPLE}", color=GREEN, fmt=MULT)
put(model, "A10", "Enterprise value")
put(model, "C10", "=C6*C9", fmt=NUM)
put(model, "A11", "Less: net debt")
put(model, "C11", f"=-{NET_DEBT}", color=GREEN, fmt=NUM)
put(model, "A12", "Equity value", bold=True)
put(model, "C12", "=C10+C11", fmt=NUM, bold=True).border = Border(top=Side(style="thin"))

inputs.column_dimensions["A"].width = 30
inputs.column_dimensions["B"].width = 12
inputs.column_dimensions["C"].width = 44
model.column_dimensions["A"].width = 30
for col in COLS:
    model.column_dimensions[col].width = 11
model.freeze_panes = "B3"
wb.save("projection.xlsx")
```

Then recalculate, audit and look at the result:

```bash
valuz-python $SKILL/scripts/recalc_check.py projection.xlsx          # writes projection.recalc.xlsx
valuz-python $SKILL/scripts/model_audit.py projection.recalc.xlsx --strict
dsoffice render --input projection.recalc.xlsx --output-dir preview-model --sheet Model --range A1:G12
```

`recalc_check.py` should report `"status": "clean"` with 27 formulas, and
`model_audit.py` should report `"finding_count": 0`. Spot-check by hand:
revenue FY2025E = 1,250 × 1.08 = 1,350.0; FY2029E = 1,250 × 1.08^5 ≈ 1,836.7;
EBITDA FY2025E = 1,350 × 22% = 297.0; enterprise value = 297 × 8.5 = 2,524.5;
equity value = 2,524.5 − 320 = 2,204.5. Net debt shows as `(320.0)` and the
blank FY2024A growth cell stays empty rather than showing a made-up figure.
