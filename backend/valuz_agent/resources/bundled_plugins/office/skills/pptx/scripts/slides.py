#!/usr/bin/env python3
"""Duplicate, delete, move and rearrange slides in a .pptx file.

python-pptx has no API for these operations. This script edits the package
directly and keeps it consistent:

* duplicate - copies the slide XML, shares images/media/layout with the
  source, and gives the copy its own charts (with their embedded workbooks),
  SmartArt parts, notes slide and other per-slide parts.
* delete - removes the slide from the slide list, custom shows and sections,
  and removes hyperlinks on other slides that jumped to it. Parts nothing else
  uses (notes, charts, unused images) are left out when the file is saved.
* move / arrange - reorder the slide list. ``arrange --order 1,3,3,5`` builds
  the deck from those source slides in that order: repeated numbers become
  duplicates, slides not listed are deleted.

Slide numbers are 1-based, matching ``dsoffice render`` page numbers and
``outline.py``. Each command writes OUTPUT (which must differ from INPUT) and
prints a JSON summary.

    valuz-python slides.py duplicate in.pptx out.pptx --slide 3 [--to 5] [--copies 2]
    valuz-python slides.py delete    in.pptx out.pptx --slides 2,4-6
    valuz-python slides.py move      in.pptx out.pptx --slide 5 --to 2
    valuz-python slides.py arrange   in.pptx out.pptx --order 1,3,3,5

The functions can also be imported (``sys.path.insert(0, "<skill>/scripts")``)
and used on an open ``Presentation``: duplicate_slide, delete_slide,
move_slide, arrange_slides. Indexes passed to the functions are 0-based.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part, PartFactory, XmlPart

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"

# Targets that several slides may point at: shared, never copied.
_SHARED_REL_SUFFIXES = (
    "/image",
    "/media",
    "/video",
    "/audio",
    "/hdphoto",
    "/slideLayout",
    "/slideMaster",
    "/notesMaster",
    "/handoutMaster",
    "/theme",
    "/slide",
    "/commentAuthors",
    "/presProps",
    "/viewProps",
    "/tableStyles",
)


# python-pptx overrides element.xpath(); compiled XPath objects take variables.
_BY_RID = etree.XPath("//*[@r:id=$rid]", namespaces={"r": R_NS})
_CUSTOM_SHOW_REFS = etree.XPath(
    "./p:custShowLst/p:custShow/p:sldLst/p:sld[@r:id=$rid]", namespaces={"p": P_NS, "r": R_NS}
)


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


def _is_shared(reltype: str) -> bool:
    return reltype.endswith(_SHARED_REL_SUFFIXES)


def _partname_template(partname: str) -> str:
    """'/ppt/charts/chart3.xml' -> '/ppt/charts/chart%d.xml'."""
    stem, dot, ext = partname.rpartition(".")
    if not dot:
        stem, ext = partname, ""
    stem = re.sub(r"\d+$", "", stem)
    return f"{stem}%d.{ext}" if ext else f"{stem}%d"


def _remap_rids(element: etree._Element, mapping: dict[str, str]) -> None:
    """Rewrite every r:* attribute (r:id, r:embed, r:link, r:dm, ...) through mapping."""
    if not mapping:
        return
    prefix = "{" + R_NS + "}"
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        for key, value in list(node.attrib.items()):
            if key.startswith(prefix) and value in mapping:
                node.set(key, mapping[value])


def _xml_root(part: Part) -> etree._Element | None:
    """The parsed XML of a part, or None for binary parts."""
    if isinstance(part, XmlPart):
        return part._element
    content_type = part.content_type
    if content_type.endswith("+xml") or content_type.endswith("/xml"):
        return etree.fromstring(part.blob)
    return None


def _copy_rels(
    source: Part, target: Part, cache: dict[Part, Part], redirect: dict[Part, Part]
) -> dict[str, str]:
    """Relate `target` to copies (or shared originals) of `source`'s targets; old->new rIds."""
    mapping: dict[str, str] = {}
    for rid, rel in list(source.rels.items()):
        if rel.is_external:
            mapping[rid] = target.relate_to(rel.target_ref, rel.reltype, is_external=True)
            continue
        part = rel.target_part
        if part in redirect:
            mapping[rid] = target.relate_to(redirect[part], rel.reltype)
        elif _is_shared(rel.reltype):
            mapping[rid] = target.relate_to(part, rel.reltype)
        else:
            mapping[rid] = target.relate_to(_copy_part(part, target, cache, redirect), rel.reltype)
    return mapping


