"""Helpers for building and filling decks with python-pptx.

Import from a script run with valuz-python:

    import sys
    sys.dont_write_bytecode = True  # the skill directory is read-only
    sys.path.insert(0, "<this skill's directory>/scripts")
    from pptx_helpers import *

All positions and sizes are in inches (floats); font sizes in points; colours
are "RRGGBB" hex strings. Every function returns the python-pptx object it
created so you can adjust it further with the normal python-pptx API.

Building blocks
  new_presentation(template=None)        16:9 deck; the default layouts are rescaled to 16:9
  open_presentation(path)                open a .pptx or a .potx template (save as .pptx)
  Theme / PALETTES / apply_theme(prs, t) palette + fonts written into the deck's theme
  blank_slide / content_slide            slide with no placeholders / with a title placeholder
  add_title, add_text, add_bullets       text boxes with explicit size, wrapping on, autofit off
  add_box                                flat rectangle / rounded rectangle (cards, bands)
  add_chart                              native chart with readable defaults
  add_table                              native table with header fill, row rules, number alignment
  add_image                              picture fitted ("contain") or cropped ("cover") into a box
  columns / rows                         split a box on the spacing grid
  set_background, set_notes
Template filling
  set_text(shape, content)               replace text, keep each paragraph's formatting
  replace_text(target, old, new)         run-aware find/replace in a deck, slide or shape
  replace_image(picture, path)           swap a picture's image, keep its frame (crop to fit)
  set_font(font, ...)                    size/colour/bold + Latin and East Asian typeface
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from dataclasses import dataclass

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

__all__ = [
    "PALETTES",
    "Theme",
    "add_bullets",
    "add_box",
    "add_chart",
    "add_image",
    "add_table",
    "add_text",
    "add_title",
    "apply_theme",
    "blank_slide",
    "columns",
    "content_slide",
    "new_presentation",
    "open_presentation",
    "replace_image",
    "replace_text",
    "rows",
    "set_background",
    "set_font",
    "set_notes",
    "set_text",
]

SLIDE_W, SLIDE_H = 13.333, 7.5
_ALIGN = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}


# --------------------------------------------------------------------------- theme


@dataclass(frozen=True)
class Theme:
    """Palette, fonts, type scale and spacing grid for one deck."""

    bg: str = "FFFFFF"  # slide background
    surface: str = "F1F4F9"  # card / band fill, zebra rows
    text: str = "1B2433"  # body text
    muted: str = "5B6575"  # captions, sources, axis labels
    primary: str = "1F3A5F"  # structure: header fills, key numbers, markers
    on_primary: str = "FFFFFF"  # text placed on primary
    highlight: str = "C2410C"  # emphasis for text or fills under white text (>= 4.5:1 vs bg)
    accents: tuple[str, ...] = (
        "1F3A5F",
        "3B7DD8",
        "8FB3E8",
        "D2601F",
        "6B7280",
        "A3B1C6",
    )  # chart series
    rule: str = "D5DBE3"  # hairlines, table rules, gridlines
    font: str = "Calibri"  # Latin typeface
    font_ea: str = "Microsoft YaHei"  # East Asian typeface (CJK text)
    title_size: int = 30
    subtitle_size: int = 18
    body_size: int = 18
    small_size: int = 12  # captions, table text, chart labels
    source_size: int = 10  # footnotes / sources
    margin: float = 0.6  # left/right/bottom page margin
    title_top: float = 0.45
    content_top: float = 1.55  # where content starts under a one-line title
    gap: float = 0.3  # gutter between columns / blocks

    def content_box(self, subtitle: bool = False) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the area under the title (and subtitle line) inside the margins."""
        top = self.content_top + (0.3 if subtitle else 0.0)
        return (self.margin, top, SLIDE_W - 2 * self.margin, SLIDE_H - top - self.margin)


