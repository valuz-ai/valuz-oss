"""Helpers for building Word documents with python-docx.

Import this module in a build script (before opening or creating documents):

    import sys
    sys.path.insert(0, "<this skill's directory>/scripts")
    from docx_helpers import *

Covers what python-docx has no API for: page presets, theme-free style fonts,
East Asian fonts, real list numbering, table widths/borders/shading/header
rows, pictures fitted to the page, sections with columns, hyperlinks and
bookmarks, footnotes/endnotes, fields (page numbers, TOC placeholder).
Lengths are python-docx lengths (Cm, Mm, Pt, Inches); colours are "RRGGBB".
"""

from __future__ import annotations

import copy
import re
from typing import Any

from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.image.image import Image as DocxImage
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import PartFactory, XmlPart
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Emu, Inches, Length, Mm, Pt, RGBColor
from docx.text.paragraph import Paragraph
from ooxml_package import SETTINGS_ORDER

__all__ = [
    "PAGE_SIZES",
    "set_page_layout",
    "text_width",
    "text_height",
    "set_document_fonts",
    "set_style_font",
    "set_run_font",
    "create_list",
    "add_list_item",
    "set_col_widths",
    "set_repeat_header",
    "keep_row_together",
    "shade_cell",
    "set_cell_borders",
    "set_table_borders",
    "add_table",
    "add_picture_fit",
    "add_section",
    "set_orientation",
    "set_columns",
    "add_column_break",
    "add_page_break",
    "add_hyperlink",
    "add_bookmark",
    "add_internal_link",
    "add_footnote",
    "add_endnote",
    "add_tab_stop",
    "add_field",
    "add_page_number_footer",
    "set_page_number_start",
    "add_toc",
    "update_fields_on_open",
    "replace_text",
]

FOOTNOTES_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
ENDNOTES_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"

# Let python-docx load existing footnote/endnote parts as editable XML.
PartFactory.part_type_for.setdefault(FOOTNOTES_CT, XmlPart)
PartFactory.part_type_for.setdefault(ENDNOTES_CT, XmlPart)

PAGE_SIZES: dict[str, tuple[Length, Length]] = {
    "A4": (Mm(210), Mm(297)),
    "A3": (Mm(297), Mm(420)),
    "A5": (Mm(148), Mm(210)),
    "Letter": (Inches(8.5), Inches(11)),
    "Legal": (Inches(8.5), Inches(14)),
}

_THEME_FONT_ATTRS = ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme")

# Child order of the property containers we touch (ECMA-376 sequences).
_RPR_ORDER = (
    "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint "
    "noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u "
    "effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath"
).split()
_TCPR_ORDER = (
    "cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText "
    "vAlign hideMark headers cellIns cellDel cellMerge tcPrChange"
).split()
_TBLPR_ORDER = (
    "tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc "
    "tblCellSpacing tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption "
    "tblDescription tblPrChange"
).split()
_SECTPR_ORDER = (
    "headerReference footerReference footnotePr endnotePr type pgSz pgMar paperSrc pgBorders "
    "lnNumType pgNumType cols formProt vAlign noEndnote titlePg textDirection bidi rtlGutter "
    "docGrid printerSettings sectPrChange"
).split()
_SETTINGS_ORDER = SETTINGS_ORDER


# ---------------------------------------------------------------- XML basics


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _insert_ordered(parent, child, order: list[str]):
    """Insert ``child`` into ``parent`` at its schema position given by ``order``."""
    name = _local(child.tag)
    successors = set(order[order.index(name) + 1 :]) if name in order else set()
    for existing in parent:
        if _local(existing.tag) in successors:
            existing.addprevious(child)
            return child
    parent.append(child)
    return child


def _replace_child(parent, tag: str, order: list[str]):
    """Remove any ``tag`` children of ``parent`` and insert a fresh one in order."""
    for old in parent.findall(qn(tag)):
        parent.remove(old)
    return _insert_ordered(parent, OxmlElement(tag), order)


def _hex(color: str | RGBColor) -> str:
    text = str(color).lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", text):
        raise ValueError(f"colour must be RRGGBB, got {color!r}")
    return text


def _twips(length: Length) -> int:
    return int(round(Emu(length).pt * 20))


def _story_part(obj):
    part = getattr(obj, "part", None)
    if part is None:
        raise ValueError("object is not attached to a document part")
    return part


def _document_of(obj):
    """The python-docx Document that owns ``obj`` (a Document, paragraph, cell...)."""
    if hasattr(obj, "sections") and hasattr(obj, "styles"):
        return obj
    part = _story_part(obj)
    document = getattr(part, "document", None)
    if document is None:  # header, footer and notes parts
        document = part.package.main_document_part.document
    return document


# ---------------------------------------------------------------- page setup