def _copy_part(
    part: Part, owner: Part, cache: dict[Part, Part], redirect: dict[Part, Part]
) -> Part:
    """Deep-copy a part (and the non-shared parts below it) into owner's package."""
    if part in cache:
        return cache[part]
    package = owner.package
    partname = package.next_partname(_partname_template(str(part.partname)))
    clone = PartFactory(partname, part.content_type, package, part.blob)
    cache[part] = clone
    # Relate before recursing so next_partname sees the new name as taken.
    # The temporary relationship is dropped once the real one exists.
    probe_rid = owner.relate_to(clone, "urn:valuz:pending-copy")
    mapping = _copy_rels(part, clone, cache, redirect)
    root = _xml_root(clone)
    if root is not None:
        _remap_rids(root, mapping)
        if not isinstance(clone, XmlPart):
            clone.blob = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
    owner.rels.pop(probe_rid)
    return clone


def _sld_id_list(prs) -> etree._Element:
    return prs.part._element.find(f"{{{P_NS}}}sldIdLst")


def _check_index(prs, index: int) -> None:
    count = len(prs.slides)
    if not 0 <= index < count:
        raise IndexError(f"slide {index + 1} does not exist (deck has {count} slides)")


def duplicate_slide(prs, index: int, to: int | None = None):
    """Copy slide `index` (0-based) and insert the copy at `to` (default: right after it).

    Returns the new slide object.
    """
    _check_index(prs, index)
    source = prs.slides[index]
    source_part = source.part
    package = prs.part.package
    partname = package.next_partname("/ppt/slides/slide%d.xml")
    new_part = PartFactory(partname, source_part.content_type, package, source_part.blob)
    rid = prs.part.relate_to(new_part, RT.SLIDE)
    id_list = _sld_id_list(prs)
    used = [int(node.get("id")) for node in id_list]
    new_id = etree.SubElement(id_list, f"{{{P_NS}}}sldId")
    new_id.set("id", str(max([255, *used]) + 1))
    new_id.set(f"{{{R_NS}}}id", rid)

    cache: dict[Part, Part] = {}
    redirect = {source_part: new_part}
    mapping = _copy_rels(source_part, new_part, cache, redirect)
    _remap_rids(new_part._element, mapping)
    for node in new_part._element.iter(f"{{{P14_NS}}}creationId"):
        node.set("val", str(random.randint(1, 2**31 - 1)))

    id_list.remove(new_id)
    position = index + 1 if to is None else to
    position = max(0, min(position, len(id_list)))
    id_list.insert(position, new_id)
    return prs.slides[position]


def _remove_links_to(prs, slide_part: Part) -> None:
    """Drop slide-jump hyperlinks on other slides that target slide_part."""
    for slide in prs.slides:
        part = slide.part
        if part is slide_part:
            continue
        for rid, rel in list(part.rels.items()):
            if rel.is_external or rel.target_part is not slide_part:
                continue
            for node in _BY_RID(part._element, rid=rid):
                local = etree.QName(node).localname
                if local in ("hlinkClick", "hlinkHover", "hlinkMouseOver"):
                    node.getparent().remove(node)
                else:
                    del node.attrib[f"{{{R_NS}}}id"]
            part.rels.pop(rid)


def delete_slide(prs, index: int) -> None:
    """Remove slide `index` (0-based) from the deck."""
    _check_index(prs, index)
    id_list = _sld_id_list(prs)
    node = id_list[index]
    rid = node.get(f"{{{R_NS}}}id")
    slide_id = node.get("id")
    slide_part = prs.part.related_part(rid)
    id_list.remove(node)
    root = prs.part._element
    # Custom shows list slides by relationship id.
    for ref in _CUSTOM_SHOW_REFS(root, rid=rid):
        ref.getparent().remove(ref)
    # Sections (PowerPoint 2010+) list slides by slide id.
    for ref in root.iter("{*}sldId"):
        if ref.getparent() is not id_list and ref.get("id") == slide_id:
            ref.getparent().remove(ref)
    _remove_links_to(prs, slide_part)
    prs.part.drop_rel(rid)


