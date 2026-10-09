---
name: xlsx
description: Read, create, edit, recalculate and check Excel workbooks (.xlsx, .xlsm, .xls, .csv) with openpyxl, pandas and XlsxWriter, including formulas, formatting, charts and financial models (projections, valuation, budgets). Use whenever a spreadsheet is an input or the deliverable. Covers live formulas instead of pasted results, financial-model conventions (colour coding, number formats, formula rules), recalculation through dsoffice with a scan of every cell for #REF!, #DIV/0!, #NAME? and other errors (scripts/recalc_check.py), a model audit (scripts/model_audit.py), structural checks, and visual checks of formatted ranges when formatting or layout matters.
---

# Excel workbooks

`$SKILL` in the commands below means this skill's directory (the folder that
holds this SKILL.md); use its absolute path. The skill directory is read-only:
keep scripts, working files and outputs in the task workspace, and save to a
new workbook unless the user asks for an in-place edit.

## Environment

- Run every script and snippet with `valuz-python`, the bundled Python 3.12
  with openpyxl, pandas, XlsxWriter, Pillow and lxml: `valuz-python script.py`
  or `valuz-python - <<'PY' … PY`. It is read-only. If a task truly needs
  another package, create a task venv that keeps the bundled libraries:
  `valuz-python -m venv --system-site-packages .venv && .venv/bin/pip install <pkg>`.
  If `valuz-python` is not on PATH, install what it provides and use that
  interpreter wherever this file says `valuz-python`: Python 3.12 with
  openpyxl, pandas, XlsxWriter, Pillow and lxml
  (plan the installation yourself, e.g. a venv).
  An explicit user or AGENTS.md environment requirement takes precedence.
- `dsoffice` is the bundled LibreOffice engine: `recalculate`, `convert` and
  `render`. Its outputs must be new files or directories; it never overwrites.
  Do not search for or call a system LibreOffice, `soffice` or another
  converter unless the user explicitly asks. If `dsoffice` fails, report
  that instead of substituting another tool.
  If `dsoffice` is not on PATH, install it: it is the `dsoffice` command of
  the npm package `@deepseek-ai/libreoffice-kit@0.1.5` (Node.js 22.19 or
  newer; plan the installation yourself).

## Workflow

1. Inspect the input workbook or template (sheets, formulas, styles, conventions).
2. Build or edit with the right library, keeping calculations as formulas.
3. Meet the output requirements; for models, follow `reference/financial-models.md`.
4. Recalculate and scan with `scripts/recalc_check.py`; fix until clean.
5. Work through the verification checklist and run the structural checker.
6. Render only for formatting or layout requests, then deliver.

## Choose the library

- **pandas** for data analysis and transformation, and for reading CSV or
  large tables. A DataFrame is not the workbook: exporting it over an
  existing file can lose sheets, formulas, charts and formatting. Write
  analysis results back to the intended ranges with openpyxl.
- **openpyxl** for existing `.xlsx`/`.xlsm` workbooks and for targeted cell,
  formula or style changes; also fine for new workbooks.
- **XlsxWriter** for new, heavily formatted workbooks (many formats, charts,
  conditional formats, large outputs). It cannot read or modify existing files.

## Read and inspect

Load with `data_only=False` (the default) when formulas must survive. Inspect
sheet names, the affected cell types, formulas, styles, merged ranges and
referenced ranges before editing:

```bash
valuz-python - <<'PY'
from openpyxl import load_workbook
wb = load_workbook("input.xlsx")                     # formulas come back as "=..." text
print("defined names:", list(wb.defined_names.keys()))
for ws in wb.worksheets:
    print(f"== {ws.title} {ws.dimensions} {ws.sheet_state} merged={list(ws.merged_cells.ranges)[:5]}")
    for row in ws.iter_rows(max_row=min(ws.max_row, 15), max_col=min(ws.max_column, 12)):
        print([cell.value for cell in row])
PY
```

`load_workbook(path, data_only=True)` returns the values Excel last saved;
they can be missing or stale. `pandas.read_excel(path, sheet_name=None)` reads
every sheet into DataFrames for analysis.

## Create

For a new workbook, write values, styles and formulas directly:

```python
from openpyxl import Workbook
from openpyxl.styles import Font

FONT = "Arial"
wb = Workbook()
ws = wb.active
ws.title = "Revenue"
for row in [("Quarter", "Revenue (US$ k)"), ("Q1", 12), ("Q2", 18), ("Total", "=SUM(B2:B3)")]:
    ws.append(row)
for row in ws.iter_rows():
    for cell in row:
        cell.font = Font(name=FONT, size=10, bold=cell.row in (1, 4))
for cell in ws["B"][1:]:
    cell.number_format = "#,##0"
ws.column_dimensions["A"].width = 18
ws.column_dimensions["B"].width = 18
wb.save("report-draft.xlsx")
```

With XlsxWriter, set the font once for the whole workbook, including empty
cells: `xlsxwriter.Workbook(path, {"default_format_properties": {"font_name": "Arial", "font_size": 10}})`.