def set_page_layout(section, size: str = "A4", orientation: str = "portrait", margins=None):
    """Set paper size, orientation and margins of a section.

    margins: one Length for all sides, or (top, right, bottom, left).
    """
    if size not in PAGE_SIZES:
        raise ValueError(f"size must be one of {sorted(PAGE_SIZES)}")
    width, height = PAGE_SIZES[size]
    if orientation == "landscape":
        width, height = height, width
        section.orientation = WD_ORIENT.LANDSCAPE
    elif orientation == "portrait":
        section.orientation = WD_ORIENT.PORTRAIT
    else:
        raise ValueError("orientation must be 'portrait' or 'landscape'")
    section.page_width, section.page_height = width, height
    if margins is not None:
        top, right, bottom, left = margins if isinstance(margins, (tuple, list)) else (margins,) * 4
        section.top_margin, section.right_margin = top, right
        section.bottom_margin, section.left_margin = bottom, left
    return section


def set_orientation(section, orientation: str):
    """Switch a section between portrait and landscape, swapping width and height."""
    width, height = section.page_width, section.page_height
    landscape = orientation == "landscape"
    if landscape != (width > height):
        section.page_width, section.page_height = height, width
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    return section


def text_width(section) -> Length:
    """Usable line width of a section (page width minus side margins and gutter)."""
    gutter = section.gutter or 0
    return Emu(section.page_width - section.left_margin - section.right_margin - gutter)


def text_height(section) -> Length:
    return Emu(section.page_height - section.top_margin - section.bottom_margin)


# ---------------------------------------------------------------- fonts and styles


def _set_fonts_on_rpr(rpr, latin: str | None, east_asia: str | None):
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = _insert_ordered(rpr, OxmlElement("w:rFonts"), _RPR_ORDER)
    if latin:
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme"):
            fonts.attrib.pop(qn(attr), None)
        fonts.set(qn("w:ascii"), latin)
        fonts.set(qn("w:hAnsi"), latin)
        fonts.set(qn("w:cs"), latin)
    if east_asia:
        fonts.attrib.pop(qn("w:eastAsiaTheme"), None)
        fonts.set(qn("w:eastAsia"), east_asia)
    return fonts


def _apply_run_props(rpr, size=None, bold=None, italic=None, color=None):
    if size is not None:
        points = size.pt if isinstance(size, Length) else float(size)
        half_points = str(int(round(points * 2)))
        for tag in ("w:sz", "w:szCs"):
            _replace_child(rpr, tag, _RPR_ORDER).set(qn("w:val"), half_points)
    for flag, tags in ((bold, ("w:b", "w:bCs")), (italic, ("w:i", "w:iCs"))):
        if flag is not None:
            for tag in tags:
                _replace_child(rpr, tag, _RPR_ORDER).set(qn("w:val"), "1" if flag else "0")
    if color is not None:
        _replace_child(rpr, "w:color", _RPR_ORDER).set(qn("w:val"), _hex(color))


def set_run_font(
    run,
    latin: str | None = None,
    east_asia: str | None = None,
    size=None,
    bold=None,
    italic=None,
    color=None,
):
    """Direct font settings for one run; ``east_asia`` sets the CJK font (w:eastAsia).

    ``size`` is in points (number) or a Length.
    """
    rpr = run._r.get_or_add_rPr()
    if latin or east_asia:
        _set_fonts_on_rpr(rpr, latin, east_asia)
    _apply_run_props(rpr, size=size, bold=bold, italic=italic, color=color)
    return run


def set_style_font(
    document,
    style_name: str,
    latin: str | None = None,
    east_asia: str | None = None,
    size=None,
    bold=None,
    italic=None,
    color=None,
    space_before=None,
    space_after=None,
):
    """Override a (built-in or custom) style's font, size, weight, colour and spacing.

    Removes theme-font and theme-colour attributes that would otherwise win over
    the explicit values.
    """
    style = document.styles[style_name]
    rpr = style.element.get_or_add_rPr()
    if latin or east_asia:
        _set_fonts_on_rpr(rpr, latin, east_asia)
    _apply_run_props(rpr, size=size, bold=bold, italic=italic, color=color)
    if color is not None:
        node = rpr.find(qn("w:color"))
        for attr in ("w:themeColor", "w:themeShade", "w:themeTint"):
            node.attrib.pop(qn(attr), None)
    if space_before is not None:
        style.paragraph_format.space_before = space_before
    if space_after is not None:
        style.paragraph_format.space_after = space_after
    return style