PALETTES: dict[str, Theme] = {
    # corporate / finance: navy with a warm orange highlight
    "navy": Theme(),
    # health, science, calm tech
    "teal": Theme(
        surface="EEF6F5",
        text="17302E",
        muted="4F6A66",
        primary="0F766E",
        highlight="B45309",
        accents=("0F766E", "14B8A6", "99D5CC", "F59E0B", "64748B", "B7C9C6"),
        rule="D3E3E0",
    ),
    # minimal, editorial: charcoal with one orange accent
    "charcoal": Theme(
        surface="F3F3F1",
        text="1F1F1F",
        muted="616161",
        primary="2B2B2B",
        highlight="C2410C",
        accents=("E4572E", "2B2B2B", "8C8C8C", "F2A541", "4F6D7A", "C9C9C9"),
        rule="DDDDDA",
    ),
    # sustainability, agriculture, outdoor
    "forest": Theme(
        bg="FBFAF6",
        surface="EEF2E8",
        text="1E2A1F",
        muted="56634F",
        primary="2F5D3A",
        highlight="9A5B13",
        accents=("2F5D3A", "7BA05B", "C9D8A8", "D08C2E", "6B705C", "A9B49A"),
        rule="D9DFD0",
    ),
    # premium, legal, culture
    "burgundy": Theme(
        surface="F7F1F2",
        text="2A1E21",
        muted="6A5A5E",
        primary="7A1F35",
        highlight="3E5C76",
        accents=("7A1F35", "C0485F", "E8B4BE", "3E5C76", "9A8C98", "D9C7CB"),
        rule="E6D9DC",
    ),
    # Chinese corporate red with neutral greys
    "red": Theme(
        surface="F8F1F0",
        text="262626",
        muted="616161",
        primary="B91C1C",
        highlight="B91C1C",
        accents=("B91C1C", "404040", "D97757", "A3A3A3", "E5B567", "7F1D1D"),
        rule="E5DAD8",
    ),
    # dark background for keynote-style talks
    "midnight": Theme(
        bg="0F172A",
        surface="1E293B",
        text="F1F5F9",
        muted="A5B4C8",
        primary="38BDF8",
        on_primary="0F172A",
        highlight="FBBF24",
        accents=("38BDF8", "A78BFA", "34D399", "FBBF24", "F472B6", "94A3B8"),
        rule="334155",
    ),
}


def _theme_part(prs):
    return prs.slide_master.part.part_related_by(RT.THEME)


def apply_theme(prs, theme: Theme) -> None:
    """Write the palette and fonts into the deck theme.

    Native charts, tables and any text without explicit formatting then follow
    the deck's palette and fonts (accent1..6 = theme.accents, dk1 = text,
    lt1 = bg, Latin + East Asian theme fonts).
    """
    part = _theme_part(prs)
    root = etree.fromstring(part.blob)
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    scheme = root.find(f".//{a}clrScheme")
    dark_bg = _luminance(theme.bg) < 0.4
    slots = {
        "dk1": theme.bg if dark_bg else theme.text,
        "lt1": theme.text if dark_bg else theme.bg,
        "dk2": theme.primary,
        "lt2": theme.surface,
    }
    for index, color in enumerate(theme.accents[:6], 1):
        slots[f"accent{index}"] = color
    for name, color in slots.items():
        slot = scheme.find(f"{a}{name}")
        if slot is None:
            continue
        for child in list(slot):
            slot.remove(child)
        etree.SubElement(slot, f"{a}srgbClr", val=color)
    for kind in ("majorFont", "minorFont"):
        font = root.find(f".//{a}fontScheme/{a}{kind}")
        if font is None:
            continue
        font.find(f"{a}latin").set("typeface", theme.font)
        font.find(f"{a}ea").set("typeface", theme.font_ea)
        for script in font.findall(f"{a}font"):
            if script.get("script") in ("Hans", "Hant"):
                script.set("typeface", theme.font_ea)
    part.blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if dark_bg:
        # Text colour tx1 maps to dk1 by default; swap the master mapping for a dark deck.
        clr_map = prs.slide_master._element.find(qn("p:clrMap"))
        clr_map.set("bg1", "dk1")
        clr_map.set("tx1", "lt1")
        clr_map.set("bg2", "dk2")
        clr_map.set("tx2", "lt2")


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# --------------------------------------------------------------------------- deck and slides


def open_presentation(path: str):
    """Open a .pptx, or a .potx template (python-pptx alone rejects the template type).

    Save the result as .pptx.
    """
    if not str(path).lower().endswith(".potx"):
        return Presentation(path)
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


