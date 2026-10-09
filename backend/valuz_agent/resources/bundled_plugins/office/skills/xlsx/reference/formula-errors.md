# Recalculating and fixing formula errors

`$SKILL` below is this skill's directory.

## Running the check

```bash
valuz-python $SKILL/scripts/recalc_check.py model.xlsx
```

1. `dsoffice recalculate` writes a recalculated copy to a new file
   (`model.recalc.xlsx`, then `model.recalc-2.xlsx`, … if that exists; or
   `--output new.xlsx`). `--replace` lets a re-run overwrite a previous
   `--output`. The input file is never modified.
2. Every cell of every sheet, hidden sheets included, is scanned for an error
   value. The JSON report goes to stdout and, with `--report path.json`, to a
   file.

| Exit | Meaning |
|---|---|
| 0 | clean: no error values, every formula has a cached result |
| 1 | error values remain, or formulas have no cached result (`--allow-errors` turns this into 0) |
| 2 | bad arguments, e.g. `--output` already exists and `--replace` was not given |
| 3 | `dsoffice` is missing or failed, or the workbook could not be read |

Use `--allow-errors` only when an error value is intended and the user knows
(for example an `NA()` placeholder that a chart skips). Use `--no-recalc` to
scan the cached values exactly as saved, e.g. a file the user sent; it reports
`"status": "unverified"` when formulas have no cached result. A `--no-recalc`
scan of a file written by openpyxl or XlsxWriter proves nothing: openpyxl
stores no results and XlsxWriter stores `0` for every formula.

## Reading the report

```json
{
  "status": "errors",
  "output": "/work/model.recalc.xlsx",
  "total_formulas": 412,
  "total_errors": 37,
  "errors_by_type": {
    "#DIV/0!": {"count": 36, "locations": ["Model!D14", "Model!E14", "..."],
                "truncated": false, "hint": "A divisor is zero or blank. ..."},
    "#NAME?": {"count": 1, "locations": ["Summary!B3"], "truncated": false, "hint": "..."}
  },
  "root_causes": [
    {"cell": "Model!D14", "error": "#DIV/0!", "formula": "=D12/C12-1", "reason": "origin"},
    {"cell": "Summary!B3", "error": "#NAME?", "formula": "=XLOOKUP(B2,A:A,C:C)", "reason": "origin"}
  ],
  "circular_references": [],
  "sheets": [{"name": "Model", "formulas": 380, "errors": 36}, {"name": "Summary", "formulas": 32, "errors": 1}],
  "cached_values_missing": {"count": 0, "locations": [], "truncated": false},
  "error_like_text": {"count": 0, "locations": [], "truncated": false},
  "external_reference_cells": {"count": 0, "locations": [], "truncated": false},
  "broken_defined_names": [],
  "fidelity": {"dropped": [], "before": {"sheets": 2, "charts": 1}, "after": {"sheets": 2, "charts": 1}},
  "notes": ["Fix root_causes first, then run this check again on the edited source workbook."]
}
```

- `errors_by_type`: count and locations (`Sheet!A1`, quoted when the sheet
  name needs it) per error value; at most `--max-locations` (default 50) are
  listed, `truncated` says whether there are more.
- `root_causes`: error cells whose formula does not read another error cell.
  Errors spread: one `#DIV/0!` feeds every total built on it. Fix these cells
  and most of the others clear. `reason` is `origin`, `external-link` (the
  formula reads another workbook) or `circular` (the cell is in a loop).
  The analysis follows plain references and simple defined names; references
  built at run time (`INDIRECT`, `OFFSET`) are not followed. It is `null` when
  there are more than 20,000 errors.
- `circular_references`: cells that depend on themselves through other error
  cells. LibreOffice reports an unintended loop as `#VALUE!` in every cell of it.
- `error_like_text`: text cells whose whole content is an error code, usually
  values pasted from a broken sheet. They are not counted as errors but look
  like errors to the reader.
- `external_reference_cells`: formulas that read other workbook files. Those
  files are not available during recalculation, so these cells may show
  `#N/A`, `#REF!` or old values regardless of whether the formula is right.
- `broken_defined_names`: names whose definition contains `#REF!`.
- `fidelity`: workbook features counted before and after the LibreOffice round
  trip (charts, images, comments, tables, pivot tables, data validations,
  conditional-format rules, merged ranges, hyperlinks, defined names).
  `dropped` lists anything the recalculated copy lost.
- `missing_fonts`: fonts the engine did not have; values are unaffected.

## Fix loop

1. Read `root_causes`. Open the source workbook with openpyxl
   (`data_only=False`) and look at each cell's formula and the cells it reads.
2. Fix the cause in the source workbook or in the script that builds it, not
   in the recalculated copy.
3. Run `recalc_check.py` again; it writes a fresh `.recalc` copy each time.
4. Repeat until `"status": "clean"`. Then spot-check values (see the checklist
   in SKILL.md).

## What each error usually means in a generated workbook