def set_document_fonts(
    document,
    latin: str = "Calibri",
    east_asia: str | None = None,
    size=None,
    east_asia_lang: str | None = None,
):
    """Set the document-wide default fonts and drop theme fonts from every style.

    east_asia: CJK font, e.g. "SimSun", "Microsoft YaHei", "SimHei".
    east_asia_lang: e.g. "zh-CN", "ja-JP"; tells Word and LibreOffice which
    language CJK text is.
    """
    styles = document.styles.element
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is None:
        defaults = OxmlElement("w:docDefaults")
        styles.insert(0, defaults)
    rpr_default = defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        rpr_default = OxmlElement("w:rPrDefault")
        defaults.insert(0, rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    fonts = _set_fonts_on_rpr(rpr, latin, east_asia)
    for attr in _THEME_FONT_ATTRS:
        fonts.attrib.pop(qn(attr), None)
    if size is not None:
        _apply_run_props(rpr, size=size)
    if east_asia_lang:
        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = _insert_ordered(rpr, OxmlElement("w:lang"), _RPR_ORDER)
        lang.set(qn("w:eastAsia"), east_asia_lang)
        if lang.get(qn("w:val")) is None:
            lang.set(qn("w:val"), "en-US")
    for node in styles.iter(qn("w:rFonts")):
        if node.getparent() is rpr:
            continue
        for attr in _THEME_FONT_ATTRS:
            node.attrib.pop(qn(attr), None)
        if not node.attrib:
            node.getparent().remove(node)
    return document


# ---------------------------------------------------------------- lists

_BULLETS = ("•", "◦", "▪")
_NUMBER_FORMATS = (("decimal", "%{n}."), ("lowerLetter", "%{n}."), ("lowerRoman", "%{n}."))


def create_list(document, kind: str = "bullet", start: int = 1) -> int:
    """Create a new list definition and return its numId for ``add_list_item``.

    kind: "bullet" (•, ◦, ▪), "number" (1. / a. / i.), or "outline"
    (1. / 1.1. / 1.1.1., for contracts and specs). Each call starts a new
    list, so numbering restarts at ``start``.
    """
    if kind not in ("bullet", "number", "outline"):
        raise ValueError("kind must be 'bullet', 'number' or 'outline'")
    numbering = document.part.numbering_part.element
    abstract_ids = [
        int(n.get(qn("w:abstractNumId"))) for n in numbering.findall(qn("w:abstractNum"))
    ]
    num_ids = [int(n.get(qn("w:numId"))) for n in numbering.findall(qn("w:num"))]
    abstract_id = max(abstract_ids, default=-1) + 1
    num_id = max(num_ids, default=0) + 1
    levels = []
    number_pos = 0
    for level in range(9):
        if kind == "bullet":
            fmt, text = "bullet", _BULLETS[level % 3]
            left, hanging = 720 * (level + 1), 360
        elif kind == "number":
            fmt, pattern = _NUMBER_FORMATS[level % 3]
            text = pattern.format(n=level + 1)
            left, hanging = 720 * (level + 1), 360
        else:
            fmt = "decimal"
            text = "".join(f"%{i + 1}." for i in range(level + 1))
            # each level's number sits where the previous level's text starts
            hanging = 567 + 142 * level
            left = number_pos + hanging
            number_pos = left
        level_start = start if level == 0 else 1
        fonts = (
            '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:hint="default"/></w:rPr>'
            if kind == "bullet"
            else ""
        )
        levels.append(
            f'<w:lvl w:ilvl="{level}"><w:start w:val="{level_start}"/>'
            f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{left}" w:hanging="{hanging}"/></w:pPr>{fonts}</w:lvl>'
        )
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
        f'<w:multiLevelType w:val="{"multilevel" if kind == "outline" else "hybridMultilevel"}"/>'
        f"{''.join(levels)}</w:abstractNum>"
    )
    first_num = numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering.append(abstract)
    num = parse_xml(
        f'<w:num {nsdecls("w")} w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'
    )
    cleanup = numbering.find(qn("w:numIdMacAtCleanup"))
    if cleanup is not None:
        cleanup.addprevious(num)
    else:
        numbering.append(num)
    return num_id


def add_list_item(
    container, text: str, list_id: int, level: int = 0, style: str | None = "List Paragraph"
):
    """Append a list paragraph (real Word numbering, no typed bullet) and return it."""
    paragraph = container.add_paragraph(text)
    if style:
        try:
            paragraph.style = style
        except KeyError:
            pass
    num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    num_pr.get_or_add_ilvl().val = level
    num_pr.get_or_add_numId().val = list_id
    return paragraph


# ---------------------------------------------------------------- tables


