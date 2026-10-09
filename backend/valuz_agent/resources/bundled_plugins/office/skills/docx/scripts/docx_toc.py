"""Fill a document's table-of-contents field with entries and page numbers.

    valuz-python docx_toc.py INPUT.docx OUTPUT.docx [--update-on-open]

python-docx cannot paginate, and LibreOffice does not rebuild a TOC field
when it opens a file. This script:

1. finds every TOC field (e.g. the placeholder from docx_helpers.add_toc, or a
   stale TOC written by Word) and the headings it covers (\\o "1-3" levels,
   heading styles or paragraph outline levels);
2. bookmarks each heading and writes one entry per heading inside the TOC
   field (hyperlink when the field has \\h, right tab with dot leader, a
   PAGEREF field for the page number);
3. lays the document out once with ``dsoffice`` to obtain the page numbers and
   stores them as the fields' results.

Word keeps the TOC as a real field (References > Update Table works).
Without dsoffice the entries are written with empty page numbers and the
JSON reports it; Word fills them when it updates fields. --update-on-open sets
w:updateFields so Word refreshes all fields when the file is opened (Word asks
the user first).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx_text import DocumentModel, TextExtractor
from lxml import etree
from ooxml_package import (
    REL_SETTINGS,
    REL_STYLES,
    SETTINGS_ORDER,
    XML_SPACE,
    Package,
    insert_in_order,
    local,
    next_annotation_id,
    w,
)

TOC_LEVELS = re.compile(r'\\o\s+"?(\d)\s*-\s*(\d)"?')


def _el(tag: str, **attrs: str) -> etree._Element:
    node = etree.Element(w(tag))
    for key, value in attrs.items():
        node.set(w(key), value)
    return node


def _run(*children: etree._Element) -> etree._Element:
    run = _el("r")
    for child in children:
        run.append(child)
    return run


def _text(value: str) -> etree._Element:
    node = _el("t")
    node.set(XML_SPACE, "preserve")
    node.text = value
    return node


def _fld(kind: str) -> etree._Element:
    return _run(_el("fldChar", fldCharType=kind))


def _instr(code: str) -> etree._Element:
    node = _el("instrText")
    node.set(XML_SPACE, "preserve")
    node.text = f" {code.strip()} "
    return _run(node)


def find_toc_fields(body: etree._Element) -> list[dict]:
    """Top-level TOC fields as {begin, end, code} (fldChar elements)."""
    fields: list[dict] = []
    stack: list[dict] = []
    for node in body.iter(w("fldChar"), w("instrText")):
        if node.tag == w("instrText"):
            if stack and stack[-1]["phase"] == "code":
                stack[-1]["code"] += node.text or ""
            continue
        kind = node.get(w("fldCharType"))
        if kind == "begin":
            stack.append({"begin": node, "code": "", "phase": "code"})
        elif kind == "separate" and stack:
            stack[-1]["phase"] = "result"
        elif kind == "end" and stack:
            field = stack.pop()
            field["end"] = node
            if not stack and field["code"].strip().upper().startswith("TOC"):
                fields.append(field)
    return fields


def _paragraph_of(node: etree._Element) -> etree._Element:
    while node is not None and node.tag != w("p"):
        node = node.getparent()
    if node is None:
        raise ValueError("field character outside a paragraph")
    return node


def _section_text_width(body: etree._Element, paragraph: etree._Element) -> int:
    """Text width in twips of the section that contains ``paragraph``."""
    following = False
    sect = None
    for node in body.iter(w("p")):
        if node is paragraph:
            following = True
        if following:
            sect = node.find(f"{w('pPr')}/{w('sectPr')}")
            if sect is not None:
                break
    if sect is None:
        sect = body.find(w("sectPr"))
    width = 12240
    left = right = 1440
    gutter = 0
    if sect is not None:
        size = sect.find(w("pgSz"))
        margins = sect.find(w("pgMar"))
        if size is not None:
            width = int(size.get(w("w"), width))
        if margins is not None:
            left = int(margins.get(w("left"), left))
            right = int(margins.get(w("right"), right))
            gutter = int(margins.get(w("gutter"), 0))
    return width - left - right - gutter


def _ensure_toc_styles(package: Package, model: DocumentModel, levels: range) -> dict[int, str]:
    styles_parts = package.related(model.main, REL_STYLES)
    if not styles_parts:
        return {}
    root = package.xml(styles_parts[0])
    normal = model.styles.default_paragraph
    ids: dict[int, str] = {}
    for level in levels:
        existing = model.styles.id_for_name(f"toc {level}")
        if existing:
            ids[level] = existing
            continue
        style_id = f"TOC{level}"
        style = _el("style", type="paragraph", styleId=style_id)
        style.append(_el("name", val=f"toc {level}"))
        if normal:
            style.append(_el("basedOn", val=normal))
            style.append(_el("next", val=normal))
        style.append(_el("uiPriority", val="39"))
        style.append(_el("unhideWhenUsed"))
        ppr = _el("pPr")
        ppr.append(_el("spacing", after="100"))
        ppr.append(_el("ind", left=str(220 * (level - 1))))
        style.append(ppr)
        root.append(style)
        model.styles.styles[style_id] = style
        ids[level] = style_id
    return ids


def _collect_headings(package, model, body, toc_paragraphs, lo, hi):
    extractor = TextExtractor(package, model.main, view="final", links=False, notes=False)
    model.numbering.counters = {}
    headings = []
    for paragraph in body.iter(w("p")):
        if any(local(a.tag) == "txbxContent" for a in paragraph.iterancestors()):
            continue
        listed = model.list_info(paragraph)
        label = model.numbering.label(*listed) if listed else ""
        if paragraph in toc_paragraphs:
            continue
        level = model.heading_level(paragraph)
        if level is None or not lo <= level <= hi:
            continue
        text = " ".join(extractor.paragraph(paragraph).replace("\t", " ").split())
        if not text:
            continue
        if label and label != "-":
            text = f"{label} {text}"
        headings.append({"paragraph": paragraph, "level": level, "text": text})
    return headings


def _bookmark_headings(package: Package, headings: list[dict]) -> None:
    root_names = set()
    for item in headings:
        for start in item["paragraph"].iter(w("bookmarkStart")):
            root_names.add(start.get(w("name"), ""))
    next_id = next_annotation_id(package)
    serial = 100000000
    for item in headings:
        paragraph = item["paragraph"]
        existing = [
            s.get(w("name"))
            for s in paragraph.findall(w("bookmarkStart"))
            if (s.get(w("name")) or "").startswith("_Toc")
        ]
        if existing:
            item["bookmark"] = existing[0]
            continue
        while f"_Toc{serial}" in root_names:
            serial += 1
        name = f"_Toc{serial}"
        root_names.add(name)
        start = _el("bookmarkStart", id=str(next_id), name=name)
        end = _el("bookmarkEnd", id=str(next_id))
        next_id += 1
        ppr = paragraph.find(w("pPr"))
        if ppr is not None:
            ppr.addnext(start)
        else:
            paragraph.insert(0, start)
        paragraph.append(end)
        item["bookmark"] = name


def _entry_paragraphs(headings, styles, tab_pos, code, hyperlinks, page_numbers):
    paragraphs = []
    for item in headings:
        paragraph = _el("p")
        ppr = _el("pPr")
        if item["level"] in styles:
            ppr.append(_el("pStyle", val=styles[item["level"]]))
        tabs = _el("tabs")
        tabs.append(_el("tab", val="right", leader="dot", pos=str(tab_pos)))
        ppr.append(tabs)
        paragraph.append(ppr)
        holder = paragraph
        if hyperlinks:
            holder = _el("hyperlink", anchor=item["bookmark"], history="1")
            paragraph.append(holder)
        holder.append(_run(_text(item["text"])))
        if page_numbers:
            holder.append(_run(_el("tab")))
            holder.append(_fld("begin"))
            holder.append(_instr(f"PAGEREF {item['bookmark']} \\h"))
            holder.append(_fld("separate"))
            result = _run(_text(""))
            result.set("toc-result", item["bookmark"])
            holder.append(result)
            holder.append(_fld("end"))
        paragraphs.append(paragraph)
    if not paragraphs:
        paragraph = _el("p")
        paragraph.append(_run(_text("No headings found for the table of contents.")))
        paragraphs.append(paragraph)
    first = paragraphs[0]
    anchor = first.find(w("pPr"))
    for node in reversed([_fld("begin"), _instr(code), _fld("separate")]):
        node.set("toc-outer", "1")
        anchor.addnext(node)
    closing = _fld("end")
    closing.set("toc-outer", "1")
    paragraphs[-1].append(closing)
    return paragraphs


def _page_numbers_from(package_path: Path) -> dict[str, str]:
    """Bookmark -> computed page number from a LibreOffice-written .docx."""
    package = Package(package_path)
    root = package.xml(package.main_part())
    tokens = []
    for node in root.iter(w("fldChar"), w("instrText"), w("t")):
        if node.tag == w("fldChar"):
            tokens.append(("fld", node.get(w("fldCharType"))))
        elif node.tag == w("instrText"):
            tokens.append(("instr", node.text or ""))
        else:
            tokens.append(("t", node.text or ""))
    pages: dict[str, str] = {}
    for index, (kind, value) in enumerate(tokens):
        match = re.match(r"\s*PAGEREF\s+(\S+)", value) if kind == "instr" else None
        if not match or match.group(1) in pages:
            continue
        position = index + 1
        while position < len(tokens) and tokens[position] != ("fld", "separate"):
            position += 1
        position += 1
        text = ""
        while position < len(tokens) and tokens[position][0] == "t":
            text += tokens[position][1]
            position += 1
        if text.strip():
            pages[match.group(1)] = text.strip()
    return pages


def _compute_pages(package: Package, workdir: Path) -> tuple[dict[str, str], str]:
    dsoffice = shutil.which("dsoffice")
    if dsoffice is None:
        return {}, "dsoffice not found on PATH; page numbers left empty"
    main = package.main_part()
    probe = copy.deepcopy(package.xml(main))
    for node in list(probe.iter()):
        if node.get("toc-outer"):
            node.getparent().remove(node)
        elif node.get("toc-result") is not None:
            del node.attrib["toc-result"]
    original = package.xml(main)
    package.set_xml(main, probe)
    layout_in = workdir / "layout-input.docx"
    layout_out = workdir / "layout-output.docx"
    try:
        package.save(layout_in)
    finally:
        package.set_xml(main, original)
    result = subprocess.run(
        [dsoffice, "convert", "--input", str(layout_in), "--output", str(layout_out)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0 or not layout_out.exists():
        detail = (result.stdout + result.stderr).strip()[-500:]
        return {}, f"dsoffice convert failed; page numbers left empty: {detail}"
    return _page_numbers_from(layout_out), "computed with dsoffice (LibreOffice pagination)"


def _set_update_fields(package: Package, main: str) -> None:
    settings = package.related(main, REL_SETTINGS)
    if not settings:
        return
    root = package.xml(settings[0])
    for node in root.findall(w("updateFields")):
        root.remove(node)
    node = _el("updateFields", val="true")
    insert_in_order(root, node, tuple(SETTINGS_ORDER[SETTINGS_ORDER.index("updateFields") + 1 :]))


def fill_toc(source: Path, target: Path, update_on_open: bool = False) -> dict:
    package = Package(source)
    model = DocumentModel(package)
    main = model.main
    root = package.xml(main)
    body = root.find(w("body"))
    fields = find_toc_fields(body)
    if not fields:
        raise ValueError("no TOC field found; insert one with docx_helpers.add_toc()")
    report: dict = {"tocs": []}
    plans = []
    for field in fields:
        first = _paragraph_of(field["begin"])
        last = _paragraph_of(field["end"])
        if first.getparent() is not last.getparent():
            raise ValueError("TOC field spans different containers; cannot rebuild it")
        siblings = list(first.getparent())
        span = siblings[siblings.index(first) : siblings.index(last) + 1]
        plans.append({"field": field, "span": span})
    toc_paragraphs = {p for plan in plans for p in plan["span"]}
    for plan in plans:
        code = " ".join(plan["field"]["code"].split())
        match = TOC_LEVELS.search(code)
        lo, hi = (int(match.group(1)), int(match.group(2))) if match else (1, 3)
        headings = _collect_headings(package, model, body, toc_paragraphs, lo, hi)
        _bookmark_headings(package, headings)
        styles = _ensure_toc_styles(package, model, range(lo, hi + 1))
        width = _section_text_width(body, plan["span"][0])
        page_numbers = not re.search(r"\\n(\s|$)", code)
        entries = _entry_paragraphs(headings, styles, width, code, "\\h" in code, page_numbers)
        anchor = plan["span"][0]
        for paragraph in entries:
            anchor.addprevious(paragraph)
        for paragraph in plan["span"]:
            paragraph.getparent().remove(paragraph)
        report["tocs"].append(
            {
                "field": code,
                "entries": [
                    {"level": h["level"], "text": h["text"], "bookmark": h["bookmark"]}
                    for h in headings
                ],
            }
        )
    with tempfile.TemporaryDirectory(prefix="docx-toc-") as tmp:
        pages, status = _compute_pages(package, Path(tmp))
    for node in list(root.iter()):
        bookmark = node.get("toc-result")
        if bookmark is not None:
            del node.attrib["toc-result"]
            node.find(w("t")).text = pages.get(bookmark, "")
        if node.get("toc-outer"):
            del node.attrib["toc-outer"]
    for toc in report["tocs"]:
        for entry in toc["entries"]:
            entry["page"] = pages.get(entry["bookmark"], "")
    if update_on_open:
        _set_update_fields(package, main)
    package.save(target)
    report["page_numbers"] = status
    report["output"] = str(target)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--update-on-open", action="store_true", help="ask Word to refresh fields on open"
    )
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve():
        parser.error("output must be a new path")
    try:
        report = fill_toc(args.input, args.output, args.update_on_open)
    except (ValueError, KeyError, OSError, etree.XMLSyntaxError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
