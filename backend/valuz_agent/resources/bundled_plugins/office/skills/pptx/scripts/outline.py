#!/usr/bin/env python3
"""Print a per-slide outline of a .pptx: layout, title, text, tables, charts, pictures, notes.

    valuz-python outline.py deck.pptx                 # readable outline
    valuz-python outline.py deck.pptx --json          # same content as JSON
    valuz-python outline.py deck.pptx --slides 2,5-7  # only some slides
    valuz-python outline.py deck.pptx --layouts       # also list layouts and their placeholders
    valuz-python outline.py deck.pptx --geometry      # add each shape's position/size in inches

Shapes are listed top-to-bottom, left-to-right; group members are indented
under their group. Use --layouts when filling a template: it shows which
placeholder idx/type each layout offers.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

EMU_PER_INCH = 914400
TITLE_TYPES = {"TITLE", "CENTER_TITLE", "VERTICAL_TITLE"}


def open_deck(path: Path) -> Presentation:
    """Open a .pptx, or a .potx template (python-pptx only accepts the presentation type)."""
    if Path(path).suffix.lower() != ".potx":
        return Presentation(str(path))
    buffer = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"presentationml.template.main+xml", b"presentationml.presentation.main+xml"
                )
            out.writestr(item, data)
    buffer.seek(0)
    return Presentation(buffer)


def inches(value) -> float:
    return round(Emu(value or 0) / EMU_PER_INCH, 2)


def placeholder_type(shape) -> str | None:
    if not shape.is_placeholder:
        return None
    try:
        return shape.placeholder_format.type.name
    except (AttributeError, ValueError):
        return "UNKNOWN"


def paragraphs(text_frame) -> list[dict]:
    result = []
    for paragraph in text_frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs) if paragraph.runs else paragraph.text
        text = text.replace("\x0b", " / ")
        if text.strip():
            result.append({"level": paragraph.level, "text": text})
    return result


def chart_info(chart) -> dict:
    info: dict = {"type": None, "categories": [], "series": []}
    try:
        info["type"] = chart.chart_type.name
    except (AttributeError, ValueError, KeyError):
        pass
    try:
        plot = chart.plots[0]
        info["categories"] = [str(c) for c in plot.categories]
    except (IndexError, AttributeError, KeyError, ValueError):
        pass
    for plot in chart.plots:
        for series in plot.series:
            try:
                values = list(series.values)
            except (AttributeError, KeyError, ValueError):
                values = []
            info["series"].append({"name": series.name, "values": values})
    if chart.has_title and chart.chart_title.has_text_frame:
        info["title"] = chart.chart_title.text_frame.text
    return info


def describe(shape, geometry: bool) -> dict:
    item: dict = {"name": shape.name}
    ph = placeholder_type(shape)
    if ph:
        item["placeholder"] = ph
        item["idx"] = shape.placeholder_format.idx
    if geometry:
        item["box_in"] = [
            inches(shape.left),
            inches(shape.top),
            inches(shape.width),
            inches(shape.height),
        ]
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        item["kind"] = "group"
        item["children"] = [describe(child, geometry) for child in ordered(shape.shapes)]
    elif getattr(shape, "has_table", False) and shape.has_table:
        item["kind"] = "table"
        item["rows"] = [
            [cell.text.replace("\n", " / ") for cell in row.cells] for row in shape.table.rows
        ]
    elif getattr(shape, "has_chart", False) and shape.has_chart:
        item["kind"] = "chart"
        item["chart"] = chart_info(shape.chart)
    elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE or (
        ph == "PICTURE" and hasattr(shape, "image")
    ):
        item["kind"] = "picture"
        try:
            item["image"] = shape.image.filename or shape.image.content_type
        except (AttributeError, ValueError, KeyError):
            pass
    elif shape.has_text_frame and (
        ph or shape.text_frame.text.strip() or shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX
    ):
        item["kind"] = "title" if ph in TITLE_TYPES else "text"
        item["paragraphs"] = paragraphs(shape.text_frame)
    elif shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
        item["kind"] = "shape"
        try:
            item["shape"] = shape.auto_shape_type.name.lower()
        except (AttributeError, ValueError, NotImplementedError):
            pass
    else:
        item["kind"] = "graphic"
    return item


def ordered(shapes) -> list:
    def key(shape):
        is_title = placeholder_type(shape) in TITLE_TYPES
        return (0 if is_title else 1, shape.top or 0, shape.left or 0)

    return sorted(shapes, key=key)


def slide_title(slide) -> str:
    for shape in slide.placeholders:
        if placeholder_type(shape) in TITLE_TYPES and shape.has_text_frame:
            return shape.text_frame.text.replace("\x0b", " ").strip()
    return ""


def notes_text(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text.strip() if frame is not None else ""


def layouts(prs) -> list[dict]:
    result = []
    for master_number, master in enumerate(prs.slide_masters, 1):
        for layout in master.slide_layouts:
            result.append(
                {
                    "master": master_number,
                    "name": layout.name,
                    "placeholders": [
                        {
                            "idx": ph.placeholder_format.idx,
                            "type": placeholder_type(ph),
                            "name": ph.name,
                            "box_in": [
                                inches(ph.left),
                                inches(ph.top),
                                inches(ph.width),
                                inches(ph.height),
                            ],
                        }
                        for ph in layout.placeholders
                    ],
                }
            )
    return result


def build(path: Path, numbers: set[int] | None, geometry: bool, with_layouts: bool) -> dict:
    prs = open_deck(path)
    report: dict = {
        "file": str(path),
        "slide_size_in": [inches(prs.slide_width), inches(prs.slide_height)],
        "slide_count": len(prs.slides),
        "slides": [],
    }
    for number, slide in enumerate(prs.slides, 1):
        if numbers and number not in numbers:
            continue
        report["slides"].append(
            {
                "number": number,
                "layout": slide.slide_layout.name,
                "hidden": slide._element.get("show") == "0",
                "title": slide_title(slide),
                "shapes": [describe(shape, geometry) for shape in ordered(slide.shapes)],
                "notes": notes_text(slide),
            }
        )
    if with_layouts:
        report["layouts"] = layouts(prs)
    return report


def render_shape(item: dict, depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    label = item.get("placeholder", "").lower() or item["kind"]
    if "idx" in item:
        label += f" idx={item['idx']}"
    box = ""
    if "box_in" in item:
        left, top, width, height = item["box_in"]
        box = f" @({left}, {top}) {width}x{height}in"
    head = f'{pad}[{label} "{item["name"]}"{box}]'
    kind = item["kind"]
    if kind in ("title", "text"):
        paras = item["paragraphs"]
        if not paras:
            lines.append(f"{head} (empty)")
        elif len(paras) == 1 and paras[0]["level"] == 0:
            lines.append(f"{head} {paras[0]['text']}")
        else:
            lines.append(head)
            lines.extend(f"{pad}  {'  ' * p['level']}- {p['text']}" for p in paras)
    elif kind == "table":
        rows = item["rows"]
        lines.append(f"{head} {len(rows)}x{len(rows[0]) if rows else 0} table")
        lines.extend(f"{pad}  | " + " | ".join(row) + " |" for row in rows)
    elif kind == "chart":
        chart = item["chart"]
        lines.append(
            f"{head} {chart.get('type') or 'chart'}"
            + (f" '{chart['title']}'" if chart.get("title") else "")
        )
        if chart["categories"]:
            lines.append(f"{pad}  categories: {', '.join(chart['categories'][:20])}")
        for series in chart["series"]:
            values = ", ".join("" if v is None else f"{v:g}" for v in series["values"][:20])
            lines.append(f"{pad}  series {series['name']!r}: {values}")
    elif kind == "picture":
        lines.append(f"{head} {item.get('image', '')}".rstrip())
    elif kind == "shape":
        lines.append(f"{head} {item.get('shape', '')}".rstrip())
    elif kind == "group":
        lines.append(head)
        for child in item["children"]:
            render_shape(child, depth + 1, lines)
    else:
        lines.append(head)


def render_text(report: dict) -> str:
    width, height = report["slide_size_in"]
    lines = [f"{report['file']}: {report['slide_count']} slides, {width} x {height} in"]
    for slide in report["slides"]:
        flags = " (hidden)" if slide["hidden"] else ""
        lines.append("")
        lines.append(f'## Slide {slide["number"]}{flags} - layout "{slide["layout"]}"')
        lines.append(f"Title: {slide['title'] or '(none)'}")
        for item in slide["shapes"]:
            if item["kind"] == "title" and item.get("paragraphs") and "box_in" not in item:
                continue  # already printed as "Title:"
            render_shape(item, 0, lines)
        if slide["notes"]:
            lines.append("Notes: " + slide["notes"].replace("\n", "\n       "))
    if "layouts" in report:
        lines.append("")
        lines.append("## Layouts")
        for layout in report["layouts"]:
            parts = [f"{ph['idx']}:{(ph['type'] or '').lower()}" for ph in layout["placeholders"]]
            lines.append(
                f'- [master {layout["master"]}] "{layout["name"]}": '
                f"{', '.join(parts) or 'no placeholders'}"
            )
    return "\n".join(lines) + "\n"


def parse_numbers(spec: str) -> set[int]:
    numbers: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start, end = (int(x) for x in chunk.split("-", 1))
            numbers.update(range(min(start, end), max(start, end) + 1))
        elif chunk:
            numbers.add(int(chunk))
    return numbers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--slides", help="1-based slide list, e.g. 1,3-5")
    parser.add_argument(
        "--layouts", action="store_true", help="list slide layouts and their placeholders"
    )
    parser.add_argument("--geometry", action="store_true", help="include shape positions and sizes")
    parser.add_argument("--out", type=Path, help="also write the output to this file")
    args = parser.parse_args(argv)
    report = build(
        args.input, parse_numbers(args.slides) if args.slides else None, args.geometry, args.layouts
    )
    output = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json
        else render_text(report)
    )
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