def new_presentation(template: str | None = None, theme: Theme | None = None):
    """A 16:9 (13.333 x 7.5 in) deck.

    Without a template, python-pptx's default 4:3 layouts are stretched to the
    new width so title/body placeholders sit correctly. With a template, its
    own size and layouts are kept. Pass theme to apply_theme() in one step.
    """
    prs = open_presentation(template) if template else Presentation()
    if template is None:
        old_width = prs.slide_width
        prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
        ratio = prs.slide_width / old_width
        masters = list(prs.slide_masters)
        for owner in masters + [layout for master in masters for layout in master.slide_layouts]:
            for xfrm in owner._element.iter(qn("a:xfrm")):
                off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
                if off is not None:
                    off.set("x", str(int(int(off.get("x")) * ratio)))
                if ext is not None:
                    ext.set("cx", str(int(int(ext.get("cx")) * ratio)))
    if theme is not None:
        apply_theme(prs, theme)
    return prs


def _layout(prs, name: str, fallback_placeholders: int):
    for layout in prs.slide_layouts:
        if layout.name.lower() == name.lower():
            return layout
    return min(
        prs.slide_layouts, key=lambda lay: abs(len(lay.placeholders) - fallback_placeholders)
    )


def blank_slide(prs, theme: Theme | None = None):
    """A slide with no content placeholders (layout "Blank" or the emptiest layout)."""
    slide = prs.slides.add_slide(_layout(prs, "Blank", 0))
    _drop_empty_placeholders(slide, keep_title=False)
    if theme is not None and theme.bg.upper() != "FFFFFF":
        set_background(slide, theme.bg)
    return slide


def content_slide(prs, title: str, theme: Theme, *, subtitle: str | None = None):
    """A slide on the "Title Only" layout with the title placed and styled per theme.

    Using the real title placeholder keeps slide titles in PowerPoint's outline,
    navigation and accessibility tree.
    """
    slide = prs.slides.add_slide(_layout(prs, "Title Only", 1))
    _drop_empty_placeholders(slide, keep_title=True)
    if theme.bg.upper() != "FFFFFF":
        set_background(slide, theme.bg)
    add_title(slide, title, theme, subtitle=subtitle)
    return slide


def _drop_empty_placeholders(slide, keep_title: bool) -> None:
    for shape in list(slide.placeholders):
        is_title = shape.placeholder_format.type in (
            PP_PLACEHOLDER.TITLE,
            PP_PLACEHOLDER.CENTER_TITLE,
        )
        if keep_title and is_title:
            continue
        shape._element.getparent().remove(shape._element)


