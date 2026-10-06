#!/usr/bin/env python3
"""Content QA for a .pptx: leftover template text, empty placeholders, likely overflow and more.

    valuz-python content_qa.py deck.pptx            # readable report
    valuz-python content_qa.py deck.pptx --json     # JSON report
    valuz-python content_qa.py deck.pptx --strict   # warnings also fail

Checks (per slide; codes in brackets):
  errors
    [leftover-text]      template prompts or filler: "Click to add title", "Lorem ipsum",
                         "单击此处添加标题", "Your text here", ...
    [empty-placeholder]  a title/body/picture/... placeholder with nothing in it
                         (PowerPoint shows its prompt text in the editor)
    [overflow]           estimated text height clearly exceeds the box (>115%) or text with
                         wrapping off runs past the slide edge
    [off-slide]          a text shape or table extends past the slide edge
  warnings
    [placeholder-marker] TODO / TBD / XXX / {{name}} / [Company] / 待补充 ...
    [overflow]           text may not fit (100-115%), shrink-on-overflow relied on,
                         or a table taller than its frame
    [tiny-text]          text below --min-font points (default 10)
    [low-contrast]       text colour vs. the fill behind it under 4.5:1 (3:1 for large text)
    [overlap]            two text shapes overlap
    [dense]              more than --max-words words on one slide
    [off-slide]          a picture or graphic extends past the slide edge
    [empty-text]         a text box with no text

Text size is estimated from font size, box size, insets, line spacing and
average glyph widths (CJK characters count as one em). It is a heuristic:
always confirm flagged slides - and the deck as a whole - on rendered images.
Exit status: 0 clean (or warnings only), 1 errors found (or warnings with
--strict), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import colorsys
import io
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
EMU_PER_INCH = 914400
EMU_PER_PT = 12700
TOLERANCE = int(0.05 * EMU_PER_INCH)

LEFTOVER_PATTERNS = [
    r"click to (add|edit|insert)\b",
    r"click icon to add",
    r"lorem ipsum",
    r"dolor sit amet",
    r"consectetur adipiscing",
    r"\byour (text|title|subtitle|content|logo|name|company) here\b",
    r"\b(insert|add|enter) (your )?(text|title|subtitle|content|caption) here\b",
    r"\b(sample|placeholder|dummy) text\b",
    r"\btitle here\b",
    r"\bsubtitle here\b",
    r"单击此处",
    r"点击此处",
    r"单击(以)?添加",
    r"点击(以)?添加",
    r"在此处(添加|输入|键入)",
    r"此处(添加|输入|键入)",
    r"请(在此)?输入(标题|文字|文本|内容)",
    r"示例文(本|字)",
    r"占位(文本|文字|符)",
    r"添加(标题|副标题|文本|文字)",
]
MARKER_PATTERNS = [
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bTBC\b",
    r"\bFIXME\b",
    r"\bXXX+\b",
    r"\{\{[^}]*\}\}",
    r"\[(company|client|name|date|title|insert[^\]]*|placeholder)\]",
    r"<(insert|placeholder)[^>]*>",
    r"待补充",
    r"待定",
    r"待填写",
]
LEFTOVER_RE = re.compile("|".join(LEFTOVER_PATTERNS), re.IGNORECASE)
MARKER_RE = re.compile("|".join(MARKER_PATTERNS), re.IGNORECASE)

SKIP_EMPTY_TYPES = {"DATE", "FOOTER", "SLIDE_NUMBER", "HEADER"}
TITLE_TYPES = {"TITLE", "CENTER_TITLE", "VERTICAL_TITLE"}
BODY_TYPES = {"BODY", "SUBTITLE", "OBJECT", "VERTICAL_BODY", "VERTICAL_OBJECT"}
SCHEME_ALIASES = {"tx1": "dk1", "tx2": "dk2", "bg1": "lt1", "bg2": "lt2"}


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


@dataclass
class Box:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def intersection(self, other: Box) -> int:
        w = min(self.right, other.right) - max(self.left, other.left)
        h = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(0, w) * max(0, h)

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass
class Report:
    issues: list[dict] = field(default_factory=list)

    def add(self, slide: int, severity: str, code: str, shape: str, message: str) -> None:
        self.issues.append(
            {"slide": slide, "severity": severity, "code": code, "shape": shape, "message": message}
        )


def inches(emu: float) -> str:
    return f"{emu / EMU_PER_INCH:.2f}in"


# --------------------------------------------------------------------------- inheritance


def _lvl(list_style, level: int):
    if list_style is None:
        return None
    return list_style.find(f"{A}lvl{level + 1}pPr")


class Context:
    """Resolves inherited text properties, theme colours and backgrounds for one deck."""

    def __init__(self, prs):
        self.prs = prs
        self.default_style = prs.part._element.find(f"{P}defaultTextStyle")
        self._themes: dict[int, dict[str, str]] = {}
        self._fonts: dict[int, dict[str, str]] = {}

    # -- placeholder chain -------------------------------------------------------
    @staticmethod
    def ph_type(shape) -> str | None:
        if not shape.is_placeholder:
            return None
        try:
            return shape.placeholder_format.type.name
        except (AttributeError, ValueError):
            return "BODY"

    @staticmethod
    def bases(shape) -> list:
        """Layout placeholder, then master placeholder, that this placeholder inherits from."""
        chain = []
        current = shape
        for _ in range(2):
            try:
                current = current._base_placeholder
            except (AttributeError, KeyError, ValueError):
                current = None
            if current is None:
                break
            chain.append(current)
        return chain

    def list_styles(self, shape, slide) -> list:
        """lstStyle-like elements in priority order, after the shape's own."""
        styles = []
        if shape.is_placeholder:
            for base in self.bases(shape):
                tx = base._element.find(f"{P}txBody")
                if tx is not None:
                    styles.append(tx.find(f"{A}lstStyle"))
            ph = self.ph_type(shape)
            tx_styles = slide.slide_layout.slide_master._element.find(f"{P}txStyles")
            if tx_styles is not None:
                name = (
                    "titleStyle"
                    if ph in TITLE_TYPES
                    else "bodyStyle"
                    if ph in BODY_TYPES
                    else "otherStyle"
                )
                styles.append(tx_styles.find(f"{P}{name}"))
        else:
            styles.append(self.default_style)
        return [s for s in styles if s is not None]

    def body_pr_chain(self, shape) -> list:
        chain = []
        own = shape._element.find(f".//{A}bodyPr")
        if own is not None:
            chain.append(own)
        if shape.is_placeholder:
            for base in self.bases(shape):
                body = base._element.find(f".//{A}bodyPr")
                if body is not None:
                    chain.append(body)
        return chain

    # -- colours ---------------------------------------------------------------------
    def theme(self, slide) -> dict[str, str]:
        master = slide.slide_layout.slide_master
        key = id(master)
        if key not in self._themes:
            colors: dict[str, str] = {}
            try:
                theme = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
                scheme = theme.find(f".//{A}clrScheme")
                for child in scheme if scheme is not None else []:
                    name = child.tag.split("}")[1]
                    value = child.find(f"{A}srgbClr")
                    if value is not None:
                        colors[name] = value.get("val")
                    else:
                        system = child.find(f"{A}sysClr")
                        if system is not None and system.get("lastClr"):
                            colors[name] = system.get("lastClr")
            except (KeyError, ValueError, AttributeError):
                pass
            mapping = master._element.find(f"{P}clrMap")
            if mapping is not None:
                for alias in SCHEME_ALIASES:
                    target = mapping.get(alias)
                    if target and target in colors:
                        colors[alias] = colors[target]
            for alias, target in SCHEME_ALIASES.items():
                colors.setdefault(alias, colors.get(target, ""))
            self._themes[key] = colors
        return self._themes[key]

    def fonts(self, slide) -> dict[str, str]:
        """Theme Latin typefaces: {"major": ..., "minor": ...}."""
        master = slide.slide_layout.slide_master
        key = id(master)
        if key not in self._fonts:
            fonts: dict[str, str] = {}
            try:
                theme = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
                for kind in ("major", "minor"):
                    latin = theme.find(f".//{A}{kind}Font/{A}latin")
                    if latin is not None:
                        fonts[kind] = latin.get("typeface", "")
            except (KeyError, ValueError, AttributeError):
                pass
            self._fonts[key] = fonts
        return self._fonts[key]

    def color(self, element, slide) -> tuple[float, float, float] | None:
        """RGB (0-1) of the first colour child under element, or None when unknown."""
        if element is None:
            return None
        for child in element:
            tag = child.tag.split("}")[-1]
            if tag == "srgbClr":
                value = child.get("val", "")
            elif tag == "schemeClr":
                value = self.theme(slide).get(child.get("val", ""), "")
            elif tag == "sysClr":
                value = child.get("lastClr", "")
            else:
                continue
            if not re.fullmatch(r"[0-9A-Fa-f]{6}", value or ""):
                return None
            rgb = tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
            return _apply_modifiers(rgb, child)
        return None


