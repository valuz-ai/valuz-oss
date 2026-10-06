#!/usr/bin/env python3
"""Recalculate a workbook with dsoffice, then scan every cell of every sheet for Excel errors.

    valuz-python recalc_check.py model.xlsx                    # writes and scans model.recalc.xlsx
    valuz-python recalc_check.py model.xlsx --output out.xlsx  # choose the (new) output path
    valuz-python recalc_check.py model.xlsx --no-recalc        # scan cached values as saved

Recalculation runs ``dsoffice recalculate`` into a NEW file; the input is never modified.
The scan reads the sheet XML directly, so it sees exactly what Excel or a viewer would
display: every cell whose cached value is an error (#REF!, #DIV/0!, #VALUE!, #NAME?, #N/A,
#NUM!, #NULL!, and newer ones such as #SPILL!) is reported with its location and formula.
Error cells whose formula only references cells that are not errors are listed as
``root_causes``: fix those first, the cells downstream usually clear with them.

A JSON report goes to stdout (and to --report). Exit codes:
  0  clean: no error values and every formula has a cached result
  1  error values found, or formulas without cached results (--allow-errors gives 0)
  2  invalid command-line arguments (e.g. an existing --output without --replace)
  3  recalculation failed or the workbook could not be read
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import Token
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    get_column_letter,
    range_boundaries,
)

ERROR_HINTS = {
    "#DIV/0!": "A divisor is zero or blank. Fix the input, or guard the formula, "
    "e.g. =IF(B5=0,0,A5/B5).",
    "#REF!": "A reference points at a deleted row/column/sheet or outside its range "
    "(INDEX, OFFSET, INDIRECT). Re-point it.",
    "#NAME?": "Unknown function, sheet or defined name: a typo, text without quotes, a missing "
    "sheet, or an Excel 2010+ function written without its _xlfn. prefix.",
    "#VALUE!": "Wrong argument type (text in arithmetic, a range where one value is expected). "
    "LibreOffice also uses #VALUE! for invalid numeric arguments (SQRT(-1), LN(0)) and for "
    "unintended circular references.",
    "#N/A": "A lookup found no match, or NA() was used. Check keys, ranges and data types; "
    "use IFERROR/IFNA only when a miss is expected.",
    "#NUM!": "Numeric result out of range, or IRR/RATE did not converge. Check the inputs "
    "or give a guess.",
    "#NULL!": "A space between two ranges that do not intersect; usually a missing comma or colon.",
}
ERROR_LITERALS = set(ERROR_HINTS) | {
    "#SPILL!",
    "#CALC!",
    "#FIELD!",
    "#BLOCKED!",
    "#UNKNOWN!",
    "#CONNECT!",
    "#BUSY!",
    "#GETTING_DATA",
    "#PYTHON!",
}
RECALC_INPUTS = {".xlsx", ".xls", ".ods"}
SCAN_INPUTS = {".xlsx", ".xlsm"}
ROOT_ANALYSIS_LIMIT = 20000
CELL_RANGE = re.compile(
    r"^\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?$"  # A1, $A$1:$B$9
    r"|^\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}$"  # A:C
    r"|^\$?\d+:\$?\d+$"  # 1:3
)
NAME_TARGET = re.compile(
    r"^(?:'((?:[^']|'')+)'|([^'!]+))!(\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)$"
)
EXTERNAL_REF = re.compile(r"\[[^\]]+\][^!]*!")
FEATURE_CONTENT_TYPES = {
    "charts": "drawingml.chart+xml",
    "comments": "spreadsheetml.comments+xml",
    "threaded_comments": "threadedcomments+xml",
    "tables": "spreadsheetml.table+xml",
    "pivot_tables": "spreadsheetml.pivottable+xml",
    "slicers": "slicer+xml",
    "external_links": "spreadsheetml.externallink+xml",
}
SHEET_ELEMENTS = {
    "data_validations": re.compile(rb"<(?:\w+:)?dataValidation[\s/>]"),
    "conditional_format_rules": re.compile(rb"<(?:\w+:)?cfRule[\s/>]"),
    "merged_ranges": re.compile(rb"<(?:\w+:)?mergeCell[\s/>]"),
    "hyperlinks": re.compile(rb"<(?:\w+:)?hyperlink[\s/>]"),
}
DEFINED_NAME = re.compile(rb"<(?:\w+:)?definedName[\s>]")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def quote_sheet(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def location(sheet: str, ref: str) -> str:
    return f"{quote_sheet(sheet)}!{ref}"


# ---------------------------------------------------------------------------
# Package reading


def part_relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Map relationship id -> (type, resolved package path) for internal targets of ``part``."""
    rels_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    try:
        root = ET.fromstring(archive.read(rels_path))
    except KeyError:
        return {}
    result = {}
    for rel in root:
        if rel.get("TargetMode") == "External":
            continue
        target = unquote(rel.get("Target", ""))
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        result[rel.get("Id", "")] = (rel.get("Type", ""), resolved)
    return result