Preserve numbers, dates, booleans and identifiers as their intended types;
formatting is not a type conversion. Write `1250`, not the string `"1,250"`,
and real `datetime`/`date` values with a date format.

## Edit existing workbooks

- `wb = load_workbook("input.xlsx")`, change only the requested ranges, save
  to a new file, and reopen it to check the change and the content that had
  to stay unchanged.
- openpyxl's `insert_rows`, `delete_rows`, `insert_cols` and `delete_cols` do
  not update formulas, defined names, merged cells, charts or conditional
  formats that point past the change. Prefer writing into existing space;
  when you add rows, update every formula and range that should include them
  (`=SUM(B2:B9)` becomes `=SUM(B2:B10)`), then check ranges that grew.
- Set `wb.calculation.fullCalcOnLoad = True` before saving an edited file you
  may deliver without recalculation, so Excel recalculates when it opens it.
- openpyxl does not keep shapes, form controls, slicers or some advanced
  features of files it re-saves. When the input has such features, compare
  `zipfile.ZipFile(path).namelist()` of input and output and tell the user
  what changed.
- Do not rename `.xls`, `.xlsb`, encrypted or macro-enabled files to `.xlsx`.
  Convert `.xls` with `dsoffice convert --input in.xls --output in.xlsx` and say
  so. For `.xlsm`, `load_workbook(path, keep_vba=True)` and save as `.xlsm`
  keeps the VBA project but does not run or edit macros. `dsoffice` cannot
  recalculate `.xlsm`: to check its formulas, save a scratch `.xlsx` copy
  without `keep_vba` and run `recalc_check.py` on that copy.
- CSV: read with `pandas.read_csv`; export one sheet with
  `dsoffice convert --input book.xlsx --output sheet.csv --sheet NAME`.

## Output requirements (every workbook)

1. **One consistent professional font.** English: Arial or Calibri, 10–11 pt.
   Chinese or mixed text: one family with CJK glyphs, such as
   `Microsoft YaHei` (微软雅黑) or `DengXian` (等线), for every cell including
   numbers. Titles may be larger or bold; the family stays the same. In
   openpyxl set `Font(name=...)` on every cell you write.
2. **Zero formula errors** in the delivered file: `recalc_check.py` reports
   `"status": "clean"`, and no text cell holds a pasted `#N/A` or `#REF!`.
3. **An existing workbook or template sets the style.** Follow its fonts,
   colours, number formats, units, column widths, header styles and sheet
   order; give new cells the style of their neighbours; do not restyle or
   reorganise what you were not asked to change.
4. **Readable layout.** Bold headers, column widths that show full values (no
   `###`), number formats with thousands separators and consistent decimals,
   units in headers, frozen header rows on long tables.

## Formulas, not pasted results

Anything the workbook should keep live must be an Excel formula, so the user
can change an input and see every result update, and can audit how a number
was derived. Do not compute in Python and paste the answer.

```python
# Wrong: the total is calculated in Python and frozen as a number
ws["B10"] = sum(ws[f"B{row}"].value for row in range(2, 10))
# Right: Excel calculates it and updates it when B2:B9 change
ws["B10"] = "=SUM(B2:B9)"

# Wrong: the growth rate disappears into a constant
ws["C4"] = revenue_2024 * 1.08
# Right: the rate lives in an input cell that the formula references
ws["B3"] = 0.08
ws["C4"] = "=B4*(1+$B$3)"
```

Use pandas to analyse data, then write the workbook's own calculations
(totals, ratios, growth, variances, lookups) as formulas next to the data.
Plain values are right for raw data, snapshots of external data (label the
source and date) and when the user asks for values only.

Writing a formula does not calculate it: openpyxl stores no result and
XlsxWriter stores `0`, so viewers, pandas and `data_only=True` see nothing or
zeros until the workbook is recalculated. Functions added after Excel 2007
(`XLOOKUP`, `IFNA`, `IFS`, `TEXTJOIN`, `STDEV.S`, …) must be written with their
`_xlfn.` prefix through openpyxl or they show `#NAME?`; prefer long-standing
functions (`INDEX`/`MATCH`, `SUMIFS`, `IFERROR`). The list is in
`reference/formula-errors.md`.

## Financial models

When building or editing models, read `reference/financial-models.md`: layout,
colour coding, number formats, formula rules and a worked example. In short:

- Keep the conventions an existing model already uses; apply these to new ones.
- Font colours: typed inputs and assumptions blue (`0000FF`), formulas black,
  formulas reading other sheets green (`008000`), links to other files red
  (`FF0000`); optional light-yellow fill (`FFF2CC`) on key assumptions.
- Formats: units in headers (`Revenue (US$ mm)`), negatives in parentheses,
  zero as `-` (`#,##0.0_);(#,##0.0);"-"_)`), percentages with consistent
  decimals, multiples as `8.5x`, years as text labels (`FY2025E`).
- Every assumption in its own cell and referenced, never typed into a formula;
  one formula shape across each row; no circular references unless intended;
  the source and date of each hard-coded input in a note or Source column.

Check a model mechanically:

```bash
valuz-python $SKILL/scripts/model_audit.py model.xlsx            # add --strict to exit 1 on findings
```