def _apply_modifiers(rgb, node):
    mods = {c.tag.split("}")[-1]: int(c.get("val", "0")) / 100000 for c in node}
    if "lumMod" in mods or "lumOff" in mods:
        h, lum, s = colorsys.rgb_to_hls(*rgb)
        lum = min(1.0, max(0.0, lum * mods.get("lumMod", 1.0) + mods.get("lumOff", 0.0)))
        rgb = colorsys.hls_to_rgb(h, lum, s)
    if "tint" in mods:
        rgb = tuple(c + (1 - c) * (1 - mods["tint"]) for c in rgb)
    if "shade" in mods:
        rgb = tuple(c * mods["shade"] for c in rgb)
    return rgb


def luminance(rgb) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# --------------------------------------------------------------------------- text metrics


# Average glyph width relative to Arial/Helvetica; unknown fonts count as 1.0.
FONT_WIDTH = {
    "calibri": 0.88,
    "carlito": 0.88,
    "calibri light": 0.86,
    "aptos": 0.94,
    "segoe ui": 0.95,
    "trebuchet ms": 0.96,
    "gill sans": 0.9,
    "verdana": 1.13,
    "tahoma": 1.0,
    "georgia": 1.04,
    "times new roman": 0.9,
    "cambria": 0.93,
    "microsoft yahei": 1.05,
    "微软雅黑": 1.05,
}