def read_workbook(archive: zipfile.ZipFile) -> dict:
    """Return sheet list, defined names and the shared-strings part of the workbook."""
    root_rels = part_relationships(archive, "")
    workbook_part = next(
        (target for kind, target in root_rels.values() if kind.endswith("/officeDocument")),
        "xl/workbook.xml",
    )
    root = ET.fromstring(archive.read(workbook_part))
    rels = part_relationships(archive, workbook_part)
    sheets = []
    for node in root.iter():
        if local(node.tag) != "sheet":
            continue
        rid = next((value for key, value in node.attrib.items() if local(key) == "id"), "")
        kind, target = rels.get(rid, ("", ""))
        if not kind.endswith("/worksheet"):
            continue  # chart sheets and dialog sheets have no cells
        sheets.append(
            {"name": node.get("name", ""), "state": node.get("state", "visible"), "part": target}
        )
    names = []
    for node in root.iter():
        if local(node.tag) == "definedName":
            names.append({"name": node.get("name", ""), "definition": (node.text or "").strip()})
    shared = next(
        (target for kind, target in rels.values() if kind.endswith("/sharedStrings")), None
    )
    return {"part": workbook_part, "sheets": sheets, "names": names, "shared_strings": shared}


def error_like_shared_strings(archive: zipfile.ZipFile, part: str | None) -> set[int]:
    """Indices of shared strings whose whole text is an error literal (pasted error text)."""
    found: set[int] = set()
    if part is None or part not in archive.namelist():
        return found
    index = 0
    with archive.open(part) as handle:
        for _event, node in ET.iterparse(handle, events=("end",)):
            if local(node.tag) != "si":
                continue
            text = "".join(
                child.text or ""
                for child in node.iter()
                if local(child.tag) == "t" and child is not node
            )
            if text.strip() in ERROR_LITERALS:
                found.add(index)
            index += 1
            node.clear()
    return found


def formula_text(node: ET.Element, ref: str, shared: dict[str, tuple[str, str]]) -> str:
    text = node.text or ""
    kind = node.get("t")
    if kind == "shared":
        group = node.get("si", "")
        if text:
            shared[group] = ("=" + text, ref)
            return "=" + text
        master = shared.get(group)
        if master is None:
            return "(shared formula)"
        try:
            return Translator(master[0], origin=master[1]).translate_formula(ref)
        except Exception:  # an untranslatable master still identifies the formula
            return master[0]
    if kind == "dataTable":
        return "(data table)"
    return "=" + text if text else "(formula)"


