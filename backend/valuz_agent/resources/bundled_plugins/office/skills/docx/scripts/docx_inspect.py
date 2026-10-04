"""Read a .docx: outline text, tracked changes, comments.

    valuz-python docx_inspect.py text     INPUT.docx [--view final|original|markup] [--json]
    valuz-python docx_inspect.py changes  INPUT.docx [--json]
    valuz-python docx_inspect.py comments INPUT.docx [--json]

text      Body in reading order as a light Markdown outline: headings as #,
          list items with their numbers, tables as | rows |, [image: alt],
          footnote references [^n] (notes listed at the end), uncomputed
          fields as {CODE}; then headers and footers per section.
          --view final (default) shows the text with tracked changes
          accepted, original with them rejected, markup with {+ins+} {-del-}.
changes   Every tracked change: type, author, date, part, paragraph, text.
comments  Every comment: id, author, date, the text it is anchored to, its
          text, reply parent and resolved state when the file records them.

Read-only; never modifies the input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docx_text import DocumentModel, TextExtractor, own_paragraph
from lxml import etree
from ooxml_package import (
    REL_COMMENTS,
    REL_COMMENTS_EXTENDED,
    REL_ENDNOTES,
    REL_FOOTER,
    REL_FOOTNOTES,
    REL_HEADER,
    W14_NS,
    W15_NS,
    Package,
    local,
    w,
)

# ---------------------------------------------------------------- text view


class OutlineWriter:
    def __init__(self, package: Package, view: str):
        self.package = package
        self.view = view
        self.model = DocumentModel(package)
        self.lines: list[str] = []
        self.blocks: list[dict] = []
        self.table_count = 0

    def run(self) -> None:
        main = self.model.main
        body = self.package.xml(main).find(w("body"))
        extractor = TextExtractor(self.package, main, self.view)
        self._blocks(body, extractor, depth=0)
        self._notes()
        self._headers_footers(body)

    def _blocks(self, container: etree._Element, extractor: TextExtractor, depth: int) -> None:
        for child in container:
            name = local(child.tag)
            if name == "p":
                self._paragraph(child, extractor)
            elif name == "tbl":
                self._table(child, extractor, depth)
            elif name in ("sdt", "customXml"):
                content = child.find(w("sdtContent")) if name == "sdt" else child
                if content is not None:
                    self._blocks(content, extractor, depth)

    def _paragraph(self, paragraph: etree._Element, extractor: TextExtractor) -> None:
        text = extractor.paragraph(paragraph)
        level = self.model.heading_level(paragraph)
        style = self.model.styles.name(self.model.paragraph_style(paragraph))
        listed = self.model.list_info(paragraph)
        block: dict = {"type": "paragraph", "style": style, "text": text}
        if style.lower() in ("title", "subtitle") and text.strip():
            line = f"[{style}] {text}"
        elif level is not None and text.strip():
            line = "#" * level + " " + text
            block["heading_level"] = level
        elif listed:
            num_id, ilvl = listed
            label = self.model.numbering.label(num_id, ilvl)
            line = "  " * ilvl + f"{label} {text}"
            block["list"] = {"label": label, "level": ilvl}
        else:
            line = text
        self.lines.append(line)
        self.blocks.append(block)
        for box in paragraph.iter(w("txbxContent")):
            if any(local(a.tag) == "Fallback" for a in box.iterancestors()):
                continue  # VML copy of a DrawingML text box
            for inner in box.iter(w("p")):
                if own_paragraph_in(inner, box):
                    self.lines.append("[text box] " + extractor.paragraph(inner))
        if paragraph.find(f"{w('pPr')}/{w('sectPr')}") is not None:
            self.lines.append("=== section break ===")

    def _table(self, table: etree._Element, extractor: TextExtractor, depth: int) -> None:
        self.table_count += 1
        rows = []
        for tr in table.findall(w("tr")):
            cells = []
            for tc in tr.findall(w("tc")):
                vmerge = tc.find(f"{w('tcPr')}/{w('vMerge')}")
                if vmerge is not None and vmerge.get(w("val"), "continue") == "continue":
                    text = ""
                else:
                    parts = []
                    for p in tc.iter(w("p")):
                        if own_cell(p) is tc:
                            parts.append(extractor.paragraph(p))
                    text = " / ".join(part for part in parts if part.strip())
                span = tc.find(f"{w('tcPr')}/{w('gridSpan')}")
                cells.append(text.replace("\n", " ").replace("|", "\\|"))
                cells.extend([""] * (int(span.get(w("val"), "1")) - 1 if span is not None else 0))
            rows.append(cells)
        width = max((len(r) for r in rows), default=0)
        self.lines.append(f"[table {self.table_count}: {len(rows)} rows x {width} columns]")
        for row in rows:
            self.lines.append("| " + " | ".join(row) + " |")
        self.blocks.append({"type": "table", "rows": rows})

    def _notes(self) -> None:
        main = self.model.main
        for rel_type, kind, prefix in (
            (REL_FOOTNOTES, "footnote", "^"),
            (REL_ENDNOTES, "endnote", "^e"),
        ):
            for part in self.package.related(main, rel_type):
                extractor = TextExtractor(self.package, part, self.view)
                notes = []
                for note in self.package.xml(part).findall(w(kind)):
                    if note.get(w("type")) in (
                        "separator",
                        "continuationSeparator",
                        "continuationNotice",
                    ):
                        continue
                    text = " ".join(extractor.paragraph(p).strip() for p in note.findall(w("p")))
                    notes.append((note.get(w("id")), text))
                if notes:
                    self.lines.append("")
                    self.lines.append(f"[{kind}s]")
                    for note_id, text in notes:
                        self.lines.append(f"[{prefix}{note_id}] {text}")
                        self.blocks.append({"type": kind, "id": note_id, "text": text})

    def _headers_footers(self, body: etree._Element) -> None:
        main = self.model.main
        rels = self.package.rel_targets(main)
        sections = list(body.iter(w("sectPr")))
        seen: dict[str, list[str]] = {}
        order: list[tuple[str, str, str]] = []
        for number, sect in enumerate(sections, start=1):
            for ref in sect:
                kind = local(ref.tag)
                if kind not in ("headerReference", "footerReference"):
                    continue
                rel_id = ref.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""
                )
                target = rels.get(rel_id)
                if target is None or target[0] not in (REL_HEADER, REL_FOOTER):
                    continue
                part = target[1]
                label = f"{kind[:-9]} {ref.get(w('type'), 'default')}"
                if part not in seen:
                    seen[part] = []
                    order.append((part, label, kind))
                seen[part].append(str(number))
        for part, label, _kind in order:
            if not self.package.has(part):
                continue
            extractor = TextExtractor(self.package, part, self.view)
            texts = [extractor.paragraph(p) for p in self.package.xml(part).iter(w("p"))]
            text = " / ".join(t.strip() for t in texts if t.strip())
            if not text:
                continue
            self.lines.append("")
            self.lines.append(f"[{label}, section {', '.join(seen[part])}] {text}")
            self.blocks.append(
                {
                    "type": label.split()[0],
                    "variant": label.split()[1],
                    "sections": seen[part],
                    "text": text,
                }
            )


def own_paragraph_in(paragraph: etree._Element, box: etree._Element) -> bool:
    node = paragraph.getparent()
    while node is not None and node is not box:
        if local(node.tag) == "txbxContent":
            return False
        node = node.getparent()
    return True


def own_cell(paragraph: etree._Element) -> etree._Element | None:
    node = paragraph.getparent()
    while node is not None and local(node.tag) != "tc":
        node = node.getparent()
    return node


# ---------------------------------------------------------------- tracked changes

RUN_CHANGES = {"ins": "insert", "del": "delete", "moveFrom": "move-from", "moveTo": "move-to"}
PROPERTY_CHANGES = {
    "rPrChange": "format",
    "pPrChange": "paragraph-format",
    "sectPrChange": "section-format",
    "tblPrChange": "table-format",
    "trPrChange": "row-format",
    "tcPrChange": "cell-format",
    "tblGridChange": "table-grid",
    "numberingChange": "numbering",
}


def story_parts(package: Package, main: str) -> list[str]:
    parts = [main]
    for rel_type in (REL_HEADER, REL_FOOTER, REL_FOOTNOTES, REL_ENDNOTES, REL_COMMENTS):
        parts.extend(p for p in package.related(main, rel_type) if package.has(p))
    return parts


def _change_text(element: etree._Element) -> str:
    texts = []
    for node in element.iter(w("t"), w("delText"), w("tab")):
        texts.append("\t" if local(node.tag) == "tab" else node.text or "")
    return "".join(texts)


def list_changes(package: Package) -> list[dict]:
    model = DocumentModel(package)
    changes = []
    for part in story_parts(package, model.main):
        root = package.xml(part)
        paragraphs = {p: i for i, p in enumerate(root.iter(w("p")), start=1)}
        markup = TextExtractor(package, part, "markup", links=False)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            name = local(node.tag)
            parent = local(node.getparent().tag) if node.getparent() is not None else ""
            if name in RUN_CHANGES and node.tag == w(name):
                if parent == "rPr":
                    kind = "paragraph-mark-" + RUN_CHANGES[name]
                elif parent == "trPr":
                    kind = "row-" + RUN_CHANGES[name]
                elif parent == "numPr":
                    kind = "numbering"
                else:
                    kind = RUN_CHANGES[name]
                text = _change_text(node) if parent not in ("rPr", "trPr") else ""
            elif name in PROPERTY_CHANGES and node.tag == w(name):
                kind, text = PROPERTY_CHANGES[name], ""
            elif name in ("cellIns", "cellDel", "cellMerge") and node.tag == w(name):
                kind, text = (
                    {"cellIns": "cell-insert", "cellDel": "cell-delete", "cellMerge": "cell-merge"}[
                        name
                    ],
                    "",
                )
            else:
                continue
            paragraph = own_paragraph(node)
            context = markup.paragraph(paragraph) if paragraph is not None else ""
            changes.append(
                {
                    "id": node.get(w("id")),
                    "type": kind,
                    "author": node.get(w("author"), ""),
                    "date": node.get(w("date"), ""),
                    "part": part,
                    "paragraph": paragraphs.get(paragraph) if paragraph is not None else None,
                    "text": text,
                    "context": context[:200],
                }
            )
    return changes


# ---------------------------------------------------------------- comments


def list_comments(package: Package) -> list[dict]:
    model = DocumentModel(package)
    main = model.main
    parts = package.related(main, REL_COMMENTS)
    if not parts or not package.has(parts[0]):
        return []
    comments_root = package.xml(parts[0])
    extractor = TextExtractor(package, parts[0], "final", links=False)
    para_to_comment: dict[str, str] = {}
    comments: dict[str, dict] = {}
    for comment in comments_root.findall(w("comment")):
        comment_id = comment.get(w("id"), "")
        paragraphs = comment.findall(w("p"))
        text = "\n".join(extractor.paragraph(p).strip() for p in paragraphs).strip()
        for p in paragraphs:
            para_id = p.get(f"{{{W14_NS}}}paraId")
            if para_id:
                para_to_comment[para_id] = comment_id
        comments[comment_id] = {
            "id": comment_id,
            "author": comment.get(w("author"), ""),
            "initials": comment.get(w("initials"), ""),
            "date": comment.get(w("date"), ""),
            "anchor": "",
            "text": text,
            "reply_to": None,
            "resolved": None,
        }
    for extended_part in package.related(main, REL_COMMENTS_EXTENDED):
        if not package.has(extended_part):
            continue
        for entry in package.xml(extended_part).iter(f"{{{W15_NS}}}commentEx"):
            comment_id = para_to_comment.get(entry.get(f"{{{W15_NS}}}paraId", ""))
            if comment_id is None:
                continue
            parent = entry.get(f"{{{W15_NS}}}paraIdParent")
            comments[comment_id]["reply_to"] = para_to_comment.get(parent) if parent else None
            comments[comment_id]["resolved"] = entry.get(f"{{{W15_NS}}}done") in ("1", "true")
    _collect_anchors(package.xml(main).find(w("body")), comments)
    return list(comments.values())


def _collect_anchors(body: etree._Element, comments: dict[str, dict]) -> None:
    open_ranges: list[str] = []
    for node in body.iter():
        if not isinstance(node.tag, str):
            continue
        name = local(node.tag)
        if name == "commentRangeStart":
            open_ranges.append(node.get(w("id"), ""))
        elif name == "commentRangeEnd":
            comment_id = node.get(w("id"), "")
            if comment_id in open_ranges:
                open_ranges.remove(comment_id)
        elif name in ("t", "delText") and open_ranges:
            for comment_id in open_ranges:
                if comment_id in comments:
                    comments[comment_id]["anchor"] += node.text or ""
        elif name == "tab" and open_ranges and local(node.getparent().tag) == "r":
            for comment_id in open_ranges:
                if comment_id in comments:
                    comments[comment_id]["anchor"] += "\t"
        elif name == "p" and open_ranges:
            for comment_id in open_ranges:
                if comment_id in comments and comments[comment_id]["anchor"]:
                    comments[comment_id]["anchor"] += "\n"
    for comment in comments.values():
        comment["anchor"] = comment["anchor"].strip("\n")


# ---------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["text", "changes", "comments"])
    parser.add_argument("input", type=Path)
    parser.add_argument("--view", choices=["final", "original", "markup"], default="final")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    try:
        package = Package(args.input)
        if args.command == "text":
            writer = OutlineWriter(package, args.view)
            writer.run()
            if args.json:
                print(json.dumps(writer.blocks, ensure_ascii=False, indent=1))
            else:
                changes = sum(1 for c in list_changes(package) if c["type"] in RUN_CHANGES.values())
                if changes and args.view == "final":
                    print(
                        f"(document has {changes} tracked insertions/deletions;"
                        " shown accepted, use --view markup)"
                    )
                print("\n".join(writer.lines))
        elif args.command == "changes":
            changes = list_changes(package)
            if args.json:
                print(json.dumps(changes, ensure_ascii=False, indent=1))
            else:
                print(f"{len(changes)} tracked changes")
                for c in changes:
                    where = f"{c['part']} p{c['paragraph']}" if c["paragraph"] else c["part"]
                    text = f' "{c["text"]}"' if c["text"] else ""
                    author = c["author"] or "?"
                    print(f"- [{c['id']}] {c['type']}{text} by {author} {c['date']} ({where})")
        else:
            comments = list_comments(package)
            if args.json:
                print(json.dumps(comments, ensure_ascii=False, indent=1))
            else:
                print(f"{len(comments)} comments")
                for c in comments:
                    extra = f" reply-to={c['reply_to']}" if c["reply_to"] else ""
                    extra += " resolved" if c["resolved"] else ""
                    print(f"- [{c['id']}] {c['author']} {c['date']}{extra}")
                    print(f'  on: "{c["anchor"]}"')
                    print(f"  says: {c['text']}")
    except (OSError, KeyError, ValueError, etree.XMLSyntaxError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