def set_background(slide, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor.from_string(color)


def set_notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


# --------------------------------------------------------------------------- text


def set_font(
    font,
    *,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    color: str | None = None,
    name: str | None = None,
    ea: str | None = None,
) -> None:
    """Style a python-pptx Font (run.font, paragraph.font, chart.font ...).

    name sets the Latin typeface, ea the East Asian one (needed for CJK text to
    use a CJK font instead of a fallback).
    """
    if size is not None:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    if color is not None:
        font.color.rgb = RGBColor.from_string(color)
    if name is not None:
        font.name = name
    if ea is not None:
        r_pr = font._rPr if hasattr(font, "_rPr") else font._element
        existing = r_pr.find(qn("a:ea"))
        if existing is None:
            existing = etree.SubElement(r_pr, qn("a:ea"))
            latin = r_pr.find(qn("a:latin"))
            if latin is not None:
                latin.addnext(existing)
            else:
                successors = [
                    r_pr.find(qn(tag))
                    for tag in (
                        "a:cs",
                        "a:sym",
                        "a:hlinkClick",
                        "a:hlinkMouseOver",
                        "a:rtl",
                        "a:extLst",
                    )
                ]
                successors = [s for s in successors if s is not None]
                if successors:
                    successors[0].addprevious(existing)
        existing.set("typeface", ea)


def _prepare_frame(shape, *, margins=(0.05, 0.03, 0.05, 0.03), anchor="top", wrap=True):
    frame = shape.text_frame
    frame.word_wrap = wrap
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left, frame.margin_top, frame.margin_right, frame.margin_bottom = (
        Inches(m) for m in margins
    )
    frame.vertical_anchor = _ANCHOR[anchor]
    return frame


def _normalize(content) -> list[dict]:
    """str ("a\\nb"), or a list of str / (text, level) / dict(text, level, bold, color, size)."""
    if isinstance(content, str):
        return [{"text": line} for line in content.split("\n")]
    items = []
    for item in content:
        if isinstance(item, str):
            items.append({"text": item})
        elif isinstance(item, tuple):
            items.append({"text": item[0], "level": item[1]})
        else:
            items.append(dict(item))
    return items


def _fill_paragraphs(
    frame, items, *, size, color, bold, font, font_ea, align, line_spacing, space_after
):
    for index, item in enumerate(items):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = _ALIGN[item.get("align", align)]
        paragraph.level = item.get("level", 0)
        if line_spacing:
            paragraph.line_spacing = line_spacing
        if space_after is not None:
            paragraph.space_after = Pt(space_after)
        run = paragraph.add_run()
        run.text = item["text"]
        set_font(
            run.font,
            size=item.get("size", size),
            bold=item.get("bold", bold),
            italic=item.get("italic"),
            color=item.get("color", color),
            name=font,
            ea=font_ea,
        )
    return frame


def add_text(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    content,
    *,
    theme: Theme | None = None,
    size: float | None = None,
    color: str | None = None,
    bold: bool = False,
    align: str = "left",
    anchor: str = "top",
    line_spacing: float | None = 1.1,
    space_after: float | None = None,
    font: str | None = None,
    font_ea: str | None = None,
    margins=(0.05, 0.03, 0.05, 0.03),
):
    """A text box of a fixed size with wrapping on and autofit off.

    content: "line\\nline", or a list of str / dict(text, level, bold, color, size, italic, align).
    """
    theme = theme or Theme()
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = _prepare_frame(box, margins=margins, anchor=anchor)
    _fill_paragraphs(
        frame,
        _normalize(content),
        size=size or theme.body_size,
        color=color or theme.text,
        bold=bold,
        font=font or theme.font,
        font_ea=font_ea or theme.font_ea,
        align=align,
        line_spacing=line_spacing,
        space_after=space_after,
    )
    return box


def add_bullets(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    items,
    *,
    theme: Theme | None = None,
    size: float | None = None,
    color: str | None = None,
    bullet_color: str | None = None,
    space_after: float = 6,
    line_spacing: float = 1.1,
    numbered: bool = False,
):
    """Real bulleted (or numbered) list: bullet glyphs come from paragraph formatting,
    not typed characters. items: list of str, (text, level) or dict(text, level, bold...).
    """
    theme = theme or Theme()
    size = size or theme.body_size
    box = add_text(
        slide,
        x,
        y,
        w,
        h,
        items,
        theme=theme,
        size=size,
        color=color,
        line_spacing=line_spacing,
        space_after=space_after,
    )
    for paragraph in box.text_frame.paragraphs:
        level = paragraph.level
        p_pr = paragraph._p.get_or_add_pPr()
        indent = Pt(size * 1.1)
        p_pr.set("marL", str(int(indent * (level + 1))))
        p_pr.set("indent", str(-int(indent)))
        for tag in ("a:buClr", "a:buSzPct", "a:buFont", "a:buNone", "a:buAutoNum", "a:buChar"):
            for old in p_pr.findall(qn(tag)):
                p_pr.remove(old)
        bu_clr = etree.SubElement(p_pr, qn("a:buClr"))
        etree.SubElement(bu_clr, qn("a:srgbClr"), val=bullet_color or theme.primary)
        if numbered and level == 0:
            etree.SubElement(p_pr, qn("a:buFont"), typeface="+mj-lt")
            etree.SubElement(p_pr, qn("a:buAutoNum"), type="arabicPeriod")
        else:
            etree.SubElement(p_pr, qn("a:buFont"), typeface="Arial")
            etree.SubElement(p_pr, qn("a:buChar"), char="•" if level == 0 else "–")
        # bullet properties must precede tab stops / defRPr / extLst in a:pPr
        for tag in ("a:tabLst", "a:defRPr", "a:extLst"):
            node = p_pr.find(qn(tag))
            if node is not None:
                p_pr.remove(node)
                p_pr.append(node)
    return box


def add_title(
    slide,
    text: str,
    theme: Theme,
    *,
    subtitle: str | None = None,
    y: float | None = None,
    size: float | None = None,
):
    """Slide title plus an optional subtitle line.

    Uses the layout's title placeholder when there is one, else a text box.
    """
    top = theme.title_top if y is None else y
    width = SLIDE_W - 2 * theme.margin
    height = 0.75
    title = slide.shapes.title
    if title is None:
        title = slide.shapes.add_textbox(
            Inches(theme.margin), Inches(top), Inches(width), Inches(height)
        )
    else:
        title.left, title.top, title.width, title.height = (
            Inches(theme.margin),
            Inches(top),
            Inches(width),
            Inches(height),
        )
    frame = _prepare_frame(title, margins=(0, 0, 0, 0), anchor="bottom")
    frame.text = ""
    _fill_paragraphs(
        frame,
        _normalize(text),
        size=size or theme.title_size,
        color=theme.text,
        bold=True,
        font=theme.font,
        font_ea=theme.font_ea,
        align="left",
        line_spacing=1.0,
        space_after=None,
    )
    if subtitle:
        add_text(
            slide,
            theme.margin,
            top + height + 0.02,
            width,
            0.4,
            subtitle,
            theme=theme,
            size=theme.subtitle_size,
            color=theme.muted,
            margins=(0, 0, 0, 0),
        )
    return title


# --------------------------------------------------------------------------- shapes, grid, images


_BOX_KINDS = {
    "rect": MSO_SHAPE.RECTANGLE,
    "rounded": MSO_SHAPE.ROUNDED_RECTANGLE,
    "oval": MSO_SHAPE.OVAL,
    "chevron": MSO_SHAPE.CHEVRON,
    "pentagon": MSO_SHAPE.PENTAGON,
}


def add_box(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str | None = None,
    line: str | None = None,
    line_width: float = 0.75,
    kind: str = "rect",
    radius: float = 0.08,
    text: str | None = None,
    theme: Theme | None = None,
    size: float | None = None,
    color: str | None = None,
    bold: bool = True,
    align: str = "center",
):
    """A flat shape without the theme's shadow/outline: cards, bands, markers.

    kind: rect, rounded, oval, chevron, pentagon. fill/line None = transparent.
    text: optional label drawn inside the shape (centred, wrapping on).
    """
    shape = slide.shapes.add_shape(_BOX_KINDS[kind], Inches(x), Inches(y), Inches(w), Inches(h))
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)  # drops the theme's default fill, outline and shadow
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = RGBColor.from_string(line)
        shape.line.width = Pt(line_width)
    else:
        shape.line.fill.background()
    if kind == "rounded":
        shape.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
    if text is not None:
        theme = theme or Theme()
        frame = _prepare_frame(shape, margins=(0.05, 0.03, 0.05, 0.03), anchor="middle")
        _fill_paragraphs(
            frame,
            _normalize(text),
            size=size or theme.body_size,
            color=color or theme.text,
            bold=bold,
            font=theme.font,
            font_ea=theme.font_ea,
            align=align,
            line_spacing=1.0,
            space_after=None,
        )
    return shape