def font_factor(typeface: str | None) -> float:
    return FONT_WIDTH.get((typeface or "").strip().lower(), 1.0)


def is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def char_em(ch: str) -> float:
    """Approximate advance width in em for an Arial-like face; wide (CJK) characters are 1 em."""
    if is_wide(ch):
        return 1.0
    if ch == " ":
        return 0.28
    if ch in "il.,;:'!|ftjI()[]":
        return 0.3
    if ch.isupper() or ch in "mwMW@%&":
        return 0.66
    return 0.53


def tokens(text: str) -> list[str]:
    """Break opportunities: words keep their trailing space; wide characters stand alone."""
    out: list[str] = []
    current = ""
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            if current:
                out.append(current)
                current = ""
            out.append(ch)
        elif ch == " ":
            current += ch
            out.append(current)
            current = ""
        else:
            current += ch
    if current:
        out.append(current)
    return out


@dataclass
class Para:
    runs: list[tuple[str, float, bool, float]]  # text, size pt, bold, font width factor
    level: int
    size: float  # size for an empty paragraph / max size
    line_spacing: tuple[str, float]  # ("pct", 1.0) or ("pts", 24)
    space_before: float  # points
    space_after: float
    indent_pt: float  # marL


def first_attr(elements, path: str, attr: str):
    for element in elements:
        if element is None:
            continue
        node = element.find(path) if path else element
        if node is not None and node.get(attr) is not None:
            return node.get(attr)
    return None


def first_node(elements, path: str):
    for element in elements:
        if element is None:
            continue
        node = element.find(path)
        if node is not None:
            return node
    return None


def spacing(node, size: float) -> float:
    if node is None:
        return 0.0
    pct = node.find(f"{A}spcPct")
    if pct is not None:
        return size * int(pct.get("val", "0")) / 100000
    pts = node.find(f"{A}spcPts")
    if pts is not None:
        return int(pts.get("val", "0")) / 100
    return 0.0


