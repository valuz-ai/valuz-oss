"""Text and structure model of a WordprocessingML package.

Shared by docx_inspect.py and docx_toc.py; not a command by itself. Resolves
styles (by name, so localized style ids such as "1" or "a" work), heading
levels, list labels, and the visible text of paragraphs including fields,
hyperlinks and tracked changes.
"""

from __future__ import annotations

import re

from lxml import etree
from ooxml_package import (
    MC_NS,
    R_NS,
    REL_HYPERLINK,
    REL_NUMBERING,
    REL_STYLES,
    W_NS,
    WP_NS,
    Package,
    local,
    ns_of,
    w,
)

PAGE_FIELDS = {"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION"}
HEADING_NAME = re.compile(r"^heading\s+([1-9])$", re.IGNORECASE)

# Containers whose runs are part of the paragraph's own text.
SKIP_SUBTREES = {"pPr", "rPr", "txbxContent", "sectPr", "tblPr", "trPr", "tcPr"}


def _to_roman(number: int) -> str:
    values = [
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ]
    out = ""
    for value, letters in values:
        while number >= value:
            out += letters
            number -= value
    return out or "0"


def _to_letters(number: int) -> str:
    if number <= 0:
        return "0"
    letter = chr(ord("a") + (number - 1) % 26)
    return letter * ((number - 1) // 26 + 1)


def format_number(number: int, fmt: str) -> str:
    if fmt in ("lowerLetter",):
        return _to_letters(number)
    if fmt in ("upperLetter",):
        return _to_letters(number).upper()
    if fmt in ("lowerRoman",):
        return _to_roman(number)
    if fmt in ("upperRoman",):
        return _to_roman(number).upper()
    if fmt in ("decimalZero",):
        return f"{number:02d}"
    if fmt == "none":
        return ""
    return str(number)


class StyleSheet:
    """Paragraph style lookup: name, outline level, list numbering, inheritance."""

    def __init__(self, root: etree._Element | None):
        self.styles: dict[str, etree._Element] = {}
        self.default_paragraph: str | None = None
        if root is None:
            return
        for style in root.iter(w("style")):
            style_id = style.get(w("styleId"))
            if style_id is None:
                continue
            self.styles[style_id] = style
            if style.get(w("type")) == "paragraph" and style.get(w("default")) in (
                "1",
                "true",
                "on",
            ):
                self.default_paragraph = style_id

    def name(self, style_id: str | None) -> str:
        style = self.styles.get(style_id or "")
        if style is None:
            return style_id or ""
        node = style.find(w("name"))
        return node.get(w("val"), style_id) if node is not None else (style_id or "")

    def id_for_name(self, name: str) -> str | None:
        wanted = name.strip().lower()
        for style_id, style in self.styles.items():
            node = style.find(w("name"))
            if node is not None and node.get(w("val"), "").lower() == wanted:
                return style_id
        return name if name in self.styles else None

    def _chain(self, style_id: str | None):
        seen = set()
        while style_id and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            yield style
            based = style.find(w("basedOn"))
            style_id = based.get(w("val")) if based is not None else None

    def outline_level(self, style_id: str | None) -> int | None:
        """0-based outline level from the style chain, or None for body text."""
        for style in self._chain(style_id):
            node = style.find(f"{w('pPr')}/{w('outlineLvl')}")
            if node is not None:
                value = int(node.get(w("val"), "9"))
                return None if value >= 9 else value
            match = HEADING_NAME.match(self.name(style.get(w("styleId"))))
            if match:
                return int(match.group(1)) - 1
        return None

    def num_pr(self, style_id: str | None) -> tuple[str | None, int | None]:
        for style in self._chain(style_id):
            node = style.find(f"{w('pPr')}/{w('numPr')}")
            if node is not None:
                num = node.find(w("numId"))
                lvl = node.find(w("ilvl"))
                return (
                    num.get(w("val")) if num is not None else None,
                    int(lvl.get(w("val"), "0")) if lvl is not None else None,
                )
        return None, None


class Numbering:
    """List definitions and running counters for list labels."""

    def __init__(self, root: etree._Element | None):
        self.levels: dict[str, dict[int, dict[str, object]]] = {}
        self.counters: dict[str, list[int]] = {}
        if root is None:
            return
        abstract: dict[str, dict[int, dict[str, object]]] = {}
        for node in root.findall(w("abstractNum")):
            levels = {}
            for lvl in node.findall(w("lvl")):
                levels[int(lvl.get(w("ilvl"), "0"))] = {
                    "start": int(_val(lvl, "start", "1")),
                    "fmt": _val(lvl, "numFmt", "decimal"),
                    "text": _val(lvl, "lvlText", ""),
                }
            abstract[node.get(w("abstractNumId"), "")] = levels
        for num in root.findall(w("num")):
            ref = num.find(w("abstractNumId"))
            levels = {
                k: dict(v)
                for k, v in abstract.get(ref.get(w("val")) if ref is not None else "", {}).items()
            }
            for override in num.findall(w("lvlOverride")):
                ilvl = int(override.get(w("ilvl"), "0"))
                start = override.find(w("startOverride"))
                if start is not None and ilvl in levels:
                    levels[ilvl]["start"] = int(start.get(w("val"), "1"))
            self.levels[num.get(w("numId"), "")] = levels

    def is_bullet(self, num_id: str, ilvl: int) -> bool:
        level = self.levels.get(num_id, {}).get(ilvl)
        return level is None or level["fmt"] == "bullet"

    def label(self, num_id: str, ilvl: int) -> str:
        """Advance the counter for (num_id, ilvl) and return the rendered label."""
        levels = self.levels.get(num_id)
        if not levels or ilvl not in levels:
            return "-"
        counters = self.counters.setdefault(num_id, [0] * 9)
        for deeper in range(ilvl + 1, 9):
            counters[deeper] = 0
        for shallower in range(ilvl):
            if counters[shallower] == 0 and shallower in levels:
                counters[shallower] = int(levels[shallower]["start"])  # type: ignore[arg-type]
        counters[ilvl] = counters[ilvl] + 1 if counters[ilvl] else int(levels[ilvl]["start"])  # type: ignore[arg-type]
        level = levels[ilvl]
        if level["fmt"] == "bullet":
            return "-"

        def substitute(match: re.Match[str]) -> str:
            index = int(match.group(1)) - 1
            fmt = str(levels.get(index, {"fmt": "decimal"})["fmt"])
            return format_number(counters[index] or 1, fmt)

        return re.sub(r"%([1-9])", substitute, str(level["text"])) or str(counters[ilvl])


def _val(node: etree._Element, child: str, default: str) -> str:
    found = node.find(w(child))
    return found.get(w("val"), default) if found is not None else default


class _FieldState:
    def __init__(self) -> None:
        self.stack: list[dict[str, object]] = []

    def in_code(self) -> bool:
        return any(field["phase"] in ("code", "hidden") for field in self.stack)


class TextExtractor:
    """Visible text of paragraphs in one story part, with field state across paragraphs.

    view: "final" (tracked changes accepted), "original" (rejected), or
    "markup" (insertions as {+...+}, deletions as {-...-}).
    """

    def __init__(
        self,
        package: Package,
        part: str,
        view: str = "final",
        links: bool = True,
        notes: bool = True,
    ):
        self.view = view
        self.links = links
        self.notes = notes
        self.fields = _FieldState()
        self.hyperlinks = {
            rel_id: target
            for rel_id, (rel_type, target, _external) in package.rel_targets(part).items()
            if rel_type == REL_HYPERLINK
        }

    def paragraph(self, paragraph: etree._Element) -> str:
        out: list[str] = []
        self._walk(paragraph, out)
        return "".join(out)

    def _emit(self, out: list[str], text: str) -> None:
        if self.fields.in_code():
            return
        for field in self.fields.stack:
            if field["phase"] == "result" and text.strip():
                field["shown"] = True
        out.append(text)

    def _walk(self, element: etree._Element, out: list[str]) -> None:
        for child in element:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            name = local(tag)
            if ns_of(tag) == MC_NS and name == "Fallback":
                continue
            if ns_of(tag) not in (W_NS, MC_NS):
                continue
            if name in SKIP_SUBTREES:
                continue
            if name in ("del", "moveFrom"):
                if self.view == "final":
                    continue
                if self.view == "markup":
                    self._emit(out, "{-")
                    self._walk(child, out)
                    self._emit(out, "-}")
                else:
                    self._walk(child, out)
                continue
            if name in ("ins", "moveTo"):
                if self.view == "original":
                    continue
                if self.view == "markup":
                    self._emit(out, "{+")
                    self._walk(child, out)
                    self._emit(out, "+}")
                else:
                    self._walk(child, out)
                continue
            if name == "t":
                self._emit(out, child.text or "")
            elif name == "delText":
                self._emit(out, child.text or "")
            elif name == "tab":
                self._emit(out, "\t")
            elif name in ("br", "cr"):
                kind = child.get(w("type"))
                self._emit(out, "[page break]" if kind == "page" else "\n")
            elif name == "noBreakHyphen":
                self._emit(out, "-")
            elif name == "fldChar":
                self._field_char(child, out)
            elif name in ("instrText", "delInstrText"):
                if self.fields.stack:
                    self.fields.stack[-1]["code"] = str(self.fields.stack[-1]["code"]) + (
                        child.text or ""
                    )
            elif name == "fldSimple":
                inner: list[str] = []
                self._walk(child, inner)
                text = "".join(inner)
                self._emit(
                    out, text if text.strip() else "{" + child.get(w("instr"), "").strip() + "}"
                )
            elif name == "footnoteReference":
                if self.notes:
                    self._emit(out, f"[^{child.get(w('id'))}]")
            elif name == "endnoteReference":
                if self.notes:
                    self._emit(out, f"[^e{child.get(w('id'))}]")
            elif name == "hyperlink":
                inner = []
                self._walk(child, inner)
                text = "".join(inner)
                target = self.hyperlinks.get(child.get(f"{{{R_NS}}}id", ""))
                if self.links and target and text:
                    out.append(f"[{text}]({target})")
                else:
                    out.append(text)
            elif name in ("drawing", "pict", "object"):
                self._emit(out, self._image_label(child))
            else:
                self._walk(child, out)

    def _image_label(self, element: etree._Element) -> str:
        for doc_pr in element.iter(f"{{{WP_NS}}}docPr"):
            descr = doc_pr.get("descr") or doc_pr.get("title") or doc_pr.get("name") or ""
            return f"[image: {descr}]" if descr else "[image]"
        return "[image]"

    def _field_char(self, node: etree._Element, out: list[str]) -> None:
        kind = node.get(w("fldCharType"))
        stack = self.fields.stack
        if kind == "begin":
            stack.append({"code": "", "phase": "code", "shown": False})
        elif kind == "separate" and stack:
            first = str(stack[-1]["code"]).split()[:1]
            # Cached page counters are stale by nature; show the field code instead.
            stack[-1]["phase"] = "hidden" if first and first[0].upper() in PAGE_FIELDS else "result"
        elif kind == "end" and stack:
            field = stack.pop()
            code = " ".join(str(field["code"]).split())
            if (
                not field["shown"]
                and code
                and not code.upper().startswith(("PAGEREF", "HYPERLINK"))
            ):
                # An empty result means the field was never computed; show its code.
                self._emit(out, "{" + code + "}")


class DocumentModel:
    """Styles, numbering and helpers for one .docx package."""

    def __init__(self, package: Package):
        self.package = package
        self.main = package.main_part()
        styles = package.related(self.main, REL_STYLES)
        numbering = package.related(self.main, REL_NUMBERING)
        self.styles = StyleSheet(
            package.xml(styles[0]) if styles and package.has(styles[0]) else None
        )
        self.numbering = Numbering(
            package.xml(numbering[0]) if numbering and package.has(numbering[0]) else None
        )

    def paragraph_style(self, paragraph: etree._Element) -> str | None:
        node = paragraph.find(f"{w('pPr')}/{w('pStyle')}")
        if node is not None:
            return node.get(w("val"))
        return self.styles.default_paragraph

    def heading_level(self, paragraph: etree._Element) -> int | None:
        """1-based heading level (outline level + 1), or None for body text."""
        direct = paragraph.find(f"{w('pPr')}/{w('outlineLvl')}")
        if direct is not None:
            value = int(direct.get(w("val"), "9"))
            return None if value >= 9 else value + 1
        level = self.styles.outline_level(self.paragraph_style(paragraph))
        return None if level is None else level + 1

    def list_info(self, paragraph: etree._Element) -> tuple[str, int] | None:
        num_pr = paragraph.find(f"{w('pPr')}/{w('numPr')}")
        num_id: str | None = None
        ilvl: int | None = None
        if num_pr is not None:
            num = num_pr.find(w("numId"))
            lvl = num_pr.find(w("ilvl"))
            num_id = num.get(w("val")) if num is not None else None
            ilvl = int(lvl.get(w("val"), "0")) if lvl is not None else None
        if num_id is None:
            style_num, style_lvl = self.styles.num_pr(self.paragraph_style(paragraph))
            num_id = style_num
            ilvl = ilvl if ilvl is not None else style_lvl
        if num_id is None or num_id == "0":
            return None
        return num_id, ilvl or 0


def own_paragraph(element: etree._Element) -> etree._Element | None:
    """Nearest ancestor w:p of ``element``."""
    node = element.getparent()
    while node is not None and node.tag != w("p"):
        node = node.getparent()
    return node