def columns(
    box, count: int, gap: float | None = None, weights=None
) -> list[tuple[float, float, float, float]]:
    """Split box (x, y, w, h) into count side-by-side boxes separated by gap inches."""
    x, y, w, h = box
    gap = Theme.gap if gap is None else gap
    weights = weights or [1] * count
    usable = w - gap * (count - 1)
    out, cursor = [], x
    for weight in weights:
        width = usable * weight / sum(weights)
        out.append((cursor, y, width, h))
        cursor += width + gap
    return out


def rows(
    box, count: int, gap: float | None = None, weights=None
) -> list[tuple[float, float, float, float]]:
    """Split box (x, y, w, h) into count stacked boxes separated by gap inches."""
    x, y, w, h = box
    flipped = columns((y, x, h, w), count, gap, weights)
    return [(fx, fy, fh, fw) for fy, fx, fw, fh in flipped]


def add_image(slide, path: str, x: float, y: float, w: float, h: float, *, mode: str = "contain"):
    """Place an image in a box without distortion.

    contain: whole image visible, centred in the box. cover: image fills the box, overflow cropped.
    """
    from PIL import Image

    with Image.open(path) as image:
        img_w, img_h = image.size
    box_ratio, img_ratio = w / h, img_w / img_h
    if mode == "contain":
        if img_ratio > box_ratio:
            pw, ph = w, w / img_ratio
        else:
            pw, ph = h * img_ratio, h
        return slide.shapes.add_picture(
            path, Inches(x + (w - pw) / 2), Inches(y + (h - ph) / 2), Inches(pw), Inches(ph)
        )
    picture = slide.shapes.add_picture(path, Inches(x), Inches(y), Inches(w), Inches(h))
    if img_ratio > box_ratio:  # too wide: crop left/right
        excess = 1 - box_ratio / img_ratio
        picture.crop_left = picture.crop_right = excess / 2
    else:
        excess = 1 - img_ratio / box_ratio
        picture.crop_top = picture.crop_bottom = excess / 2
    return picture


# --------------------------------------------------------------------------- charts