def paragraphs_of(tx_body, list_styles: list, fonts: dict[str, str] | None = None) -> list[Para]:
    """Effective per-paragraph metrics for a txBody, resolving inherited list styles.

    fonts maps "major"/"minor" to the theme's Latin typefaces (for +mj-lt / +mn-lt).
    """
    fonts = fonts or {}
    own_list = tx_body.find(f"{A}lstStyle")
    paras = []
    for p in tx_body.findall(f"{A}p"):
        p_pr = p.find(f"{A}pPr")
        level = int(p_pr.get("lvl", "0")) if p_pr is not None else 0
        levels = [p_pr] + [_lvl(style, level) for style in [own_list, *list_styles]]
        default_size = first_attr(levels, f"{A}defRPr", "sz")
        default_size = int(default_size) / 100 if default_size else 18.0
        default_bold = first_attr(levels, f"{A}defRPr", "b") in ("1", "true")
        default_face = first_attr(levels, f"{A}defRPr/{A}latin", "typeface")
        runs = []
        for r in p:
            tag = r.tag.split("}")[-1]
            if tag not in ("r", "fld"):
                if tag == "br":
                    runs.append(("\n", default_size, default_bold, 1.0))
                continue
            r_pr = r.find(f"{A}rPr")
            size = (
                int(r_pr.get("sz")) / 100 if r_pr is not None and r_pr.get("sz") else default_size
            )
            bold = (
                (r_pr.get("b") in ("1", "true"))
                if r_pr is not None and r_pr.get("b")
                else default_bold
            )
            latin = r_pr.find(f"{A}latin") if r_pr is not None else None
            face = latin.get("typeface") if latin is not None else default_face
            if face is None or face.startswith("+mn"):
                face = fonts.get("minor")
            elif face.startswith("+mj"):
                face = fonts.get("major")
            runs.append((r.findtext(f"{A}t") or "", size, bold, font_factor(face)))
        end = p.find(f"{A}endParaRPr")
        empty_size = int(end.get("sz")) / 100 if end is not None and end.get("sz") else default_size
        size = max([s for t, s, _, _ in runs if t.strip()] or [empty_size])
        ln = first_node(levels, f"{A}lnSpc")
        if ln is not None and ln.find(f"{A}spcPts") is not None:
            line_spacing = ("pts", int(ln.find(f"{A}spcPts").get("val", "0")) / 100)
        elif ln is not None and ln.find(f"{A}spcPct") is not None:
            line_spacing = ("pct", int(ln.find(f"{A}spcPct").get("val", "100000")) / 100000)
        else:
            line_spacing = ("pct", 1.0)
        mar = first_attr(levels, "", "marL")
        paras.append(
            Para(
                runs=runs,
                level=level,
                size=size,
                line_spacing=line_spacing,
                space_before=spacing(first_node(levels, f"{A}spcBef"), size),
                space_after=spacing(first_node(levels, f"{A}spcAft"), size),
                indent_pt=int(mar) / EMU_PER_PT if mar else 0.0,
            )
        )
    return paras