def set_col_widths(table, widths):
    """Fix column widths (grid, every cell, total width) so Word and LibreOffice agree.

    Merged cells get the sum of the columns they span.
    """
    widths = [Emu(w) for w in widths]
    table.autofit = False
    tbl = table._tbl
    grid = tbl.tblGrid
    columns = grid.findall(qn("w:gridCol"))
    for index, column in enumerate(columns):
        if index < len(widths):
            column.set(qn("w:w"), str(_twips(widths[index])))
    for tr in tbl.tr_lst:
        col = 0
        trpr = tr.trPr
        if trpr is not None:
            before = trpr.find(qn("w:gridBefore"))
            col = int(before.get(qn("w:val"), "0")) if before is not None else 0
        for tc in tr.tc_lst:
            span = tc.grid_span
            tc.width = Emu(sum(widths[col : col + span]))
            col += span
    tblpr = tbl.tblPr
    tblw = tblpr.find(qn("w:tblW"))
    if tblw is None:
        tblw = _insert_ordered(tblpr, OxmlElement("w:tblW"), _TBLPR_ORDER)
    tblw.set(qn("w:w"), str(sum(_twips(w) for w in widths)))
    tblw.set(qn("w:type"), "dxa")
    return table


def set_repeat_header(row, repeat: bool = True):
    """Repeat this row (and the rows above it) at the top of every page."""
    trpr = row._tr.get_or_add_trPr()
    for node in trpr.findall(qn("w:tblHeader")):
        trpr.remove(node)
    if repeat:
        node = OxmlElement("w:tblHeader")
        _insert_before_any(trpr, node, ("ins", "del", "trPrChange"))
    return row


def keep_row_together(row, keep: bool = True):
    """Prevent a row from breaking across pages."""
    trpr = row._tr.get_or_add_trPr()
    for node in trpr.findall(qn("w:cantSplit")):
        trpr.remove(node)
    if keep:
        _insert_before_any(trpr, OxmlElement("w:cantSplit"), ("ins", "del", "trPrChange"))
    return row


def _insert_before_any(parent, child, names):
    for existing in parent:
        if _local(existing.tag) in names:
            existing.addprevious(child)
            return child
    parent.append(child)
    return child


def shade_cell(cell, fill: str):
    """Solid background colour for a table cell."""
    tcpr = cell._tc.get_or_add_tcPr()
    shd = _replace_child(tcpr, "w:shd", _TCPR_ORDER)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), _hex(fill))
    return cell


def _border(node, spec):
    if spec is None:
        node.set(qn("w:val"), "nil")
        return
    spec = {"val": "single", "sz": 4, "space": 0, "color": "000000", **spec}
    node.set(qn("w:val"), str(spec["val"]))
    node.set(qn("w:sz"), str(spec["sz"]))
    node.set(qn("w:space"), str(spec["space"]))
    node.set(qn("w:color"), "auto" if spec["color"] == "auto" else _hex(spec["color"]))


def set_cell_borders(cell, **edges):
    """Borders for one cell: set_cell_borders(cell, bottom={"sz": 12, "color": "1F3864"}).

    Edges: top, left, bottom, right (None removes the edge). sz is in eighths of a point.
    """
    tcpr = cell._tc.get_or_add_tcPr()
    borders = tcpr.find(qn("w:tcBorders"))
    if borders is None:
        borders = _insert_ordered(tcpr, OxmlElement("w:tcBorders"), _TCPR_ORDER)
    order = ["top", "left", "bottom", "right", "insideH", "insideV", "tl2br", "tr2bl"]
    for edge, spec in edges.items():
        if edge not in order:
            raise ValueError(f"unknown border edge {edge!r}")
        node = _replace_child(borders, f"w:{edge}", order)
        _border(node, spec)
    return cell


def set_table_borders(
    table, size: int = 4, color: str = "000000", style: str = "single", inside: bool = True
):
    """Uniform table borders (size in eighths of a point); inside=False keeps only the frame."""
    tblpr = table._tbl.tblPr
    borders = _replace_child(tblpr, "w:tblBorders", _TBLPR_ORDER)
    spec = {"val": style, "sz": size, "color": color}
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement(f"w:{edge}")
        borders.append(node)
        _border(node, spec if inside or not edge.startswith("inside") else None)
    return table


_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def add_table(
    container,
    rows,
    *,
    header: bool = True,
    col_widths=None,
    style: str | None = "Table Grid",
    header_fill: str | None = "D9E2F3",
    header_bold: bool = True,
    font_size=None,
    align=None,
    repeat_header: bool = True,
):
    """Add a formatted table from a list of rows (first row is the header when header=True).

    align: one alignment for all columns or a list per column ("left", "center",
    "right"; numbers usually "right"). col_widths defaults to equal columns over
    the text width.
    """
    rows = [list(row) for row in rows]
    if not rows:
        raise ValueError("rows must not be empty")
    ncols = max(len(row) for row in rows)
    try:
        table = container.add_table(rows=len(rows), cols=ncols)
    except TypeError:
        width = col_widths and sum(Emu(w) for w in col_widths)
        if not width:
            width = text_width(_document_of(container).sections[-1])
        table = container.add_table(len(rows), ncols, width)
    document = _document_of(container)
    if style:
        try:
            table.style = document.styles[style]
        except KeyError:
            set_table_borders(table)
    else:
        set_table_borders(table)
    if col_widths is None:
        total = text_width(document.sections[-1])
        col_widths = [Emu(int(total / ncols))] * ncols
    set_col_widths(table, col_widths)
    aligns = align if isinstance(align, (list, tuple)) else [align] * ncols
    for r, values in enumerate(rows):
        for c in range(ncols):
            cell = table.cell(r, c)
            value = values[c] if c < len(values) else ""
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run("" if value is None else str(value))
            if font_size is not None:
                run.font.size = font_size if isinstance(font_size, Length) else Pt(font_size)
            if header and r == 0:
                run.bold = header_bold
                if header_fill:
                    shade_cell(cell, header_fill)
            if c < len(aligns) and aligns[c]:
                paragraph.alignment = _ALIGN[aligns[c]]
    if header and repeat_header:
        set_repeat_header(table.rows[0])
    return table


