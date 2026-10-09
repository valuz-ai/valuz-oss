"""Unpack an OOXML file into a folder for XML editing, and pack it back.

    valuz-python docx_pack.py unpack INPUT.docx FOLDER [--raw]
    valuz-python docx_pack.py pack   FOLDER OUTPUT.docx [--raw] [--force]

unpack  Extracts every member into a new (or empty) FOLDER and indents the
        .xml/.rels files so they are readable and diff-friendly (--raw keeps
        the bytes as they were).
pack    Validates and zips FOLDER: every XML file must parse, every part must
        have a content type, and every internal relationship target must
        exist. Writes [Content_Types].xml first and _rels/.rels second, then
        the remaining members in a stable order. The indentation added by
        unpack is removed again (--raw packs files unchanged); whitespace
        inside w:t, w:delText, w:instrText and similar text elements is kept.
        Refuses to overwrite OUTPUT unless --force.

Works for .docx, .xlsx and .pptx. Run check_office.py on the packed file.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree
from ooxml_package import (
    CT_NS,
    FIRST_MEMBERS,
    PKG_REL_NS,
    local,
    parse_xml,
    resolve_target,
    serialize_xml,
    write_zip,
)

# Elements whose text content is significant and must never be re-indented.
TEXT_ELEMENTS = {"t", "delText", "instrText", "delInstrText", "v", "f", "formula"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _xml_member(name: str) -> bool:
    return name.endswith((".xml", ".rels"))


def _indent(data: bytes) -> bytes:
    root = parse_xml(data)
    if _has_mixed_content(root):
        return data
    etree.indent(root, space="  ")
    return serialize_xml(root)


def _has_mixed_content(root: etree._Element) -> bool:
    """True when an element mixes text and child elements (re-indenting would change it)."""
    for node in root.iter():
        if not isinstance(node.tag, str) or len(node) == 0:
            continue
        if (node.text or "").strip():
            return True
        if any((child.tail or "").strip() for child in node):
            return True
    return False


def _condense(data: bytes) -> bytes:
    root = parse_xml(data)
    for node in root.iter():
        if not isinstance(node.tag, str) or local(node.tag) in TEXT_ELEMENTS:
            continue
        if len(node) and node.text is not None and not node.text.strip():
            node.text = None
        for child in node:
            if child.tail is not None and not child.tail.strip():
                child.tail = None
    return serialize_xml(root)


def unpack(source: Path, folder: Path, raw: bool) -> dict:
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} is not empty; unpack into a new folder")
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = (folder / info.filename).resolve()
            if folder.resolve() not in target.parents:
                raise ValueError(f"unsafe member path: {info.filename}")
            data = archive.read(info)
            if not raw and _xml_member(info.filename):
                data = _indent(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            names.append(info.filename)
    return {"folder": str(folder), "members": len(names), "indented": not raw}


def _collect(folder: Path) -> list[str]:
    members = []
    for path in sorted(folder.rglob("*")):
        if path.is_dir() or path.name in SKIP_NAMES:
            continue
        members.append(PurePosixPath(path.relative_to(folder)).as_posix())
    return members


def _validate(folder: Path, members: list[str], xml: dict[str, etree._Element]) -> list[str]:
    problems = []
    if "[Content_Types].xml" not in xml:
        return ["missing [Content_Types].xml"]
    types = xml["[Content_Types].xml"]
    defaults = {
        node.get("Extension", "").lower() for node in types if node.tag == f"{{{CT_NS}}}Default"
    }
    overrides = {node.get("PartName", "") for node in types if node.tag == f"{{{CT_NS}}}Override"}
    for name in members:
        if name == "[Content_Types].xml":
            continue
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if "/" + name not in overrides and extension not in defaults:
            problems.append(f"no content type for {name} (add an Override or Default)")
    for override in overrides:
        if override.lstrip("/") not in members:
            problems.append(f"[Content_Types].xml overrides a missing part: {override}")
    member_set = set(members)
    for name, root in xml.items():
        if not name.endswith(".rels"):
            continue
        source = (
            ""
            if name == "_rels/.rels"
            else str(PurePosixPath(name).parent.parent / PurePosixPath(name).name[: -len(".rels")])
        )
        for rel in root:
            if rel.tag != f"{{{PKG_REL_NS}}}Relationship" or rel.get("TargetMode") == "External":
                continue
            target = resolve_target(source, rel.get("Target", ""))
            if target not in member_set:
                problems.append(f"{name}: {rel.get('Id')} targets missing member {target}")
    return problems


def pack(folder: Path, output: Path, raw: bool, force: bool) -> dict:
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    if output.exists() and not force:
        raise ValueError(f"{output} exists; choose a new path or pass --force")
    members = _collect(folder)
    blobs: dict[str, bytes] = {}
    xml: dict[str, etree._Element] = {}
    errors = []
    for name in members:
        data = (folder / name).read_bytes()
        if _xml_member(name):
            try:
                xml[name] = parse_xml(data)
            except etree.XMLSyntaxError as error:
                errors.append(f"{name}: {error}")
                continue
            if not raw:
                data = _condense(data)
        blobs[name] = data
    if errors:
        raise ValueError("malformed XML: " + "; ".join(errors))
    problems = _validate(folder, members, xml)
    if problems:
        raise ValueError("package problems: " + "; ".join(problems))
    ordered = [n for n in FIRST_MEMBERS if n in blobs]
    main_first = sorted(
        (n for n in blobs if n not in FIRST_MEMBERS),
        key=lambda n: (not n.endswith(("document.xml", "workbook.xml", "presentation.xml")), n),
    )
    write_zip(output, [(n, blobs[n]) for n in ordered + main_first])
    return {"output": str(output), "members": len(blobs)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    unpack_cmd = sub.add_parser("unpack")
    unpack_cmd.add_argument("input", type=Path)
    unpack_cmd.add_argument("folder", type=Path)
    unpack_cmd.add_argument("--raw", action="store_true")
    pack_cmd = sub.add_parser("pack")
    pack_cmd.add_argument("folder", type=Path)
    pack_cmd.add_argument("output", type=Path)
    pack_cmd.add_argument("--raw", action="store_true")
    pack_cmd.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "unpack":
            result = unpack(args.input, args.folder, args.raw)
        else:
            result = pack(args.folder, args.output, args.raw, args.force)
    except (OSError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