def measure(
    paras: list[Para], width_pt: float, wrap: bool, scale: float = 1.0, ln_reduction: float = 0.0
):
    """(height in pt, widest line in pt) of the paragraphs laid out in width_pt."""
    total = 0.0
    widest = 0.0
    for para in paras:
        avail = max(1.0, width_pt - para.indent_pt)
        lines = 1
        line_w = 0.0
        for text, size, bold, face_factor in para.runs:
            size *= scale
            weight = 1.06 if bold else 1.0
            for token in tokens(text):
                if token == "\n":
                    widest = max(widest, line_w)
                    lines += 1
                    line_w = 0.0
                    continue
                w = (
                    sum(char_em(ch) * (1.0 if is_wide(ch) else face_factor) for ch in token)
                    * size
                    * weight
                )
                trailing = (
                    char_em(" ") * face_factor * size * weight if token.endswith(" ") else 0.0
                )
                if wrap and line_w > 0 and line_w + w - trailing > avail:
                    widest = max(widest, line_w)
                    lines += 1
                    line_w = 0.0
                if wrap and w > avail:  # a single token longer than the line
                    lines += int(w // avail)
                    w = w % avail
                line_w += w
        widest = max(widest, line_w)
        size = para.size * scale
        if para.line_spacing[0] == "pts":
            line_h = para.line_spacing[1]
        else:
            line_h = size * 1.2 * max(0.5, para.line_spacing[1] - ln_reduction)
        total += lines * line_h + (para.space_before + para.space_after) * scale
    return total, widest


# --------------------------------------------------------------------------- shape walk


def walk(shapes, transform=None):
    """Yield (shape, absolute Box) including group members."""
    for shape in shapes:
        left, top = shape.left or 0, shape.top or 0
        width, height = shape.width or 0, shape.height or 0
        if transform:
            left, top, width, height = transform(left, top, width, height)
        box = Box(int(left), int(top), int(width), int(height))
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            xfrm = shape._element.find(f"{P}grpSpPr/{A}xfrm")
            ch_off = xfrm.find(f"{A}chOff") if xfrm is not None else None
            ch_ext = xfrm.find(f"{A}chExt") if xfrm is not None else None
            if (
                ch_off is not None
                and ch_ext is not None
                and int(ch_ext.get("cx", 0))
                and int(ch_ext.get("cy", 0))
            ):
                cx0, cy0 = int(ch_off.get("x")), int(ch_off.get("y"))
                sx = box.width / int(ch_ext.get("cx"))
                sy = box.height / int(ch_ext.get("cy"))

                def child_transform(x, y, w, h, box=box, cx0=cx0, cy0=cy0, sx=sx, sy=sy):
                    return box.left + (x - cx0) * sx, box.top + (y - cy0) * sy, w * sx, h * sy
            else:

                def child_transform(x, y, w, h):
                    return x, y, w, h

            yield shape, box
            yield from walk(shape.shapes, child_transform)
        else:
            yield shape, box


def insets(body_chain) -> tuple[int, int, int, int]:
    defaults = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}
    return tuple(
        int(first_attr(body_chain, "", key) or default) for key, default in defaults.items()
    )


def autofit_mode(body_chain) -> tuple[str, float, float]:
    for body in body_chain:
        if body.find(f"{A}spAutoFit") is not None:
            return "shape", 1.0, 0.0
        norm = body.find(f"{A}normAutofit")
        if norm is not None:
            scale = int(norm.get("fontScale", "100000")) / 100000
            reduction = int(norm.get("lnSpcReduction", "0")) / 100000
            return "shrink", scale, reduction
        if body.find(f"{A}noAutofit") is not None:
            return "none", 1.0, 0.0
    return "none", 1.0, 0.0


def word_count(text: str) -> float:
    wide = sum(1 for ch in text if unicodedata.east_asian_width(ch) in ("W", "F"))
    latin = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-.%]*", text))
    return latin + wide / 2


def background_color(ctx: Context, slide):
    for source in (slide, slide.slide_layout, slide.slide_layout.slide_master):
        bg = source._element.find(f"{P}cSld/{P}bg")
        if bg is None:
            continue
        pr = bg.find(f"{P}bgPr")
        if pr is not None:
            fill = pr.find(f"{A}solidFill")
            return ctx.color(fill, slide) if fill is not None else None
        ref = bg.find(f"{P}bgRef")
        if ref is not None:
            return ctx.color(ref, slide)
        return None
    return (1.0, 1.0, 1.0)


def shape_fill(ctx: Context, shape, slide):
    """('solid', rgb) / ('none', None) / ('unknown', None) for the shape's own fill."""
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return "unknown", None
    sp_pr = shape._element.find(f"{P}spPr")
    if sp_pr is not None:
        if sp_pr.find(f"{A}noFill") is not None:
            return "none", None
        solid = sp_pr.find(f"{A}solidFill")
        if solid is not None:
            color = ctx.color(solid, slide)
            return ("solid", color) if color else ("unknown", None)
        if (
            sp_pr.find(f"{A}gradFill") is not None
            or sp_pr.find(f"{A}blipFill") is not None
            or sp_pr.find(f"{A}pattFill") is not None
        ):
            return "unknown", None
    ref = shape._element.find(f"{P}style/{A}fillRef")
    if ref is not None and ref.get("idx", "0") != "0":
        color = ctx.color(ref, slide)
        return ("solid", color) if color else ("unknown", None)
    return "none", None