_CHARTS = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "stacked_bar": XL_CHART_TYPE.BAR_STACKED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "line_plain": XL_CHART_TYPE.LINE,
    "area": XL_CHART_TYPE.AREA,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
    "scatter": XL_CHART_TYPE.XY_SCATTER,
}


def add_chart(
    slide,
    kind: str,
    categories,
    series: dict,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    theme: Theme | None = None,
    number_format: str = "General",
    legend: str | None = "bottom",
    data_labels: bool = False,
    gridlines: bool = True,
    font_size: float | None = None,
    colors=None,
    gap_width: int = 70,
    title: str | None = None,
):
    """A native, editable chart.

    kind: column, stacked_column, bar, stacked_bar, line, line_plain, area, pie, doughnut, scatter.
    series: {"Revenue": [..], "Cost": [..]} (scatter: {"name": [(x, y), ...]}).
    The slide title should carry the message; title= adds a chart title only when needed.
    """
    theme = theme or Theme()
    colors = list(colors or theme.accents)
    font_size = font_size or theme.small_size
    if kind == "scatter":
        data = XyChartData()
        for name, points in series.items():
            s = data.add_series(name)
            for px, py in points:
                s.add_data_point(px, py)
    else:
        data = CategoryChartData(number_format=number_format)
        data.categories = list(categories)
        for name, values in series.items():
            data.add_series(name, list(values))
    frame = slide.shapes.add_chart(_CHARTS[kind], Inches(x), Inches(y), Inches(w), Inches(h), data)
    chart = frame.chart
    set_font(chart.font, size=font_size, color=theme.muted, name=theme.font, ea=theme.font_ea)
    chart.has_title = bool(title)
    if title:
        chart.chart_title.text_frame.text = title
        set_font(
            chart.chart_title.text_frame.paragraphs[0].font,
            size=font_size + 2,
            bold=True,
            color=theme.text,
        )
    single = kind in ("pie", "doughnut")
    chart.has_legend = bool(legend) and (single or len(series) > 1)
    if chart.has_legend:
        chart.legend.position = {
            "bottom": XL_LEGEND_POSITION.BOTTOM,
            "right": XL_LEGEND_POSITION.RIGHT,
            "top": XL_LEGEND_POSITION.TOP,
        }[legend]
        chart.legend.include_in_layout = False
        set_font(chart.legend.font, size=font_size, color=theme.muted)
    plot = chart.plots[0]
    if hasattr(plot, "gap_width") and kind in ("column", "stacked_column", "bar", "stacked_bar"):
        plot.gap_width = gap_width
        if kind.startswith("stacked"):
            plot.overlap = 100
    if single:
        for index, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(colors[index % len(colors)])
            point.format.line.color.rgb = RGBColor.from_string(theme.bg)
    else:
        for index, s in enumerate(plot.series):
            color = RGBColor.from_string(colors[index % len(colors)])
            if kind.startswith("line") or kind == "scatter":
                s.format.line.color.rgb = color
                s.format.line.width = Pt(2.25)
                s.smooth = False
                if kind != "line_plain":
                    s.marker.format.fill.solid()
                    s.marker.format.fill.fore_color.rgb = color
                    s.marker.format.line.color.rgb = color
            else:
                s.format.fill.solid()
                s.format.fill.fore_color.rgb = color
    if data_labels:
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.number_format = number_format
        labels.number_format_is_linked = False
        set_font(labels.font, size=font_size, color=theme.text)
        if kind in ("column", "bar"):
            labels.position = XL_LABEL_POSITION.OUTSIDE_END
        elif single:
            labels.position = (
                XL_LABEL_POSITION.OUTSIDE_END if kind == "pie" else XL_LABEL_POSITION.CENTER
            )
    if not single:
        value_axis, category_axis = chart.value_axis, chart.category_axis
        values = [
            v for vs in series.values() for v in (vs if kind != "scatter" else []) if v is not None
        ]
        if (
            kind in ("column", "stacked_column", "bar", "stacked_bar", "area")
            and values
            and min(values) >= 0
        ):
            value_axis.minimum_scale = 0  # bars and areas start at zero
        value_axis.has_major_gridlines = gridlines
        if gridlines:
            value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(theme.rule)
            value_axis.major_gridlines.format.line.width = Pt(0.75)
        value_axis.format.line.fill.background()
        value_axis.tick_labels.number_format = number_format
        value_axis.tick_labels.number_format_is_linked = False
        category_axis.format.line.color.rgb = RGBColor.from_string(theme.rule)
        category_axis.has_major_gridlines = False
        for axis in (value_axis, category_axis):
            set_font(axis.tick_labels.font, size=font_size, color=theme.muted)
    return frame