def scan_sheet(archive: zipfile.ZipFile, sheet: dict, error_strings: set[int]) -> dict:
    """Stream one worksheet part and collect formula, error and cache information."""
    result = {
        "formulas": 0,
        "errors": [],
        "uncached": [],
        "error_text": [],
        "external": [],
    }
    shared: dict[str, tuple[str, str]] = {}
    row_index = 0
    column_index = 0
    with archive.open(sheet["part"]) as handle:
        for event, node in ET.iterparse(handle, events=("start", "end")):
            name = local(node.tag)
            if event == "start":
                if name == "row":
                    row_index = int(node.get("r") or row_index + 1)
                    column_index = 0
                continue
            if name == "row":
                node.clear()
                continue
            if name != "c":
                continue
            ref = node.get("r")
            if ref:
                letters, row_index = coordinate_from_string(ref.upper())
                column_index = column_index_from_string(letters)
                ref = ref.upper()
            else:
                column_index += 1
                ref = f"{get_column_letter(column_index)}{row_index}"
            kind = node.get("t", "n")
            formula_node = value_node = inline_node = None
            for child in node:
                child_name = local(child.tag)
                if child_name == "f":
                    formula_node = child
                elif child_name == "v":
                    value_node = child
                elif child_name == "is":
                    inline_node = child
            formula = None
            if formula_node is not None:
                result["formulas"] += 1
                formula = formula_text(formula_node, ref, shared)
                # openpyxl writes <v></v> with no result; an empty text result is t="str".
                has_text = value_node is not None and bool(value_node.text)
                empty_string = value_node is not None and kind == "str"
                if not (has_text or empty_string or inline_node is not None):
                    result["uncached"].append(ref)
                if EXTERNAL_REF.search(formula):
                    result["external"].append(ref)
            value = value_node.text if value_node is not None else None
            if kind == "e":
                result["errors"].append(
                    {
                        "ref": ref,
                        "row": row_index,
                        "col": column_index,
                        "error": (value or "#ERROR").strip(),
                        "formula": formula,
                    }
                )
            elif kind == "s" and value is not None and value.strip().isdigit():
                if int(value) in error_strings:
                    result["error_text"].append(ref)
            elif kind in ("str", "inlineStr"):
                text = value
                if inline_node is not None:
                    text = "".join(
                        child.text or "" for child in inline_node.iter() if local(child.tag) == "t"
                    )
                if text and text.strip() in ERROR_LITERALS:
                    result["error_text"].append(ref)
            node.clear()
    return result


def feature_inventory(path: Path) -> dict | None:
    """Count workbook features that a LibreOffice round trip may change."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            types = ET.fromstring(archive.read("[Content_Types].xml"))
            counts = Counter()
            for node in types:
                content_type = (node.get("ContentType") or "").lower()
                if local(node.tag) != "Override":
                    continue
                for feature, marker in FEATURE_CONTENT_TYPES.items():
                    if content_type.endswith(marker):
                        counts[feature] += 1
            counts["images"] = sum(1 for name in names if name.startswith("xl/media/"))
            workbook = read_workbook(archive)
            counts["sheets"] = len(workbook["sheets"])
            counts["defined_names"] = len(DEFINED_NAME.findall(archive.read(workbook["part"])))
            for sheet in workbook["sheets"]:
                data = archive.read(sheet["part"])
                for feature, pattern in SHEET_ELEMENTS.items():
                    counts[feature] += len(pattern.findall(data))
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return None
    keys = ["sheets", *FEATURE_CONTENT_TYPES, "images", "defined_names", *SHEET_ELEMENTS]
    return {key: counts.get(key, 0) for key in keys}


# ---------------------------------------------------------------------------
# Root-cause analysis


def parse_bounds(ref: str) -> tuple[int, int, int, int] | None:
    ref = ref.replace("$", "").upper()
    if not CELL_RANGE.match(ref):
        return None
    try:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
    except ValueError:
        return None
    return (min_col or 1, min_row or 1, max_col or 16384, max_row or 1048576)


def name_targets(names: list[dict]) -> dict[str, list[tuple[str, tuple[int, int, int, int]]]]:
    """Resolve simple defined names (Sheet!$A$1 or Sheet!$A$1:$B$2) to sheet ranges."""
    targets: dict[str, list] = defaultdict(list)
    for item in names:
        match = NAME_TARGET.match(item["definition"])
        if not match:
            continue
        sheet = (match.group(1) or "").replace("''", "'") or match.group(2)
        bounds = parse_bounds(match.group(3))
        if bounds:
            targets[item["name"].lower()].append((sheet, bounds))
    return targets


def referenced_ranges(
    formula: str, host: str, names: dict
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Sheet ranges a formula reads, as far as they can be resolved without evaluating it."""
    if not formula.startswith("="):
        return []
    try:
        tokens = Tokenizer(formula).items
    except Exception:
        return []
    ranges = []
    for token in tokens:
        if token.type != Token.OPERAND or token.subtype != Token.RANGE:
            continue
        value = token.value
        sheet = host
        if "!" in value:
            prefix, value = value.rsplit("!", 1)
            prefix = prefix.strip("'").replace("''", "'")
            if "[" in prefix or ":" in prefix:
                continue  # external workbook or 3-D reference: not resolvable here
            sheet = prefix
        bounds = parse_bounds(value)
        if bounds:
            ranges.append((sheet, bounds))
        else:
            ranges.extend(names.get(value.lower(), []))
    return ranges