def text_color(ctx: Context, shape, slide, run_pr, levels) -> tuple | None:
    fill = run_pr.find(f"{A}solidFill") if run_pr is not None else None
    if fill is None:
        fill = first_node(levels, f"{A}defRPr/{A}solidFill")
    if fill is None:
        font_ref = shape._element.find(f"{P}style/{A}fontRef")
        if font_ref is not None:
            return ctx.color(font_ref, slide)
        return ctx.color(_scheme("tx1"), slide)
    return ctx.color(fill, slide)


def _scheme(name: str):
    holder = etree.Element(f"{A}solidFill")
    etree.SubElement(holder, f"{A}schemeClr", val=name)
    return holder


# --------------------------------------------------------------------------- checks


def check_slide(ctx: Context, slide, number: int, report: Report, args) -> None:
    slide_w, slide_h = ctx.prs.slide_width, ctx.prs.slide_height
    entries = list(walk(slide.shapes))
    text_boxes: list[tuple[str, Box]] = []
    words = 0.0
    background = background_color(ctx, slide)

    for order, (shape, box) in enumerate(entries):
        name = shape.name
        is_group = shape.shape_type == MSO_SHAPE_TYPE.GROUP
        ph = ctx.ph_type(shape)
        has_text_frame = getattr(shape, "has_text_frame", False) and shape.has_text_frame
        is_table = getattr(shape, "has_table", False) and shape.has_table
        text = shape.text_frame.text if has_text_frame else ""
        if is_table:
            text = "\n".join(cell.text for row in shape.table.rows for cell in row.cells)
        words += word_count(text)

        # leftover template text and markers
        for match in LEFTOVER_RE.finditer(text):
            report.add(
                number, "error", "leftover-text", name, f"template/sample text: {match.group(0)!r}"
            )
        for match in MARKER_RE.finditer(text):
            report.add(
                number,
                "warning",
                "placeholder-marker",
                name,
                f"unfinished marker: {match.group(0)!r}",
            )

        # empty placeholders and text boxes
        if ph and ph not in SKIP_EMPTY_TYPES and has_text_frame and not text.strip():
            report.add(
                number,
                "error",
                "empty-placeholder",
                name,
                f"empty {ph.lower()} placeholder: fill it or delete it",
            )
        elif (
            not ph
            and has_text_frame
            and not text.strip()
            and shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX
        ):
            report.add(number, "warning", "empty-text", name, "text box has no text")

        # off-slide
        if not is_group and box.width and box.height:
            past = (
                box.left < -TOLERANCE
                or box.top < -TOLERANCE
                or box.right > slide_w + TOLERANCE
                or box.bottom > slide_h + TOLERANCE
            )
            if past:
                severity = "error" if (text.strip() or is_table) else "warning"
                report.add(
                    number,
                    severity,
                    "off-slide",
                    name,
                    f"extends past the slide edge (box {inches(box.left)},{inches(box.top)} "
                    f"{inches(box.width)}x{inches(box.height)} on a "
                    f"{inches(slide_w)}x{inches(slide_h)} slide)",
                )

        if is_table:
            check_table(ctx, shape, box, slide, number, report, args)
            text_boxes.append((name, box))
            continue
        if not has_text_frame or not text.strip():
            continue
        text_boxes.append((name, box))
        check_text_shape(ctx, shape, box, slide, number, report, args, entries[:order], background)

    # overlapping text shapes
    for i, (name_a, a) in enumerate(text_boxes):
        for name_b, b in text_boxes[i + 1 :]:
            smaller = min(a.width * a.height, b.width * b.height)
            if smaller and a.intersection(b) > 0.15 * smaller:
                report.add(
                    number,
                    "warning",
                    "overlap",
                    f"{name_a} / {name_b}",
                    "text shapes overlap; check the render",
                )

    if words > args.max_words:
        report.add(
            number,
            "warning",
            "dense",
            "",
            f"about {int(words)} words on one slide (limit {args.max_words}); split or cut",
        )


