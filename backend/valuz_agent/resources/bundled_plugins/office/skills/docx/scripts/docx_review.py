"""Tracked changes and comments on an existing .docx, edited at the XML level.

    valuz-python docx_review.py replace  IN.docx OUT.docx --find OLD --with NEW
    valuz-python docx_review.py delete   IN.docx OUT.docx --find TEXT
    valuz-python docx_review.py insert   IN.docx OUT.docx --anchor TEXT --text NEW [--before]
    valuz-python docx_review.py insert-paragraph IN.docx OUT.docx --anchor TEXT --text NEW
                                         [--before] [--style NAME]
    valuz-python docx_review.py delete-paragraph IN.docx OUT.docx --find TEXT
    valuz-python docx_review.py comment  IN.docx OUT.docx --find TEXT --text COMMENT
    valuz-python docx_review.py accept-all IN.docx OUT.docx
    valuz-python docx_review.py reject-all IN.docx OUT.docx
    valuz-python docx_review.py apply    IN.docx OUT.docx --ops OPS.json

Edits become real Word revisions (w:ins / w:del with author and date) that
keep the formatting of the runs they touch; comments are anchored to the
matched range. Matching is on the visible text of one paragraph at a time
(body, tables, text boxes); use the exact text printed by docx_inspect.py.

Selection: --occurrence N (1-based, default 1) or --all. Edits other than
comment refuse text that is already inside a tracked insertion or a field code.
Revision metadata: --author (default "Valuz"), --initials, --date
(ISO 8601, default now UTC). replace/delete/insert/insert-paragraph/
delete-paragraph accept --comment TEXT to explain the change.

accept-all / reject-all resolve every revision in the document, headers,
footers, notes and comments (text, paragraph marks, table rows and cells,
formatting changes, moves) without LibreOffice.

OPS.json for apply: a list of objects such as
  {"op": "replace", "find": "30 days", "with": "45 days", "comment": "Per legal"}
  {"op": "comment", "find": "Section 4", "text": "Please confirm", "occurrence": 2}
  {"op": "insert", "anchor": "Annex A", "text": " and Annex B"}
Keys mirror the long options (with -> "with", insert-paragraph style -> "style").
Prints a JSON summary; exit 1 when an operation fails (nothing is written).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from docx_text import StyleSheet
from lxml import etree
from ooxml_package import (
    CT_COMMENTS,
    MC_NS,
    R_NS,
    REL_COMMENTS,
    REL_STYLES,
    W_NS,
    XML_SPACE,
    Package,
    local,
    next_annotation_id,
    ns_of,
    utc_now,
    w,
)

TEXT_CHILDREN = {"t", "tab", "br", "cr", "noBreakHyphen"}
REVISION_CONTAINERS = {"ins", "del", "moveFrom", "moveTo"}
RANGE_MARKERS = {
    "moveFromRangeStart",
    "moveFromRangeEnd",
    "moveToRangeStart",
    "moveToRangeEnd",
    "customXmlInsRangeStart",
    "customXmlInsRangeEnd",
    "customXmlDelRangeStart",
    "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart",
    "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart",
    "customXmlMoveToRangeEnd",
}
PASSIVE_SIBLINGS = {
    "bookmarkStart",
    "bookmarkEnd",
    "commentRangeStart",
    "commentRangeEnd",
    "proofErr",
    "permStart",
    "permEnd",
}


class ReviewError(Exception):
    pass


def _el(tag: str, **attrs: str) -> etree._Element:
    node = etree.Element(w(tag))
    for key, value in attrs.items():
        node.set(w(key), value)
    return node


def _text_children(text: str) -> list[etree._Element]:
    """w:t / w:tab / w:br nodes for a string with optional tabs and newlines."""
    nodes: list[etree._Element] = []
    for line_number, line in enumerate(text.split("\n")):
        if line_number:
            nodes.append(_el("br"))
        for index, chunk in enumerate(line.split("\t")):
            if index:
                nodes.append(_el("tab"))
            if chunk:
                node = _el("t")
                node.set(XML_SPACE, "preserve")
                node.text = chunk
                nodes.append(node)
    return nodes


def _clean_rpr(rpr: etree._Element | None) -> etree._Element | None:
    if rpr is None:
        return None
    rpr = copy.deepcopy(rpr)
    for child in list(rpr):
        if local(child.tag) in ("rPrChange", "ins", "del", "moveFrom", "moveTo"):
            rpr.remove(child)
    return rpr


class Reviewer:
    def __init__(self, package: Package, author: str, date: str, initials: str):
        self.package = package
        self.author = author
        self.date = date
        self.initials = initials
        self.main = package.main_part()
        self.root = package.xml(self.main)
        self.body = self.root.find(w("body"))
        if self.body is None:
            raise ReviewError("document has no body")
        self._next_id = next_annotation_id(package)
        styles = package.related(self.main, REL_STYLES)
        self.styles = StyleSheet(
            package.xml(styles[0]) if styles and package.has(styles[0]) else None
        )

    # ---------------------------------------------------------------- ids, metadata

    def new_id(self) -> str:
        value = self._next_id
        self._next_id += 1
        return str(value)

    def revision(self, tag: str) -> etree._Element:
        return _el(tag, id=self.new_id(), author=self.author, date=self.date)

    # ---------------------------------------------------------------- text model

    def paragraphs(self):
        for paragraph in self.body.iter(w("p")):
            if any(
                ns_of(a.tag) == MC_NS and local(a.tag) == "Fallback"
                for a in paragraph.iterancestors()
            ):
                continue
            yield paragraph

    @staticmethod
    def owner(node: etree._Element) -> etree._Element | None:
        parent = node.getparent()
        while parent is not None and parent.tag != w("p"):
            parent = parent.getparent()
        return parent

    def visible_runs(self, paragraph: etree._Element) -> list[etree._Element]:
        runs = []
        for run in paragraph.iter(w("r")):
            if self.owner(run) is not paragraph:
                continue
            hidden = False
            node = run.getparent()
            while node is not None and node is not paragraph:
                if local(node.tag) in ("del", "moveFrom") or (
                    ns_of(node.tag) == MC_NS and local(node.tag) == "Fallback"
                ):
                    hidden = True
                    break
                node = node.getparent()
            if not hidden:
                runs.append(run)
        return runs

    def char_map(self, paragraph: etree._Element):
        """Visible text and, per character, (run, child element, offset in child)."""
        text: list[str] = []
        cmap: list[tuple[etree._Element, etree._Element, int]] = []
        for run in self.visible_runs(paragraph):
            for child in run:
                name = local(child.tag)
                if name not in TEXT_CHILDREN or child.tag != w(name):
                    continue
                if name == "t":
                    value = child.text or ""
                elif name == "tab":
                    value = "\t"
                elif name == "noBreakHyphen":
                    value = "-"
                else:
                    if child.get(w("type")) in ("page", "column"):
                        continue
                    value = "\n"
                for offset, char in enumerate(value):
                    text.append(char)
                    cmap.append((run, child, offset))
        return "".join(text), cmap

    def find(
        self, needle: str, occurrence: int | None, every: bool
    ) -> list[tuple[etree._Element, int, int]]:
        if not needle:
            raise ReviewError("search text is empty")
        matches = []
        for paragraph in self.paragraphs():
            text, _ = self.char_map(paragraph)
            start = text.find(needle)
            while start != -1:
                matches.append((paragraph, start, start + len(needle)))
                start = text.find(needle, start + len(needle))
        if not matches:
            raise ReviewError(f"text not found: {needle!r}")
        if every:
            return matches
        index = (occurrence or 1) - 1
        if not 0 <= index < len(matches):
            raise ReviewError(
                f"occurrence {occurrence} of {needle!r} not found ({len(matches)} matches)"
            )
        return [matches[index]]

    # ---------------------------------------------------------------- run surgery

    @staticmethod
    def explode(run: etree._Element) -> None:
        """Give every content child of ``run`` its own run with the same properties."""
        rpr = run.find(w("rPr"))
        children = [c for c in run if c.tag != w("rPr")]
        anchor = run
        for child in children[1:]:
            twin = etree.Element(run.tag, dict(run.attrib))
            if rpr is not None:
                twin.append(copy.deepcopy(rpr))
            twin.append(child)
            anchor.addnext(twin)
            anchor = twin

    def split_at(self, paragraph: etree._Element, index: int) -> None:
        _, cmap = self.char_map(paragraph)
        if index <= 0 or index >= len(cmap):
            return
        run, child, offset = cmap[index]
        if offset == 0:
            return
        if child.tag != w("t"):
            raise ReviewError("cannot split inside a non-text run element")
        tail = copy.deepcopy(run)
        tail_t = [c for c in tail if c.tag == w("t")][0]
        text = child.text or ""
        child.text = text[:offset]
        child.set(XML_SPACE, "preserve")
        tail_t.text = text[offset:]
        tail_t.set(XML_SPACE, "preserve")
        run.addnext(tail)

    def isolate(self, paragraph: etree._Element, start: int, end: int) -> list[etree._Element]:
        """Split runs so [start, end) of the visible text is exactly a list of whole runs."""
        _, cmap = self.char_map(paragraph)
        for run in dict.fromkeys(entry[0] for entry in cmap[start:end]):
            self.explode(run)
        self.split_at(paragraph, start)
        self.split_at(paragraph, end)
        _, cmap = self.char_map(paragraph)
        return list(dict.fromkeys(entry[0] for entry in cmap[start:end]))

    @staticmethod
    def check_editable(runs: list[etree._Element], paragraph: etree._Element) -> None:
        for run in runs:
            if run.find(w("fldChar")) is not None or run.find(w("instrText")) is not None:
                raise ReviewError("match includes a field code; edit around the field")
            node = run.getparent()
            while node is not None and node is not paragraph:
                name = local(node.tag)
                if name in ("ins", "moveTo"):
                    raise ReviewError(
                        "match is inside an existing tracked insertion;"
                        " accept or reject that change first"
                    )
                if name == "fldSimple":
                    raise ReviewError("match is inside a field result")
                node = node.getparent()

    @staticmethod
    def spans(runs: list[etree._Element]) -> list[list[etree._Element]]:
        """Sibling spans covering ``runs``, never crossing an existing revision element."""
        groups: list[list[etree._Element]] = []
        for run in runs:
            if groups and groups[-1][-1].getparent() is run.getparent():
                groups[-1].append(run)
            else:
                groups.append([run])
        result = []
        for group in groups:
            span: list[etree._Element] = []
            node = group[0]
            last = group[-1]
            while node is not None:
                if local(node.tag) in REVISION_CONTAINERS:
                    if span:
                        result.append(span)
                    span = []
                else:
                    span.append(node)
                if node is last:
                    break
                node = node.getnext()
            if span:
                result.append(span)
        return result

    def mark_deleted(self, runs: list[etree._Element]) -> list[etree._Element]:
        deletions = []
        for span in self.spans(runs):
            deletion = self.revision("del")
            span[0].addprevious(deletion)
            for node in span:
                deletion.append(node)
            for node in deletion.iter(w("t")):
                node.tag = w("delText")
            for node in deletion.iter(w("instrText")):
                node.tag = w("delInstrText")
            deletions.append(deletion)
        return deletions

    def inserted_run(self, text: str, rpr: etree._Element | None) -> etree._Element:
        insertion = self.revision("ins")
        run = _el("r")
        cleaned = _clean_rpr(rpr)
        if cleaned is not None:
            run.append(cleaned)
        for node in _text_children(text):
            run.append(node)
        insertion.append(run)
        return insertion

    # ---------------------------------------------------------------- comments

    def comments_root(self) -> etree._Element:
        targets = self.package.related(self.main, REL_COMMENTS)
        if targets and self.package.has(targets[0]):
            return self.package.xml(targets[0])
        name = "word/comments.xml"
        number = 1
        while self.package.has(name):
            name = f"word/comments{number}.xml"
            number += 1
        root = etree.Element(w("comments"), nsmap={"w": W_NS, "r": R_NS})
        self.package.set_xml(name, root)
        self.package.set_override(name, CT_COMMENTS)
        self.package.add_relationship(self.main, REL_COMMENTS, name)
        return root

    def add_comment(self, first: etree._Element, last: etree._Element, text: str) -> str:
        root = self.comments_root()
        ids = [
            int(c.get(w("id")))
            for c in root.findall(w("comment"))
            if (c.get(w("id")) or "").isdigit()
        ]
        comment_id = str(max(ids, default=-1) + 1)
        first.addprevious(_el("commentRangeStart", id=comment_id))
        end = _el("commentRangeEnd", id=comment_id)
        last.addnext(end)
        reference = _el("r")
        reference_style = self.styles.id_for_name("annotation reference")
        if reference_style:
            rpr = _el("rPr")
            rpr.append(_el("rStyle", val=reference_style))
            reference.append(rpr)
        reference.append(_el("commentReference", id=comment_id))
        top = end
        paragraph = self.owner(end)
        while top.getparent() is not paragraph:
            top = top.getparent()
        top.addnext(reference)
        comment = _el(
            "comment", id=comment_id, author=self.author, date=self.date, initials=self.initials
        )
        text_style = self.styles.id_for_name("annotation text")
        for index, line in enumerate(text.split("\n")):
            paragraph_el = _el("p")
            if text_style:
                ppr = _el("pPr")
                ppr.append(_el("pStyle", val=text_style))
                paragraph_el.append(ppr)
            if index == 0:
                marker = _el("r")
                if reference_style:
                    rpr = _el("rPr")
                    rpr.append(_el("rStyle", val=reference_style))
                    marker.append(rpr)
                marker.append(_el("annotationRef"))
                paragraph_el.append(marker)
            run = _el("r")
            for node in _text_children(line):
                run.append(node)
            paragraph_el.append(run)
            comment.append(paragraph_el)
        root.append(comment)
        return comment_id

    # ---------------------------------------------------------------- operations

    def _targets(self, needle: str, occurrence: int | None, every: bool):
        matches = self.find(needle, occurrence, every)
        # Later matches first, so earlier offsets in the same paragraph stay valid.
        return sorted(matches, key=lambda m: m[1], reverse=True)

    def replace(self, find: str, new: str, occurrence=None, every=False, comment=None) -> int:
        targets = self._targets(find, occurrence, every)
        for paragraph, start, end in targets:
            runs = self.isolate(paragraph, start, end)
            self.check_editable(runs, paragraph)
            rpr = runs[0].find(w("rPr"))
            deletions = self.mark_deleted(runs)
            insertion = self.inserted_run(new, rpr)
            deletions[-1].addnext(insertion)
            if comment:
                self.add_comment(deletions[0], insertion, comment)
        return len(targets)

    def delete(self, find: str, occurrence=None, every=False, comment=None) -> int:
        targets = self._targets(find, occurrence, every)
        for paragraph, start, end in targets:
            runs = self.isolate(paragraph, start, end)
            self.check_editable(runs, paragraph)
            deletions = self.mark_deleted(runs)
            if comment:
                self.add_comment(deletions[0], deletions[-1], comment)
        return len(targets)

    def insert(
        self, anchor: str, text: str, before=False, occurrence=None, every=False, comment=None
    ) -> int:
        targets = self._targets(anchor, occurrence, every)
        for paragraph, start, end in targets:
            runs = self.isolate(paragraph, start, end)
            reference = runs[0] if before else runs[-1]
            rpr = reference.find(w("rPr"))
            node = reference
            in_link = False
            while node.getparent() is not paragraph and local(node.getparent().tag) in (
                "ins",
                "del",
                "moveTo",
                "moveFrom",
                "fldSimple",
                "hyperlink",
                "smartTag",
            ):
                in_link = in_link or local(node.getparent().tag) == "hyperlink"
                node = node.getparent()
            if in_link:
                rpr = self._plain_rpr(paragraph)
            insertion = self.inserted_run(text, rpr)
            if before:
                node.addprevious(insertion)
            else:
                node.addnext(insertion)
            if comment:
                self.add_comment(insertion, insertion, comment)
        return len(targets)

    def _plain_rpr(self, paragraph: etree._Element) -> etree._Element | None:
        for run in self.visible_runs(paragraph):
            if not any(local(a.tag) == "hyperlink" for a in run.iterancestors()):
                return run.find(w("rPr"))
        return None

    def _paragraph_matches(
        self, needle: str, occurrence: int | None, every: bool
    ) -> list[etree._Element]:
        matches = self.find(needle, occurrence, every)
        return list(dict.fromkeys(m[0] for m in matches))

    @staticmethod
    def _mark_paragraph(paragraph: etree._Element, mark: etree._Element) -> None:
        ppr = paragraph.find(w("pPr"))
        if ppr is None:
            ppr = _el("pPr")
            paragraph.insert(0, ppr)
        rpr = ppr.find(w("rPr"))
        if rpr is None:
            rpr = _el("rPr")
            anchor = None
            for child in ppr:
                if local(child.tag) in ("sectPr", "pPrChange"):
                    anchor = child
                    break
            if anchor is not None:
                anchor.addprevious(rpr)
            else:
                ppr.append(rpr)
        rpr.insert(0, mark)

    def insert_paragraph(
        self, anchor: str, text: str, before=False, style=None, occurrence=None, comment=None
    ) -> int:
        paragraph = self._paragraph_matches(anchor, occurrence, False)[0]
        new = _el("p")
        old_ppr = paragraph.find(w("pPr"))
        if old_ppr is not None:
            ppr = copy.deepcopy(old_ppr)
            for child in list(ppr):
                if local(child.tag) in ("sectPr", "pPrChange", "rPr"):
                    ppr.remove(child)
            new.append(ppr)
        if style:
            style_id = self.styles.id_for_name(style)
            if style_id is None:
                raise ReviewError(f"style not found: {style!r}")
            ppr = new.find(w("pPr"))
            if ppr is None:
                ppr = _el("pPr")
                new.append(ppr)
            for old in ppr.findall(w("pStyle")):
                ppr.remove(old)
            ppr.insert(0, _el("pStyle", val=style_id))
        self._mark_paragraph(new, self.revision("ins"))
        runs = self.visible_runs(paragraph)
        rpr = None if style or not runs else runs[0].find(w("rPr"))
        insertion = self.inserted_run(text, rpr)
        new.append(insertion)
        if before:
            paragraph.addprevious(new)
        else:
            paragraph.addnext(new)
        if comment:
            self.add_comment(insertion, insertion, comment)
        return 1

    def delete_paragraph(self, find: str, occurrence=None, every=False, comment=None) -> int:
        paragraphs = self._paragraph_matches(find, occurrence, every)
        for paragraph in paragraphs:
            runs = [r for r in self.visible_runs(paragraph) if any(c.tag != w("rPr") for c in r)]
            self.check_editable(runs, paragraph)
            deletions = self.mark_deleted(runs) if runs else []
            self._mark_paragraph(paragraph, self.revision("del"))
            if comment and deletions:
                self.add_comment(deletions[0], deletions[-1], comment)
        return len(paragraphs)

    def comment(self, find: str, text: str, occurrence=None, every=False) -> int:
        targets = self._targets(find, occurrence, every)
        for paragraph, start, end in targets:
            runs = self.isolate(paragraph, start, end)
            self.add_comment(runs[0], runs[-1], text)
        return len(targets)


# ---------------------------------------------------------------- accept / reject


def _unwrap(node: etree._Element) -> None:
    parent = node.getparent()
    if parent is None:
        return
    for child in list(node):
        node.addprevious(child)
    parent.remove(node)


def _remove(node: etree._Element) -> None:
    parent = node.getparent()
    if parent is not None:
        parent.remove(node)


def _content_level(node: etree._Element) -> bool:
    parent = node.getparent()
    return parent is not None and local(parent.tag) not in ("rPr", "trPr", "numPr", "tcPr")


def _has_content(paragraph: etree._Element) -> bool:
    for node in paragraph.iter():
        if not isinstance(node.tag, str) or node is paragraph:
            continue
        name = local(node.tag)
        if any(local(a.tag) == "pPr" for a in node.iterancestors() if a is not paragraph):
            continue
        if name == "t" and node.text:
            return True
        if name in (
            "drawing",
            "pict",
            "object",
            "tab",
            "br",
            "sym",
            "footnoteReference",
            "endnoteReference",
            "fldChar",
        ):
            return True
    return False


def _block_sibling(node: etree._Element, forward: bool) -> etree._Element | None:
    sibling = node.getnext() if forward else node.getprevious()
    while sibling is not None and (
        not isinstance(sibling.tag, str) or local(sibling.tag) in PASSIVE_SIBLINGS
    ):
        sibling = sibling.getnext() if forward else sibling.getprevious()
    return sibling


def _merge_with_next(paragraph: etree._Element) -> None:
    """Resolve a removed paragraph mark: join the paragraph with the one after it."""
    ppr = paragraph.find(w("pPr"))
    if ppr is not None and ppr.find(w("sectPr")) is not None:
        return  # keep section breaks
    following = _block_sibling(paragraph, forward=True)
    content = [child for child in paragraph if child.tag != w("pPr")]
    if following is not None and following.tag == w("p"):
        position = 1 if following.find(w("pPr")) is not None else 0
        for offset, child in enumerate(content):
            following.insert(position + offset, child)
        _remove(paragraph)
        return
    if _has_content(paragraph):
        return
    container = paragraph.getparent()
    siblings = [c for c in container if c.tag == w("p")] if container is not None else []
    previous = _block_sibling(paragraph, forward=False)
    after_table_at_end = (
        previous is not None
        and previous.tag == w("tbl")
        and (following is None or local(following.tag) == "sectPr")
    )
    if len(siblings) > 1 and not after_table_at_end:
        _remove(paragraph)


def _restore(
    parent: etree._Element,
    change: etree._Element,
    old_tag: str,
    keep_first: set[str],
    keep_last: set[str],
) -> None:
    """Replace ``parent``'s properties with the old ones recorded in ``change``.

    Children named in keep_first / keep_last are not part of the recorded
    change (revision marks, header references, nested sectPr) and stay in place.
    """
    old = change.find(w(old_tag))
    kept_first = [c for c in parent if local(c.tag) in keep_first]
    kept_last = [c for c in parent if local(c.tag) in keep_last]
    for child in list(parent):
        parent.remove(child)
    for child in kept_first:
        parent.append(child)
    if old is not None:
        for child in old:
            parent.append(copy.deepcopy(child))
    for child in kept_last:
        parent.append(child)


def resolve_revisions(root: etree._Element, accept: bool) -> int:
    """Accept or reject every revision in one WordprocessingML part; returns the count."""
    count = 0
    for node in list(root.iter()):
        if (
            isinstance(node.tag, str)
            and ns_of(node.tag) == W_NS
            and local(node.tag) in RANGE_MARKERS
        ):
            _remove(node)
    gone = ("del", "moveFrom") if accept else ("ins", "moveTo")
    kept = ("ins", "moveTo") if accept else ("del", "moveFrom")
    for node in list(root.iter(*(w(t) for t in gone))):
        if _content_level(node):
            _remove(node)
            count += 1
    for node in list(root.iter(*(w(t) for t in kept))):
        if _content_level(node):
            if not accept:
                for text in node.iter(w("delText")):
                    text.tag = w("t")
                for text in node.iter(w("delInstrText")):
                    text.tag = w("instrText")
            _unwrap(node)
            count += 1
    for marker in list(root.iter(w("ins"), w("del"))):
        if local(marker.getparent().tag) == "numPr":
            _remove(marker)
            count += 1
    for row in list(root.iter(w("tr"))):
        trpr = row.find(w("trPr"))
        if trpr is None:
            continue
        drop_row = trpr.find(w("del" if accept else "ins"))
        for tag in ("ins", "del"):
            for marker in trpr.findall(w(tag)):
                trpr.remove(marker)
                count += 1
        if drop_row is not None:
            _remove(row)
    for cell in list(root.iter(w("tc"))):
        tcpr = cell.find(w("tcPr"))
        if tcpr is None:
            continue
        drop_cell = tcpr.find(w("cellDel" if accept else "cellIns"))
        for tag in ("cellIns", "cellDel", "cellMerge"):
            for marker in tcpr.findall(w(tag)):
                tcpr.remove(marker)
                count += 1
        if drop_cell is not None:
            _remove(cell)
    for table in list(root.iter(w("tbl"))):
        if table.find(w("tr")) is None:
            parent = table.getparent()
            _remove(table)
            if parent is not None and local(parent.tag) == "tc" and parent.find(w("p")) is None:
                parent.append(_el("p"))
    property_changes = {
        "rPrChange": ("rPr", {"ins", "del", "moveFrom", "moveTo"}, set()),
        "pPrChange": ("pPr", set(), {"rPr", "sectPr"}),
        "sectPrChange": ("sectPr", {"headerReference", "footerReference"}, set()),
        "tblPrChange": ("tblPr", set(), set()),
        "trPrChange": ("trPr", set(), set()),
        "tcPrChange": ("tcPr", set(), set()),
        "tblGridChange": ("tblGrid", set(), set()),
    }
    for name, (old_tag, keep_first, keep_last) in property_changes.items():
        for change in list(root.iter(w(name))):
            parent = change.getparent()
            if parent is None:
                continue
            if accept:
                parent.remove(change)
            else:
                _restore(parent, change, old_tag, keep_first, keep_last)
            count += 1
    for change in list(root.iter(w("numberingChange"))):
        _remove(change)
        count += 1
    merges = []
    for paragraph in list(root.iter(w("p"))):
        rpr = paragraph.find(f"{w('pPr')}/{w('rPr')}")
        if rpr is None:
            continue
        joins = False
        for marker in list(rpr):
            name = local(marker.tag)
            if name not in ("ins", "del", "moveFrom", "moveTo"):
                continue
            rpr.remove(marker)
            count += 1
            removed_mark = name in (("del", "moveFrom") if accept else ("ins", "moveTo"))
            joins = joins or removed_mark
        if len(rpr) == 0:
            rpr.getparent().remove(rpr)
        if joins:
            merges.append(paragraph)
    for paragraph in merges:
        _merge_with_next(paragraph)
    return count


def resolve_all(package: Package, accept: bool) -> int:
    total = 0
    for name in package.names():
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = package.xml(name)
        if ns_of(root.tag) != W_NS:
            continue
        total += resolve_revisions(root, accept)
    return total


# ---------------------------------------------------------------- CLI


def run_op(reviewer: Reviewer | None, package: Package, op: dict) -> dict:
    kind = op.get("op")
    occurrence = op.get("occurrence")
    every = bool(op.get("all"))
    comment = op.get("comment")
    if kind == "accept-all":
        return {"op": kind, "resolved": resolve_all(package, accept=True)}
    if kind == "reject-all":
        return {"op": kind, "resolved": resolve_all(package, accept=False)}
    assert reviewer is not None
    if kind == "replace":
        count = reviewer.replace(
            _req(op, "find"), _req(op, "with", allow_empty=True), occurrence, every, comment
        )
    elif kind == "delete":
        count = reviewer.delete(_req(op, "find"), occurrence, every, comment)
    elif kind == "insert":
        count = reviewer.insert(
            _req(op, "anchor"), _req(op, "text"), bool(op.get("before")), occurrence, every, comment
        )
    elif kind == "insert-paragraph":
        count = reviewer.insert_paragraph(
            _req(op, "anchor"),
            _req(op, "text", allow_empty=True),
            bool(op.get("before")),
            op.get("style"),
            occurrence,
            comment,
        )
    elif kind == "delete-paragraph":
        count = reviewer.delete_paragraph(_req(op, "find"), occurrence, every, comment)
    elif kind == "comment":
        count = reviewer.comment(_req(op, "find"), _req(op, "text"), occurrence, every)
    else:
        raise ReviewError(f"unknown op: {kind!r}")
    return {"op": kind, "changed": count}


def _req(op: dict, key: str, allow_empty: bool = False) -> str:
    value = op.get(key)
    if value is None or (value == "" and not allow_empty):
        raise ReviewError(f"op {op.get('op')!r} needs {key!r}")
    return str(value)


def _check_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?", value):
        raise ReviewError("--date must look like 2026-10-04T09:30:00Z")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "replace",
            "delete",
            "insert",
            "insert-paragraph",
            "delete-paragraph",
            "comment",
            "accept-all",
            "reject-all",
            "apply",
        ],
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--find")
    parser.add_argument("--with", dest="with_text")
    parser.add_argument("--anchor")
    parser.add_argument("--text")
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--style")
    parser.add_argument("--comment")
    parser.add_argument("--occurrence", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--ops", type=Path)
    parser.add_argument("--author", default="Valuz")
    parser.add_argument("--initials")
    parser.add_argument("--date")
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve():
        parser.error("output must be a new path; keep the original for comparison")
    try:
        date = _check_date(args.date) if args.date else utc_now()
        initials = (
            args.initials or "".join(part[0] for part in args.author.split() if part)[:4].upper()
        )
        package = Package(args.input)
        if args.command == "apply":
            if args.ops is None:
                raise ReviewError("apply needs --ops FILE")
            ops = json.loads(args.ops.read_text(encoding="utf-8"))
            if not isinstance(ops, list):
                raise ReviewError("--ops must contain a JSON list")
        else:
            op = {
                "op": args.command,
                "occurrence": args.occurrence,
                "all": args.all,
                "comment": args.comment,
            }
            for key, value in (
                ("find", args.find),
                ("with", args.with_text),
                ("anchor", args.anchor),
                ("text", args.text),
                ("style", args.style),
                ("before", args.before),
            ):
                if value is not None:
                    op[key] = value
            ops = [op]
        results = []
        for op in ops:
            reviewer = None
            if op.get("op") not in ("accept-all", "reject-all"):
                author = op.get("author", args.author)
                reviewer = Reviewer(
                    package, author, op.get("date", date), op.get("initials", initials)
                )
            results.append(run_op(reviewer, package, op))
        package.save(args.output)
    except (ReviewError, OSError, KeyError, ValueError, etree.XMLSyntaxError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps({"output": str(args.output), "results": results}, ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