# ---------------------------------------------------------------- pictures


def add_picture_fit(
    container, path, *, max_width=None, max_height=None, align: str = "center", section=None
):
    """Add a picture in its own paragraph, scaled down to fit the text area.

    Keeps the aspect ratio and never enlarges past the image's native size.
    Default box: the text width and 85% of the text height of the last section.
    """
    image = DocxImage.from_file(str(path))
    if max_width is None or max_height is None:
        section = section or _document_of(container).sections[-1]
        max_width = max_width or text_width(section)
        max_height = max_height or Emu(int(text_height(section) * 0.85))
    scale = min(1.0, max_width / image.width, max_height / image.height)
    paragraph = container.add_paragraph()
    paragraph.alignment = _ALIGN[align]
    run = paragraph.add_run()
    run.add_picture(
        str(path), width=Emu(int(image.width * scale)), height=Emu(int(image.height * scale))
    )
    return paragraph


# ---------------------------------------------------------------- breaks, sections, columns

_SECTION_STARTS = {
    "new_page": WD_SECTION.NEW_PAGE,
    "continuous": WD_SECTION.CONTINUOUS,
    "odd_page": WD_SECTION.ODD_PAGE,
    "even_page": WD_SECTION.EVEN_PAGE,
}


def add_page_break(document):
    document.add_page_break()


def add_section(
    document,
    start: str = "new_page",
    orientation: str | None = None,
    columns: int | None = None,
    column_space=None,
):
    """Start a new section for the content added after this call and return it.

    The new section inherits page setup and headers/footers (linked to previous);
    set ``columns`` (1 to undo a multi-column previous section) and/or
    ``orientation`` here.
    """
    section = document.add_section(_SECTION_STARTS[start])
    if orientation:
        set_orientation(section, orientation)
    if columns is not None:
        set_columns(section, columns, space=column_space)
    return section


def set_columns(section, count: int, space=None, separator: bool = False):
    """Newspaper columns for a section (space defaults to 1.25 cm)."""
    sectpr = section._sectPr
    cols = _replace_child(sectpr, "w:cols", _SECTPR_ORDER)
    cols.set(qn("w:num"), str(count))
    cols.set(qn("w:space"), str(_twips(space if space is not None else Mm(12.5))))
    if separator:
        cols.set(qn("w:sep"), "1")
    cols.set(qn("w:equalWidth"), "1")
    return section


def add_column_break(paragraph):
    """Make ``paragraph`` start at the top of the next column.

    The break goes before the paragraph's first run, so the new column does not
    begin with an empty line.
    """
    run = paragraph.add_run()
    run.add_break(WD_BREAK.COLUMN)
    ppr = paragraph._p.pPr
    if ppr is not None:
        ppr.addnext(run._r)
    else:
        paragraph._p.insert(0, run._r)
    return paragraph


# ---------------------------------------------------------------- links and bookmarks


def _ensure_char_style(document, style_id: str, name: str, rpr_xml: str):
    styles = document.styles.element
    for style in styles.findall(qn("w:style")):
        if style.get(qn("w:styleId")) == style_id:
            return style_id
    styles.append(
        parse_xml(
            f'<w:style {nsdecls("w")} w:type="character" w:styleId="{style_id}">'
            f'<w:name w:val="{name}"/><w:uiPriority w:val="99"/><w:unhideWhenUsed/>'
            f"<w:rPr>{rpr_xml}</w:rPr></w:style>"
        )
    )
    return style_id


def _text_run(text: str, rpr=None):
    run = OxmlElement("w:r")
    if rpr is not None:
        run.append(rpr)
    for index, chunk in enumerate(text.split("\t")):
        if index:
            run.append(OxmlElement("w:tab"))
        if chunk:
            node = OxmlElement("w:t")
            node.set(qn("xml:space"), "preserve")
            node.text = chunk
            run.append(node)
    return run