def check_text_shape(ctx, shape, box, slide, number, report, args, below, background) -> None:
    name = shape.name
    tx_body = shape._element.find(f".//{P}txBody")
    if tx_body is None:
        tx_body = shape._element.find(f".//{A}txBody")
    if tx_body is None:
        return
    styles = ctx.list_styles(shape, slide)
    body_chain = ctx.body_pr_chain(shape)
    if first_attr(body_chain, "", "vert") not in (None, "horz"):
        return
    l_ins, r_ins, t_ins, b_ins = insets(body_chain)
    wrap = (first_attr(body_chain, "", "wrap") or "square") != "none"
    mode, scale, reduction = autofit_mode(body_chain)
    paras = paragraphs_of(tx_body, styles, ctx.fonts(slide))
    width_pt = max(1.0, (box.width - l_ins - r_ins) / EMU_PER_PT)
    avail_pt = max(1.0, (box.height - t_ins - b_ins) / EMU_PER_PT)
    height_pt, widest_pt = measure(paras, width_pt, wrap, scale, reduction)

    # tiny text
    sizes = [run[1] * scale for para in paras for run in para.runs if run[0].strip()]
    if sizes and min(sizes) < args.min_font:
        report.add(
            number,
            "warning",
            "tiny-text",
            name,
            f"text at {min(sizes):.1f} pt (minimum {args.min_font} pt)",
        )

    # overflow
    if not wrap and box.left + l_ins + widest_pt * EMU_PER_PT > ctx.prs.slide_width + TOLERANCE:
        report.add(
            number,
            "error",
            "overflow",
            name,
            "word wrap is off and the text runs past the right edge of the slide",
        )
    elif mode == "shape":
        grown = box.top + t_ins + b_ins + height_pt * EMU_PER_PT
        if grown > ctx.prs.slide_height + TOLERANCE:
            report.add(
                number,
                "error",
                "overflow",
                name,
                f"box grows to fit its text and would end at {inches(grown)}, "
                "below the slide bottom",
            )
    else:
        ratio = height_pt / avail_pt
        detail = (
            f"estimated text height {height_pt / 72:.2f}in vs box {avail_pt / 72:.2f}in "
            f"({ratio:.0%})"
        )
        if mode == "shrink" and ratio > 1.0:
            report.add(
                number,
                "warning",
                "overflow",
                name,
                f"{detail}; relies on shrink-on-overflow, which PowerPoint only applies "
                "after the text is edited",
            )
        elif ratio > 1.15:
            report.add(number, "error", "overflow", name, f"{detail}: text likely overflows")
        elif ratio > 1.0:
            report.add(number, "warning", "overflow", name, f"{detail}: text may not fit")

    # contrast
    check_contrast(ctx, shape, box, slide, number, report, below, background, tx_body, styles)


def check_contrast(
    ctx, shape, box, slide, number, report, below, background, tx_body, styles
) -> None:
    kind, behind = shape_fill(ctx, shape, slide)
    if kind == "unknown":
        return
    if kind == "none":
        behind = background
        cx, cy = box.left + box.width // 2, box.top + box.height // 2
        for other, other_box in reversed(below):
            if other.shape_type == MSO_SHAPE_TYPE.GROUP or not other_box.contains_point(cx, cy):
                continue
            other_kind, other_color = shape_fill(ctx, other, slide)
            if other_kind == "none":
                continue
            behind = other_color  # None when unknown (picture, gradient)
            break
    if behind is None:
        return
    own_list = tx_body.find(f"{A}lstStyle")
    worst = None
    for p in tx_body.findall(f"{A}p"):
        p_pr = p.find(f"{A}pPr")
        level = int(p_pr.get("lvl", "0")) if p_pr is not None else 0
        levels = [p_pr] + [_lvl(style, level) for style in [own_list, *styles]]
        default_size = first_attr(levels, f"{A}defRPr", "sz")
        for r in p.findall(f"{A}r"):
            if not (r.findtext(f"{A}t") or "").strip():
                continue
            r_pr = r.find(f"{A}rPr")
            color = text_color(ctx, shape, slide, r_pr, levels)
            if color is None:
                continue
            size = (
                int(r_pr.get("sz")) / 100
                if r_pr is not None and r_pr.get("sz")
                else int(default_size or 1800) / 100
            )
            bold = r_pr is not None and r_pr.get("b") in ("1", "true")
            needed = 3.0 if size >= 18 or (bold and size >= 14) else 4.5
            ratio = contrast(color, behind)
            if ratio < needed and (worst is None or ratio < worst[0]):
                worst = (ratio, needed)
    if worst:
        report.add(
            number,
            "warning",
            "low-contrast",
            shape.name,
            f"text contrast {worst[0]:.1f}:1 (needs {worst[1]:.1f}:1)",
        )