It reports the fonts in use, cells whose font colour does not match the
colour code (and, through `colour_coding.observed`, the scheme an existing
file already follows), numbers typed into formulas, formulas that break their
row's pattern and typed numbers sitting between formulas. Findings are prompts
to review, not proof of a mistake. `--axis column` checks layouts with periods
down the rows; `--sheets` limits the audit.

## Recalculate and verify

Run this whenever the workbook contains formulas, before reading results back
or delivering:

```bash
valuz-python $SKILL/scripts/recalc_check.py report-draft.xlsx --output report.xlsx
```

It runs `dsoffice recalculate` into the new file (default
`<name>.recalc.xlsx`), then scans every cell of every sheet for `#REF!`,
`#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`, `#NUM!`, `#NULL!` and newer error
values. The JSON report gives `total_formulas`, `total_errors`, locations per
error type with a hint, and `root_causes`: the error cells whose inputs are
not errors, which are the ones to fix first. Exit codes: `0` clean, `1` errors
remain, `2` bad arguments (such as an existing `--output`: add `--replace` when
re-running after a fix), `3` `dsoffice` or the workbook failed.
`--allow-errors` exits 0 for intended errors.

Fix causes in the source workbook (or the script that builds it), never in
the recalculated copy, and run the check again until it is clean.
`reference/formula-errors.md` explains every report field, common causes and
fixes, and where LibreOffice's error types differ from Excel's.

Recalculation does not validate business logic. Without a working `dsoffice`,
say so; do not replace requested formulas with constants or report old cached
values as newly calculated results.

## Verification checklist

After the workbook is clean, reopen the recalculated copy with
`data_only=True` (values) and `data_only=False` (formulas) and check:

- **Spot-check by hand** two or three results (a total, a ratio or growth
  rate, the final output) against the source numbers.
- **Edge cases**: inputs that are zero, negative or empty. In a scratch copy,
  set an input to 0 or blank and run `recalc_check.py` on it to see whether
  divisions and growth rates hold up.
- **Cross-sheet references** point at the right sheet, row and period.
- **Ranges that grew**: totals, lookups, chart series and named ranges include
  every row you added.
- **Off-by-one**: first and last row and column of each range; header rows not
  summed; formulas copied across start in the right period.
- **Types**: numbers are numbers, dates are dates, percentages are fractions.
- **Output requirements**: one font, number formats, units in headers.

## Check structure

```bash
valuz-python $SKILL/scripts/check_office.py report.xlsx --out checks.json
```

It checks ZIP/XML integrity and internal relationships and reports sheet
names, cell counts and formula counts. `--contains TEXT` (repeatable) checks
string cells and sheet names; `--count N` checks the sheet count. It does not
calculate formulas, validate numbers or judge appearance.

## Visual checks (formatting and layout only)

For data and formula tasks, deliver after the structural and data checks
pass; skip rendering, and do not add an unrequested caveat that it was
skipped. Basic styling such as bold headers or number formats does not by
itself require a visual check.

Inspect relevant regions when the task concerns formatting, layout, chart
appearance, print layout or a known display problem. First establish that the
current model accepts images; if it does not, finish the structural and data
checks and report the visual check as unavailable. Render the recalculated
copy, because the renderer shows stored results and a file written by
XlsxWriter stores `0` for every formula:

```bash
dsoffice render --input report.xlsx --output-dir preview-v1 --sheet Summary --range A1:H30 --dpi 144
```

Open the PNGs listed in `preview-v1/manifest.json` with your image reading tool.

- Use `--sheet` (exact name) and `--range`, not `--pages` (PDF pages follow
  print settings). Include chart regions explicitly; large regions may split
  into several images. The manifest lists image paths, rectangles and
  `missingFonts`.
- Check widths, number formats, clipping, colours and charts. Render each
  region once; render again only after changing the workbook. Red wavy
  underlines in a preview are LibreOffice spell-check marks, not formatting.
- A fully transparent or uniformly blank image for a populated range is a
  renderer failure, not a workbook problem: stop visual checking for that
  workbook, keep the workbook changes, and report the visual check as
  unavailable. Do not build diagnostic workbooks, retry with other ranges,
  DPI or formats, or change print settings to investigate.
- Change print areas or scaling only when the user asks for printed layout.
  Export a PDF only when requested:
  `dsoffice convert --input report.xlsx --output report.pdf`.

## Deliver

Deliver the recalculated copy: it keeps the formulas and adds fresh results,
so viewers and previews show numbers. Name it as the deliverable with
`--output` (build into `report-draft.xlsx`, recalculate to `report.xlsx`).
Deliver the edited source instead, with `wb.calculation.fullCalcOnLoad = True`,
when `fidelity.dropped` in the report lists features the round trip lost, or
when the workbook links to other files.

Call `deliver_artifacts({"attachments": [{"filePath": "/abs/path/report.xlsx"}]})`
with the absolute path of the final workbook inside the working directory. If
that tool is not available, give the final path. Do not deliver drafts, JSON
reports or preview images unless the user asks for them.