def move_slide(prs, index: int, to: int) -> None:
    """Move slide `index` to position `to` (both 0-based)."""
    _check_index(prs, index)
    id_list = _sld_id_list(prs)
    node = id_list[index]
    id_list.remove(node)
    id_list.insert(max(0, min(to, len(id_list))), node)


def arrange_slides(prs, order: list[int]) -> None:
    """Rebuild the slide list from 0-based source indexes; repeats duplicate, omissions delete."""
    if not order:
        raise ValueError("order must name at least one slide")
    for index in order:
        _check_index(prs, index)
    id_list = _sld_id_list(prs)
    originals = list(id_list)
    final: list[etree._Element] = []
    seen: set[int] = set()
    for index in order:
        if index in seen:
            duplicate_slide(prs, index, to=len(id_list))
            final.append(id_list[-1])
        else:
            seen.add(index)
            final.append(originals[index])
    # Delete unused originals from the highest index down so indexes stay valid.
    for index in sorted(set(range(len(originals))) - seen, reverse=True):
        delete_slide(prs, list(id_list).index(originals[index]))
    for node in final:
        id_list.remove(node)
    for node in final:
        id_list.append(node)


def parse_numbers(spec: str) -> list[int]:
    """'1,3-5,3' -> [1, 3, 4, 5, 3] (1-based, order and repeats kept)."""
    numbers: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = (int(x) for x in chunk.split("-", 1))
            step = 1 if end >= start else -1
            numbers.extend(range(start, end + step, step))
        else:
            numbers.append(int(chunk))
    if not numbers or min(numbers) < 1:
        raise ValueError(f"slide numbers must be 1-based: {spec!r}")
    return numbers


def _source_numbers(prs) -> list[int]:
    return [int(node.get("id")) for node in _sld_id_list(prs)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("input", type=Path)
        sub.add_argument("output", type=Path)
        return sub

    dup = add("duplicate", "copy one slide")
    dup.add_argument("--slide", type=int, required=True, help="1-based slide to copy")
    dup.add_argument(
        "--to",
        type=int,
        help="1-based position of the (first) copy; default right after the source",
    )
    dup.add_argument("--copies", type=int, default=1)
    rm = add("delete", "delete slides")
    rm.add_argument("--slides", required=True, help="1-based list, e.g. 2,4-6")
    mv = add("move", "move one slide")
    mv.add_argument("--slide", type=int, required=True)
    mv.add_argument("--to", type=int, required=True, help="1-based target position")
    arr = add("arrange", "rebuild the deck from a list of source slides")
    arr.add_argument(
        "--order", required=True, help="1-based source slides in final order; repeats duplicate"
    )
    args = parser.parse_args(argv)

    if args.output.resolve() == args.input.resolve():
        parser.error("output must be a new file, not the input")
    try:
        prs = open_deck(args.input)
        before = _source_numbers(prs)
        if args.command == "duplicate":
            if args.copies < 1:
                parser.error("--copies must be at least 1")
            position = args.slide if args.to is None else args.to - 1
            for offset in range(args.copies):
                duplicate_slide(prs, args.slide - 1, to=position + offset)
        elif args.command == "delete":
            targets = sorted(set(parse_numbers(args.slides)), reverse=True)
            if len(targets) >= len(prs.slides):
                parser.error("refusing to delete every slide")
            for number in targets:
                delete_slide(prs, number - 1)
        elif args.command == "move":
            move_slide(prs, args.slide - 1, args.to - 1)
        else:
            arrange_slides(prs, [n - 1 for n in parse_numbers(args.order)])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(args.output))
    except (IndexError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1
    # Map each output slide back to the source slide it came from (None for new copies).
    origin = {slide_id: i + 1 for i, slide_id in enumerate(before)}
    sources = [origin.get(slide_id) for slide_id in _source_numbers(prs)]
    print(
        json.dumps(
            {"status": "ok", "output": str(args.output), "slides": len(sources), "sources": sources}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