def strongly_connected(graph: dict) -> list[list]:
    """Tarjan's algorithm, iterative; returns the components of ``graph``."""
    index: dict = {}
    low: dict = {}
    stack: list = []
    on_stack: set = set()
    components = []
    counter = 0
    for start in graph:
        if start in index:
            continue
        work = [(start, iter(graph[start]))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph[child])))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def root_causes(errors: list[dict], names: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """Split error cells into origins (no erroring input) and members of circular loops."""
    if len(errors) > ROOT_ANALYSIS_LIMIT:
        return None
    resolved = name_targets(names)
    by_sheet: dict[str, list[dict]] = defaultdict(list)
    for item in errors:
        by_sheet[item["sheet"].lower()].append(item)
    graph: dict[int, set[int]] = {}
    for position, item in enumerate(errors):
        item["_id"] = position
    for item in errors:
        upstream: set[int] = set()
        for sheet, (min_col, min_row, max_col, max_row) in referenced_ranges(
            item["formula"] or "", item["sheet"], resolved
        ):
            for other in by_sheet.get(sheet.lower(), []):
                if min_col <= other["col"] <= max_col and min_row <= other["row"] <= max_row:
                    upstream.add(other["_id"])
        graph[item["_id"]] = upstream
    origins = [item for item in errors if not graph[item["_id"]]]
    loops = []
    for component in strongly_connected(graph):
        if len(component) > 1 or component[0] in graph[component[0]]:
            loops.extend(errors[member] for member in sorted(component))
    return origins, loops


# ---------------------------------------------------------------------------
# Report


def describe(item: dict, reason: str | None = None) -> dict:
    entry = {"cell": location(item["sheet"], item["ref"]), "error": item["error"]}
    if item["formula"]:
        entry["formula"] = item["formula"]
    if reason:
        entry["reason"] = reason
    return entry


def scan_workbook(path: Path, max_locations: int) -> dict:
    with zipfile.ZipFile(path) as archive:
        workbook = read_workbook(archive)
        error_strings = error_like_shared_strings(archive, workbook["shared_strings"])
        sheets = []
        errors = []
        uncached = []
        error_text = []
        external = []
        for sheet in workbook["sheets"]:
            scanned = scan_sheet(archive, sheet, error_strings)
            summary = {
                "name": sheet["name"],
                "formulas": scanned["formulas"],
                "errors": len(scanned["errors"]),
            }
            if sheet["state"] != "visible":
                summary["state"] = sheet["state"]
            sheets.append(summary)
            for item in scanned["errors"]:
                item["sheet"] = sheet["name"]
                errors.append(item)
            uncached.extend(location(sheet["name"], ref) for ref in scanned["uncached"])
            error_text.extend(location(sheet["name"], ref) for ref in scanned["error_text"])
            external.extend(location(sheet["name"], ref) for ref in scanned["external"])

    by_type: dict[str, dict] = {}
    for item in errors:
        group = by_type.setdefault(item["error"], {"count": 0, "locations": []})
        group["count"] += 1
        if len(group["locations"]) < max_locations:
            group["locations"].append(location(item["sheet"], item["ref"]))
    for name, group in by_type.items():
        group["truncated"] = group["count"] > len(group["locations"])
        if name in ERROR_HINTS:
            group["hint"] = ERROR_HINTS[name]

    analysis = root_causes(errors, workbook["names"])
    if analysis is None:
        causes: list[dict] | None = None
        circular: list[dict] = []
    else:
        origins, loops = analysis
        causes = [
            describe(
                item, "external-link" if EXTERNAL_REF.search(item["formula"] or "") else "origin"
            )
            for item in origins
        ] + [describe(item, "circular") for item in loops]
        circular = [describe(item) for item in loops]

    def capped(values: list) -> dict:
        return {
            "count": len(values),
            "locations": values[:max_locations],
            "truncated": len(values) > max_locations,
        }

    broken_names = sorted(
        {item["name"] for item in workbook["names"] if "#REF!" in item["definition"].upper()}
    )
    return {
        "total_formulas": sum(sheet["formulas"] for sheet in sheets),
        "total_errors": len(errors),
        "errors_by_type": dict(sorted(by_type.items(), key=lambda pair: -pair[1]["count"])),
        "root_causes": None if causes is None else causes[:max_locations],
        "root_causes_truncated": False if causes is None else len(causes) > max_locations,
        "circular_references": circular[:max_locations],
        "sheets": sheets,
        "cached_values_missing": capped(uncached),
        "error_like_text": capped(error_text),
        "external_reference_cells": capped(external),
        "broken_defined_names": broken_names,
    }


def default_output(source: Path) -> Path:
    candidate = source.with_name(f"{source.stem}.recalc.xlsx")
    number = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}.recalc-{number}.xlsx")
        number += 1
    return candidate