def _link_rpr(document, color: str | None, underline: bool):
    style_id = _ensure_char_style(
        document, "Hyperlink", "Hyperlink", '<w:color w:val="0563C1"/><w:u w:val="single"/>'
    )
    rpr = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), style_id)
    rpr.append(style)
    if color:
        _insert_ordered(rpr, OxmlElement("w:color"), _RPR_ORDER).set(qn("w:val"), _hex(color))
    if underline:
        _insert_ordered(rpr, OxmlElement("w:u"), _RPR_ORDER).set(qn("w:val"), "single")
    return rpr


def add_hyperlink(
    paragraph, text: str, url: str, color: str | None = "0563C1", underline: bool = True
):
    """Append a clickable external link (w:hyperlink + relationship) to a paragraph."""
    part = _story_part(paragraph)
    rel_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    link.set(qn("w:history"), "1")
    link.append(_text_run(text, _link_rpr(_document_of(paragraph), color, underline)))
    paragraph._p.append(link)
    return link


def _next_bookmark_id(document) -> int:
    ids = [
        int(node.get(qn("w:id")))
        for node in document.element.iter(qn("w:bookmarkStart"))
        if node.get(qn("w:id"), "").isdigit()
    ]
    return max(ids, default=0) + 1


def add_bookmark(paragraph, name: str):
    """Wrap the paragraph's current content in a bookmark (target for internal links)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", name):
        raise ValueError(
            "bookmark names: letters, digits, underscore; start with a letter or _; max 40"
        )
    bookmark_id = str(_next_bookmark_id(_document_of(paragraph)))
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bookmark_id)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bookmark_id)
    p = paragraph._p
    ppr = p.pPr
    if ppr is not None:
        ppr.addnext(start)
    else:
        p.insert(0, start)
    p.append(end)
    return name


def add_internal_link(
    paragraph, text: str, bookmark: str, color: str | None = "0563C1", underline: bool = True
):
    """Append a link that jumps to a bookmark in the same document."""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), bookmark)
    link.set(qn("w:history"), "1")
    link.append(_text_run(text, _link_rpr(_document_of(paragraph), color, underline)))
    paragraph._p.append(link)
    return link


# ---------------------------------------------------------------- footnotes / endnotes

_NOTES_XML = (
    '<w:{kind}s {ns}><w:{kind} w:type="separator" w:id="-1"><w:p><w:pPr>'
    '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p>'
    '</w:{kind}><w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:pPr>'
    '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:continuationSeparator/>'
    "</w:r></w:p></w:{kind}></w:{kind}s>"
)


class _PartParent:
    """Minimal parent so python-docx Paragraph objects inside a notes part resolve ``.part``."""

    def __init__(self, part):
        self.part = part


def _default_paragraph_style_id(document) -> str | None:
    for style in document.styles.element.findall(qn("w:style")):
        if style.get(qn("w:type")) == "paragraph" and style.get(qn("w:default")) in (
            "1",
            "true",
            "on",
        ):
            return style.get(qn("w:styleId"))
    return None


def _ensure_note_styles(document, kind: str):
    title = kind.capitalize()
    styles = document.styles.element
    existing = {style.get(qn("w:styleId")) for style in styles.findall(qn("w:style"))}
    if f"{title}Text" not in existing:
        based = _default_paragraph_style_id(document)
        based_xml = f'<w:basedOn w:val="{based}"/>' if based else ""
        styles.append(
            parse_xml(
                f'<w:style {nsdecls("w")} w:type="paragraph" w:styleId="{title}Text">'
                f'<w:name w:val="{kind} text"/>{based_xml}'
                '<w:uiPriority w:val="99"/><w:unhideWhenUsed/>'
                '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
                '<w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>'
            )
        )
    _ensure_char_style(
        document, f"{title}Reference", f"{kind} reference", '<w:vertAlign w:val="superscript"/>'
    )


def _notes_part(document, kind: str):
    doc_part = document.part
    reltype = RT.FOOTNOTES if kind == "footnote" else RT.ENDNOTES
    for rel in doc_part.rels.values():
        if rel.reltype == reltype and not rel.is_external:
            part = rel.target_part
            if not isinstance(part, XmlPart):
                raise RuntimeError(
                    "import docx_helpers before opening the document so its notes are editable"
                )
            return part
    used = {str(part.partname) for part in doc_part.package.iter_parts()}
    name = f"/word/{kind}s.xml"
    number = 1
    while name in used:
        name = f"/word/{kind}s{number}.xml"
        number += 1
    content_type = FOOTNOTES_CT if kind == "footnote" else ENDNOTES_CT
    part = XmlPart(
        PackURI(name),
        content_type,
        parse_xml(_NOTES_XML.format(kind=kind, ns=nsdecls("w", "r"))),
        doc_part.package,
    )
    doc_part.relate_to(part, reltype)
    _ensure_note_styles(document, kind)
    return part


def _add_note(paragraph, text: str, kind: str):
    document = _document_of(paragraph)
    if _story_part(paragraph) is not document.part:
        raise ValueError(f"{kind}s can only be referenced from the main document body")
    part = _notes_part(document, kind)
    root = part.element
    ids = [
        int(n.get(qn("w:id")))
        for n in root.findall(qn(f"w:{kind}"))
        if n.get(qn("w:id"), "").lstrip("-").isdigit()
    ]
    note_id = max([0, *ids]) + 1
    title = kind.capitalize()
    note = parse_xml(
        f'<w:{kind} {nsdecls("w")} w:id="{note_id}"><w:p>'
        f'<w:pPr><w:pStyle w:val="{title}Text"/></w:pPr>'
        f'<w:r><w:rPr><w:rStyle w:val="{title}Reference"/>'
        f'<w:vertAlign w:val="superscript"/></w:rPr>'
        f"<w:{kind}Ref/></w:r></w:p></w:{kind}>"
    )
    root.append(note)
    note_paragraph = Paragraph(note.find(qn("w:p")), _PartParent(part))
    note_paragraph.add_run(" " + text)
    ref = parse_xml(
        f'<w:r {nsdecls("w")}><w:rPr><w:rStyle w:val="{title}Reference"/>'
        f'<w:vertAlign w:val="superscript"/></w:rPr>'
        f'<w:{kind}Reference w:id="{note_id}"/></w:r>'
    )
    paragraph._p.append(ref)
    return note_paragraph


def add_footnote(paragraph, text: str):
    """Append a footnote reference at the current end of ``paragraph``.

    Returns the footnote's own paragraph (add runs to format it). Build the
    paragraph left to right: add_run(...), add_footnote(...), add_run(...).
    """
    return _add_note(paragraph, text, "footnote")


def add_endnote(paragraph, text: str):
    """Like add_footnote, but the note is collected at the end of the document."""
    return _add_note(paragraph, text, "endnote")


# ---------------------------------------------------------------- tab stops

_TAB_ALIGN = {
    "left": WD_TAB_ALIGNMENT.LEFT,
    "center": WD_TAB_ALIGNMENT.CENTER,
    "right": WD_TAB_ALIGNMENT.RIGHT,
    "decimal": WD_TAB_ALIGNMENT.DECIMAL,
}
_TAB_LEADER = {
    None: WD_TAB_LEADER.SPACES,
    "dot": WD_TAB_LEADER.DOTS,
    "hyphen": WD_TAB_LEADER.DASHES,
    "underscore": WD_TAB_LEADER.LINES,
    "middle_dot": WD_TAB_LEADER.MIDDLE_DOT,
}


def add_tab_stop(paragraph, position=None, align: str = "right", leader: str | None = "dot"):
    """Add a tab stop; default is right-aligned at the right margin with dot leaders."""
    if position is None:
        position = text_width(_document_of(paragraph).sections[-1])
    paragraph.paragraph_format.tab_stops.add_tab_stop(
        position, _TAB_ALIGN[align], _TAB_LEADER[leader]
    )
    return paragraph


# ---------------------------------------------------------------- fields


def _field_char(kind: str, dirty: bool = False):
    run = OxmlElement("w:r")
    node = OxmlElement("w:fldChar")
    node.set(qn("w:fldCharType"), kind)
    if dirty:
        node.set(qn("w:dirty"), "true")
    run.append(node)
    return run


def add_field(paragraph, instruction: str, placeholder: str = "", dirty: bool = False, rpr=None):
    """Append a complex field (e.g. "PAGE", "NUMPAGES", 'DATE \\@ "yyyy-MM-dd"').

    ``placeholder`` is the cached result shown until a renderer recomputes the
    field; ``dirty`` asks Word to refresh it on open. ``rpr`` (a w:rPr element)
    formats every run of the field.
    """
    instr = OxmlElement("w:r")
    node = OxmlElement("w:instrText")
    node.set(qn("xml:space"), "preserve")
    node.text = f" {instruction.strip()} "
    instr.append(node)
    runs = [
        _field_char("begin", dirty),
        instr,
        _field_char("separate"),
        _text_run(placeholder),
        _field_char("end"),
    ]
    for run in runs:
        if rpr is not None:
            run.insert(0, copy.deepcopy(rpr))
        paragraph._p.append(run)
    return runs


def add_page_number_footer(
    section,
    template: str = "Page {PAGE} of {NUMPAGES}",
    align: str = "center",
    size=None,
    first_page: bool = False,
):
    """Write a footer such as "Page 3 of 10" or "第 {PAGE} 页 共 {NUMPAGES} 页".

    Tokens in braces become fields: {PAGE}, {NUMPAGES}, {SECTIONPAGES}. The
    renderer (Word, LibreOffice/dsoffice) computes them; no update step needed.
    """
    footer = section.first_page_footer if first_page else section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)
    paragraph.alignment = _ALIGN[align]
    rpr = None
    if size is not None:
        rpr = OxmlElement("w:rPr")
        _apply_run_props(rpr, size=size)
    for piece in re.split(r"(\{[A-Z]+\})", template):
        if not piece:
            continue
        if re.fullmatch(r"\{[A-Z]+\}", piece):
            add_field(paragraph, piece[1:-1], "1", rpr=rpr)
        else:
            run = _text_run(piece, copy.deepcopy(rpr) if rpr is not None else None)
            paragraph._p.append(run)
    return paragraph


def set_page_number_start(section, start: int | None = 1, fmt: str | None = None):
    """Restart page numbering in a section; fmt: decimal, lowerRoman, upperRoman, ..."""
    sectpr = section._sectPr
    node = sectpr.find(qn("w:pgNumType"))
    if node is None:
        node = _insert_ordered(sectpr, OxmlElement("w:pgNumType"), _SECTPR_ORDER)
    if start is not None:
        node.set(qn("w:start"), str(start))
    if fmt:
        node.set(qn("w:fmt"), fmt)
    return section


def add_toc(document, levels: str = "1-3", title: str | None = None):
    """Insert a table-of-contents field placeholder at the current end of the document.

    After saving, run ``scripts/docx_toc.py`` to write the entries and page
    numbers (python-docx cannot paginate). Headings must use heading styles.
    """
    if title:
        try:
            document.add_paragraph(title, style="TOC Heading")
        except KeyError:
            document.add_paragraph().add_run(title).bold = True
    paragraph = document.add_paragraph()
    add_field(
        paragraph,
        f'TOC \\o "{levels}" \\h \\z \\u',
        "Table of contents: run docx_toc.py to fill it.",
        dirty=True,
    )
    return paragraph


def _paragraph_texts(paragraph):
    """The paragraph's visible w:t nodes (direct runs and runs inside hyperlinks)."""
    nodes = []
    for node in paragraph._p.iter(qn("w:t")):
        run = node.getparent()
        holder = run.getparent() if run is not None else None
        if holder is paragraph._p or (holder is not None and holder.tag == qn("w:hyperlink")):
            nodes.append(node)
    return nodes