# --------------------------------------------------------------------------- tables

_NO_GRID_STYLE = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
_NUMERIC = re.compile(
    r"^[\s(+\-−]*[$€£¥]?\s*[\d.,]+\s*(%|x|×|bn|mm|m|k|亿|万|元)?\)?\s*$", re.IGNORECASE
)


def _cell_border(cell, side: str, color: str | None, width_pt: float = 0.75) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tag = {"left": "a:lnL", "right": "a:lnR", "top": "a:lnT", "bottom": "a:lnB"}[side]
    for old in tc_pr.findall(qn(tag)):
        tc_pr.remove(old)
    line = etree.Element(qn(tag), w=str(int(Pt(width_pt))) if color else "0")
    if color:
        fill = etree.SubElement(line, qn("a:solidFill"))
        etree.SubElement(fill, qn("a:srgbClr"), val=color)
    else:
        etree.SubElement(line, qn("a:noFill"))
    # border elements come first in a:tcPr, in the order lnL, lnR, lnT, lnB
    order = ["a:lnL", "a:lnR", "a:lnT", "a:lnB"]
    position = 0
    for existing in tc_pr:
        if existing.tag in [qn(t) for t in order[: order.index(tag)]]:
            position += 1
    tc_pr.insert(position, line)


def add_table(
    slide,
    data,
    x: float,
    y: float,
    w: float,
    h: float | None = None,
    *,
    theme: Theme | None = None,
    col_widths=None,
    font_size: float | None = None,
    header: bool = True,
    align=None,
    zebra: bool = False,
    row_height: float | None = None,
    bold_last_row: bool = False,
):
    """A native table from a list of rows (first row = header when header=True).

    align: list of "l"/"c"/"r" per column; by default numeric-looking columns are
    right-aligned and the rest left-aligned. Height defaults to rows x row_height
    (0.4 in at 12 pt); rows still grow if text wraps, so keep cell text short.
    """
    theme = theme or Theme()
    font_size = font_size or theme.small_size
    n_rows, n_cols = len(data), max(len(row) for row in data)
    row_height = row_height or round(font_size / 72 * 2.4, 2)
    h = h or row_height * n_rows
    frame = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y), Inches(w), Inches(h))
    table = frame.table
    tbl_pr = table._tbl.tblPr
    style_id = tbl_pr.find(qn("a:tableStyleId"))
    if style_id is None:
        style_id = etree.SubElement(tbl_pr, qn("a:tableStyleId"))
    style_id.text = _NO_GRID_STYLE
    table.first_row = header
    table.horz_banding = False
    widths = col_widths or [w / n_cols] * n_cols
    for index, width in enumerate(widths):
        table.columns[index].width = Inches(width)
    for row in table.rows:
        row.height = Inches(h / n_rows)
    if align is None:
        body = data[1:] if header else data
        align = []
        for col in range(n_cols):
            values = [str(r[col]) for r in body if col < len(r) and str(r[col]).strip()]
            align.append("r" if values and all(_NUMERIC.match(v) for v in values) else "l")
    for r_index, row_values in enumerate(data):
        is_header = header and r_index == 0
        is_last = r_index == n_rows - 1
        for c_index in range(n_cols):
            cell = table.cell(r_index, c_index)
            value = row_values[c_index] if c_index < len(row_values) else ""
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            frame_text = cell.text_frame
            frame_text.word_wrap = True
            frame_text.text = ""
            paragraph = frame_text.paragraphs[0]
            paragraph.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[
                align[c_index]
            ]
            run = paragraph.add_run()
            run.text = str(value)
            set_font(
                run.font,
                size=font_size,
                bold=is_header or (bold_last_row and is_last),
                color=theme.on_primary if is_header else theme.text,
                name=theme.font,
                ea=theme.font_ea,
            )
            if is_header:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor.from_string(theme.primary)
            elif zebra and r_index % 2 == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor.from_string(theme.surface)
            else:
                cell.fill.background()
            for side in ("left", "right", "top"):
                _cell_border(cell, side, None)
            _cell_border(
                cell,
                "bottom",
                theme.text if (bold_last_row and r_index == n_rows - 2) else theme.rule,
            )
    return frame


# --------------------------------------------------------------------------- template filling