def run_recalculation(source: Path, target: Path, timeout_ms: int | None) -> dict:
    """Run dsoffice recalculate; returns its JSON result or raises RuntimeError."""
    executable = shutil.which("dsoffice")
    if executable is None:
        raise RuntimeError("dsoffice is not on PATH; recalculation is unavailable in this session")
    command = [executable, "recalculate", "--input", str(source), "--output", str(target)]
    if timeout_ms:
        command += ["--timeout-ms", str(timeout_ms)]
    completed = subprocess.run(command, capture_output=True, text=True)
    payload: dict = {}
    for line in reversed(completed.stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    if completed.returncode != 0 or not target.is_file():
        detail = payload.get("error") or completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"dsoffice recalculate failed (exit {completed.returncode}): {detail}")
    return payload


def emit(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input", type=Path, help="workbook to check (.xlsx; .xls/.ods are recalculated to .xlsx)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new .xlsx path for the recalculated copy (default: <name>.recalc.xlsx)",
    )
    parser.add_argument(
        "--no-recalc",
        action="store_true",
        help="scan the cached values as saved, without recalculating",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace an existing --output file (a previous run's copy); never the input",
    )
    parser.add_argument("--report", type=Path, help="also write the JSON report to this file")
    parser.add_argument(
        "--allow-errors", action="store_true", help="exit 0 even when error values remain"
    )
    parser.add_argument(
        "--max-locations", type=int, default=50, help="locations listed per error type (default 50)"
    )
    parser.add_argument(
        "--timeout-ms", type=int, help="recalculation timeout passed to dsoffice (default 120000)"
    )
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    suffix = source.suffix.lower()
    if not source.is_file():
        parser.error(f"input not found: {source}")
    if args.max_locations < 0:
        parser.error("--max-locations must be zero or more")
    allowed = SCAN_INPUTS if args.no_recalc else RECALC_INPUTS
    if suffix not in allowed:
        mode = "--no-recalc scans" if args.no_recalc else "recalculation accepts"
        parser.error(f"{mode} only {', '.join(sorted(allowed))} files, not {suffix or 'this file'}")
    if args.no_recalc and args.output is not None:
        parser.error("--output applies only when recalculating")

    report: dict = {"status": "failed", "input": str(source), "recalculated": not args.no_recalc}
    target = source
    if not args.no_recalc:
        target = (
            args.output.expanduser().resolve()
            if args.output is not None
            else default_output(source)
        )
        if target.suffix.lower() != ".xlsx":
            parser.error("--output must be an .xlsx path")
        if target == source:
            parser.error("--output must differ from the input workbook")
        if target.exists() and not args.replace:
            parser.error(f"--output already exists (pass --replace to overwrite it): {target}")
        # dsoffice only writes new files: recalculate beside the target, then swap it in.
        fresh = target
        if target.exists():
            fresh = target.with_name(f".{target.stem}.{os.getpid()}.recalc-tmp.xlsx")
        try:
            result = run_recalculation(source, fresh, args.timeout_ms)
            if fresh != target:
                os.replace(fresh, target)
        except (OSError, RuntimeError) as error:
            if fresh != target:
                fresh.unlink(missing_ok=True)  # the previous output stays as it was
            report["detail"] = str(error)
            emit(report, args.report)
            return 3
        report["output"] = str(target)
        report["missing_fonts"] = result.get("missingFonts", [])

    try:
        report.update(scan_workbook(target, args.max_locations))
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as error:
        report["detail"] = f"could not read {target}: {error}"
        emit(report, args.report)
        return 3

    notes = []
    if report["total_errors"]:
        report["status"] = "errors"
        notes.append(
            "Fix root_causes first, then run this check again on the edited source workbook."
        )
    elif report["cached_values_missing"]["count"]:
        report["status"] = "unverified"
        notes.append(
            "Some formulas have no cached result, so their values were not checked. "
            "Run without --no-recalc."
        )
    else:
        report["status"] = "clean"
    if report["external_reference_cells"]["count"]:
        notes.append(
            "Formulas link to other workbooks. Those files are not available to the "
            "recalculation, so errors or stale values in these cells may come from the missing "
            "source, not the formula."
        )
    if report["broken_defined_names"]:
        notes.append("Defined names point at #REF!; delete or re-point them.")
    if not args.no_recalc and suffix in SCAN_INPUTS:
        before, after = feature_inventory(source), feature_inventory(target)
        if before is not None and after is not None:
            dropped = [
                {"feature": key, "before": before[key], "after": after.get(key, 0)}
                for key in before
                if after.get(key, 0) < before[key]
            ]
            report["fidelity"] = {
                "dropped": dropped,
                "before": {key: value for key, value in before.items() if value},
                "after": {key: value for key, value in after.items() if value},
            }
            if dropped:
                notes.append(
                    "The recalculated copy lost features listed in fidelity.dropped. Deliver "
                    "the source workbook (set wb.calculation.fullCalcOnLoad = True so Excel "
                    "recalculates on open) and use the recalculated copy only for checking."
                )
    if (
        report["status"] == "clean"
        and not args.no_recalc
        and not report.get("fidelity", {}).get("dropped")
    ):
        notes.append(
            "The recalculated copy keeps the formulas and adds fresh cached values; "
            "it is the file to deliver."
        )
    report["notes"] = notes
    emit(report, args.report)
    if report["status"] == "clean" or args.allow_errors:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