def check_table(ctx, shape, box, slide, number, report, args) -> None:
    table = shape.table
    widths = [col.width for col in table.columns]
    total = 0
    smallest = None
    for row in table.rows:
        row_height = row.height
        for index, cell in enumerate(row.cells):
            if cell.is_spanned:
                continue
            span = cell.span_width if cell.is_merge_origin else 1
            width = sum(widths[index : index + span])
            tc_pr = cell._tc.tcPr
            mar = {
                key: int(tc_pr.get(key)) if tc_pr is not None and tc_pr.get(key) else default
                for key, default in (
                    ("marL", 91440),
                    ("marR", 91440),
                    ("marT", 45720),
                    ("marB", 45720),
                )
            }
            paras = paragraphs_of(
                cell._tc.txBody,
                [ctx.default_style] if ctx.default_style is not None else [],
                ctx.fonts(slide),
            )
            sizes = [run[1] for para in paras for run in para.runs if run[0].strip()]
            if sizes:
                smallest = min(sizes) if smallest is None else min(smallest, *sizes)
            height_pt, _ = measure(
                paras, max(1.0, (width - mar["marL"] - mar["marR"]) / EMU_PER_PT), True
            )
            row_height = max(row_height, int(height_pt * EMU_PER_PT) + mar["marT"] + mar["marB"])
        total += row_height
    if smallest is not None and smallest < args.min_font:
        report.add(
            number,
            "warning",
            "tiny-text",
            shape.name,
            f"table text at {smallest:.1f} pt (minimum {args.min_font} pt)",
        )
    bottom = box.top + total
    if bottom > ctx.prs.slide_height + TOLERANCE:
        report.add(
            number,
            "error",
            "overflow",
            shape.name,
            "rows grow to fit their text; the table would end at about "
            f"{inches(bottom)}, below the slide bottom",
        )
    elif total > box.height * 1.15:
        report.add(
            number,
            "warning",
            "overflow",
            shape.name,
            f"table needs about {inches(total)} but its frame is {inches(box.height)}; "
            "check what sits below it",
        )


def run_checks(path: Path, args) -> dict:
    prs = open_deck(path)
    ctx = Context(prs)
    report = Report()
    for number, slide in enumerate(prs.slides, 1):
        check_slide(ctx, slide, number, report, args)
    errors = sum(1 for issue in report.issues if issue["severity"] == "error")
    warnings = len(report.issues) - errors
    return {
        "file": str(path),
        "slides": len(prs.slides),
        "errors": errors,
        "warnings": warnings,
        "issues": report.issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument(
        "--min-font", type=float, default=10.0, help="smallest acceptable font size in points"
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=120,
        help="word budget per slide (CJK: 2 characters = 1 word)",
    )
    args = parser.parse_args(argv)
    if args.input.suffix.lower() not in (".pptx", ".potx"):
        parser.error("input must be a .pptx or .potx file")
    result = run_checks(args.input, args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{result['file']}: {result['slides']} slides, "
            f"{result['errors']} errors, {result['warnings']} warnings"
        )
        for issue in result["issues"]:
            shape = f' "{issue["shape"]}"' if issue["shape"] else ""
            print(
                f"slide {issue['slide']} [{issue['severity']}] {issue['code']}{shape}: "
                f"{issue['message']}"
            )
    failed = result["errors"] > 0 or (args.strict and result["warnings"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