def _text_frames(target):
    """Every text frame in a Presentation, slide, shape collection or shape.

    Group members and table cells are included.
    """
    if hasattr(target, "slides"):
        for slide in target.slides:
            yield from _text_frames(slide)
        return
    if hasattr(target, "shapes") and not hasattr(target, "shape_type"):
        for shape in target.shapes:
            yield from _text_frames(shape)
        return
    shape = target
    if getattr(shape, "shape_type", None) is not None and hasattr(shape, "shapes"):  # group
        for child in shape.shapes:
            yield from _text_frames(child)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            for cell in row.cells:
                yield cell.text_frame
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        yield shape.text_frame


def replace_text(target, old: str, new: str) -> int:
    """Replace old with new everywhere under target, keeping run formatting.

    Works when old spans several runs (the replacement takes the formatting of
    the run where the match starts). Returns the number of replacements.
    """
    count = 0
    for frame in _text_frames(target):
        for paragraph in frame.paragraphs:
            while True:
                runs = list(paragraph.runs)
                full = "".join(run.text for run in runs)
                start = full.find(old)
                if start < 0 or not old:
                    break
                end = start + len(old)
                position = 0
                first = True
                for run in runs:
                    run_start, run_end = position, position + len(run.text)
                    position = run_end
                    if run_end <= start or run_start >= end:
                        continue
                    before = run.text[: max(0, start - run_start)]
                    after = run.text[max(0, end - run_start) :] if run_end > end else ""
                    run.text = before + (new if first else "") + after
                    first = False
                count += 1
                if new.find(old) >= 0:
                    break  # avoid endless replacement when new contains old
    return count


def set_text(shape_or_frame, content) -> None:
    """Replace all text in a shape/text frame, keeping paragraph and run formatting.

    Paragraph i of the new content takes the paragraph properties and first-run
    formatting of existing paragraph i (or of the last existing paragraph when
    there are more new paragraphs). content: str ("a\\nb") or a list of str /
    (text, level) / dict(text, level).
    """
    frame = shape_or_frame.text_frame if hasattr(shape_or_frame, "text_frame") else shape_or_frame
    tx_body = frame._txBody
    old_paragraphs = tx_body.findall(qn("a:p"))
    templates = []
    for p in old_paragraphs:
        p_pr = p.find(qn("a:pPr"))
        r = p.find(qn("a:r"))
        r_pr = r.find(qn("a:rPr")) if r is not None else None
        if r_pr is None:
            r_pr = p.find(qn("a:endParaRPr"))
        templates.append((p_pr, r_pr))
    for p in old_paragraphs:
        tx_body.remove(p)
    for index, item in enumerate(_normalize(content)):
        p_pr, r_pr = templates[min(index, len(templates) - 1)] if templates else (None, None)
        p = etree.SubElement(tx_body, qn("a:p"))
        if p_pr is not None:
            p.append(copy.deepcopy(p_pr))
        if "level" in item:
            _set_level(p, item["level"])
        r = etree.SubElement(p, qn("a:r"))
        if r_pr is not None:
            run_pr = copy.deepcopy(r_pr)
            run_pr.tag = qn("a:rPr")
            r.append(run_pr)
        etree.SubElement(r, qn("a:t")).text = item["text"]


def replace_image(picture, path: str):
    """Swap the image of an existing picture shape, keeping its position and size.

    The new image is cropped (centred) to the frame's aspect ratio so it is not
    stretched. Returns the picture shape.
    """
    from PIL import Image

    _, r_id = picture.part.get_or_add_image_part(path)
    blip = picture._element.find(".//" + qn("a:blip"))
    blip.set(qn("r:embed"), r_id)
    with Image.open(path) as image:
        img_ratio = image.width / image.height
    box_ratio = picture.width / picture.height
    picture.crop_left = picture.crop_right = picture.crop_top = picture.crop_bottom = 0.0
    if img_ratio > box_ratio:
        picture.crop_left = picture.crop_right = (1 - box_ratio / img_ratio) / 2
    elif img_ratio < box_ratio:
        picture.crop_top = picture.crop_bottom = (1 - img_ratio / box_ratio) / 2
    return picture


def _set_level(p, level: int) -> None:
    p_pr = p.find(qn("a:pPr"))
    if p_pr is None:
        p_pr = etree.Element(qn("a:pPr"))
        p.insert(0, p_pr)
    p_pr.set("lvl", str(level))