def _replace_in_paragraph(paragraph, old: str, new: str, limit: int | None) -> int:
    count = 0
    search_from = 0
    while limit is None or count < limit:
        nodes = _paragraph_texts(paragraph)
        owners = []
        for index, node in enumerate(nodes):
            owners.extend((index, offset) for offset in range(len(node.text or "")))
        full = "".join(node.text or "" for node in nodes)
        start = full.find(old, search_from)
        if start == -1 or not old:
            return count
        end = start + len(old)
        first, first_offset = owners[start]
        last, last_offset = owners[end - 1]
        for index in range(first, last + 1):
            node = nodes[index]
            text = node.text or ""
            if index == first and index == last:
                node.text = text[:first_offset] + new + text[last_offset + 1 :]
            elif index == first:
                node.text = text[:first_offset] + new
            elif index == last:
                node.text = text[last_offset + 1 :]
            else:
                node.text = ""
            node.set(qn("xml:space"), "preserve")
        search_from = start + len(new)
        count += 1
    return count


def replace_text(
    document, old: str, new: str, include_headers: bool = True, limit: int | None = None
) -> int:
    """Replace text everywhere (body, tables, headers/footers) keeping run formatting.

    A match may span several runs; the new text takes the formatting of the
    run where the match starts. Returns the number of replacements. Not a
    tracked change: use docx_review.py for that.
    """
    parts = [document.element.body]
    if include_headers:
        for section in document.sections:
            for story in (
                section.header,
                section.footer,
                section.first_page_header,
                section.first_page_footer,
                section.even_page_header,
                section.even_page_footer,
            ):
                if not story.is_linked_to_previous:
                    parts.append(story._element)
    total = 0
    seen = set()
    for root in parts:
        if id(root) in seen:
            continue
        seen.add(id(root))
        for p in root.iter(qn("w:p")):
            remaining = None if limit is None else limit - total
            if remaining == 0:
                return total
            total += _replace_in_paragraph(Paragraph(p, document._body), old, new, remaining)
    return total


def update_fields_on_open(document, enabled: bool = True):
    """Ask Word to refresh all fields (TOC, page references) when the file opens.

    Word shows a confirmation prompt; LibreOffice ignores the flag.
    """
    settings = document.settings.element
    for node in settings.findall(qn("w:updateFields")):
        settings.remove(node)
    if enabled:
        node = OxmlElement("w:updateFields")
        node.set(qn("w:val"), "true")
        _insert_ordered(settings, node, _SETTINGS_ORDER)
    return document