| Error | Typical cause | Fix |
|---|---|---|
| `#DIV/0!` | growth or margin on a zero or empty base; a divisor row that is blank in the first period | correct the input or row reference; guard the known case: `=IF(C12=0,0,D12/C12-1)` |
| `#REF!` | reference to a deleted row, column or sheet; `INDEX` position beyond its range; `INDIRECT` to a sheet that does not exist | re-point the reference to the intended cell; check range sizes |
| `#NAME?` | misspelt function or sheet; sheet name with spaces not quoted (`'Op Model'!A1`); undefined name; text without quotes; Excel 2010+ function without its `_xlfn.` prefix | fix the spelling or quoting; define the name; add the prefix (below) |
| `#VALUE!` | text in arithmetic (a header row or a number stored as text such as `"1,250"`); a whole range where one value is expected; unintended circular reference | write real numbers (not strings); point at a single cell; break the loop |
| `#N/A` | lookup key not found: trailing spaces, number vs text keys, wrong lookup range, unsorted data with approximate match | clean and align key types; use exact match (`MATCH(...,0)`, `FALSE` in `VLOOKUP`); use `IFNA` only when a miss is legitimate |
| `#NUM!` | `IRR`/`RATE` did not converge; a result too large | check the cash-flow signs, give a guess argument, check the inputs |
| `#NULL!` | a space instead of a comma or colon between two ranges | write `SUM(A1:A5,C1:C5)` or `SUM(A1:C5)` |

Mistakes that produce wrong numbers without an error value, so the checklist
in SKILL.md still applies: ranges that stop one row short after rows were
added, percentages stored as `8` instead of `0.08`, relative references that
should have been absolute when a formula was copied across.

## Where LibreOffice differs from Excel

The engine behind `dsoffice` is LibreOffice. The error type can differ from
what Excel would show; the cause and the fix are the same.

- `SQRT(-1)`, `LN(0)`, `FACT(200)`, `DATE(-1,1,1)`: `#VALUE!` (Excel: `#NUM!`).
- `OFFSET` beyond the sheet edge: `#VALUE!` (Excel: `#REF!`).
- `IRR` that cannot converge: may be `#N/A` (Excel: `#NUM!`).
- A reference to a sheet that does not exist: `#NAME?`.
- Unintended circular reference: `#VALUE!` in every cell of the loop (Excel
  shows a warning and 0). With `wb.calculation.iterate = True` the loop is
  iterated instead.

## Excel 2010+ functions need a prefix in the file

Excel stores functions added after Excel 2007 with a prefix. openpyxl writes
formula text exactly as given, so `=XLOOKUP(...)` lands in the file without it
and shows `#NAME?` in Excel as well as in the recalculation. Either prefer
long-standing functions, or write the stored form:

| Write in the formula | Instead of |
|---|---|
| `_xlfn.XLOOKUP`, `_xlfn.XMATCH` | `XLOOKUP`, `XMATCH` |
| `_xlfn.IFNA`, `_xlfn.IFS`, `_xlfn.SWITCH` | `IFNA`, `IFS`, `SWITCH` |
| `_xlfn.MAXIFS`, `_xlfn.MINIFS` | `MAXIFS`, `MINIFS` |
| `_xlfn.CONCAT`, `_xlfn.TEXTJOIN` | `CONCAT`, `TEXTJOIN` |
| `_xlfn.STDEV.S`, `_xlfn.STDEV.P`, `_xlfn.PERCENTILE.INC`, `_xlfn.RANK.EQ`, `_xlfn.NORM.DIST` | the same names without prefix |
| `_xlfn.CEILING.MATH`, `_xlfn.FLOOR.MATH`, `_xlfn.DAYS`, `_xlfn.ISOWEEKNUM`, `_xlfn.AGGREGATE`, `_xlfn.RRI`, `_xlfn.FORECAST.LINEAR` | the same names without prefix |
| `_xlfn.LET(_xlpm.x, 2, _xlpm.x*3)` | `LET(x, 2, x*3)` |

Do not prefix older functions: `IFERROR`, `SUMIFS`, `COUNTIFS`, `AVERAGEIFS`,
`EOMONTH`, `EDATE`, `YEARFRAC`, `NETWORKDAYS`, `XNPV`, `XIRR`, `NPV`, `IRR`,
`PMT`, `INDEX`, `MATCH`, `VLOOKUP`, `SUMPRODUCT` are stored as written
(`_xlfn.XIRR` fails). XlsxWriter adds the prefixes itself when the workbook is
created with `xlsxwriter.Workbook(path, {"use_future_functions": True})`.

Dynamic-array functions (`FILTER`, `SORT`, `UNIQUE`, `SEQUENCE`) do not spill
when written through openpyxl: the cell shows only the first value. Avoid
them in generated workbooks, or use XlsxWriter's
`write_dynamic_array_formula()` and check the result.

## Links to other workbooks

A formula such as `='[Budget.xlsx]Sheet1'!A1` keeps the last value Excel saved,
but the linked file is not available to `dsoffice`, so the recalculated copy
shows `#N/A` or `#REF!` there. If the user wants the workbook self-contained,
copy the linked values into an input sheet (blue, with the source file and
date) and point the formulas at it. Otherwise leave the links, deliver the
source workbook rather than the recalculated copy, and tell the user which
cells depend on which file.
