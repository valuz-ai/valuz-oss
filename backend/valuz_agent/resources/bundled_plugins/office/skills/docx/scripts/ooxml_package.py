"""Read and write an OOXML package (.docx/.xlsx/.pptx) at the XML level.

Shared by the docx scripts in this directory; not a command by itself.
Members keep their original order on save, except that ``[Content_Types].xml``
and ``_rels/.rels`` always come first (what Office writes and what strict
readers expect).
"""

from __future__ import annotations

import posixpath
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_OFFICE_DOCUMENT = REL_BASE + "officeDocument"
REL_STYLES = REL_BASE + "styles"
REL_NUMBERING = REL_BASE + "numbering"
REL_SETTINGS = REL_BASE + "settings"
REL_COMMENTS = REL_BASE + "comments"
REL_FOOTNOTES = REL_BASE + "footnotes"
REL_ENDNOTES = REL_BASE + "endnotes"
REL_HEADER = REL_BASE + "header"
REL_FOOTER = REL_BASE + "footer"
REL_HYPERLINK = REL_BASE + "hyperlink"
REL_COMMENTS_EXTENDED = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"

CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"

FIRST_MEMBERS = ("[Content_Types].xml", "_rels/.rels")

# Child order of w:settings (ECMA-376 CT_Settings); Word rejects misplaced settings.
SETTINGS_ORDER = (
    "writeProtection view zoom removePersonalInformation removeDateAndTime "
    "doNotDisplayPageBoundaries displayBackgroundShape printPostScriptOverText "
    "printFractionalCharacterWidth printFormsData embedTrueTypeFonts embedSystemFonts "
    "saveSubsetFonts saveFormsData mirrorMargins alignBordersAndEdges "
    "bordersDoNotSurroundHeader bordersDoNotSurroundFooter gutterAtTop hideSpellingErrors "
    "hideGrammaticalErrors activeWritingStyle proofState formsDesign attachedTemplate "
    "linkStyles stylePaneFormatFilter stylePaneSortMethod documentType mailMerge revisionView "
    "trackRevisions doNotTrackMoves doNotTrackFormatting documentProtection autoFormatOverride "
    "styleLockTheme styleLockQFSet defaultTabStop autoHyphenation consecutiveHyphenLimit "
    "hyphenationZone doNotHyphenateCaps showEnvelope summaryLength clickAndTypeStyle "
    "defaultTableStyle evenAndOddHeaders bookFoldRevPrinting bookFoldPrinting "
    "bookFoldPrintingSheets drawingGridHorizontalSpacing drawingGridVerticalSpacing "
    "displayHorizontalDrawingGridEvery displayVerticalDrawingGridEvery "
    "doNotUseMarginsForDrawingGridOrigin drawingGridHorizontalOrigin drawingGridVerticalOrigin "
    "doNotShadeFormData noPunctuationKerning characterSpacingControl printTwoOnOne "
    "strictFirstAndLastChars noLineBreaksAfter noLineBreaksBefore savePreviewPicture "
    "doNotValidateAgainstSchema saveInvalidXml ignoreMixedContent alwaysShowPlaceholderText "
    "doNotDemarcateInvalidXml saveXmlDataOnly useXSLTWhenSaving saveThroughXslt showXMLTags "
    "alwaysMergeEmptyNamespace updateFields hdrShapeDefaults footnotePr endnotePr compat "
    "docVars rsids mathPr attachedSchema themeFontLang clrSchemeMapping "
    "doNotIncludeSubdocsInStats doNotAutoCompressPictures forceUpgrade captions "
    "readModeInkLockDown smartTagType schemaLibrary shapeDefaults doNotEmbedSmartTags "
    "decimalSymbol listSeparator"
).split()

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def w(tag: str) -> str:
    """Clark name for a WordprocessingML element or attribute."""
    return f"{{{W_NS}}}{tag}"


def local(tag: object) -> str:
    """Local name of an element tag ('' for comments and processing instructions)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def ns_of(tag: object) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]


def utc_now() -> str:
    """Timestamp in the form Word writes for revisions and comments."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, _PARSER)


def serialize_xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def rels_name(part: str) -> str:
    """Name of the relationships member for ``part`` ('' means the package)."""
    if not part:
        return "_rels/.rels"
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def relative_target(source_part: str, target_part: str) -> str:
    return posixpath.relpath(target_part, posixpath.dirname(source_part) or ".")


def insert_in_order(
    parent: etree._Element, child: etree._Element, successors: tuple[str, ...]
) -> etree._Element:
    """Insert ``child`` before the first existing child whose local name is in ``successors``.

    OOXML schemas fix the order of most child elements; ``successors`` lists the
    local names that must come after ``child``.
    """
    names = set(successors)
    for existing in parent:
        if local(existing.tag) in names:
            existing.addprevious(child)
            return child
    parent.append(child)
    return child


class Package:
    """An OOXML package loaded into memory; XML members are parsed on demand."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.order: list[str] = []
        self.blobs: dict[str, bytes] = {}
        self._xml: dict[str, etree._Element] = {}
        with zipfile.ZipFile(self.path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.filename in self.blobs:
                    raise ValueError(f"duplicate ZIP member: {info.filename}")
                self.order.append(info.filename)
                self.blobs[info.filename] = archive.read(info.filename)

    # -- members ---------------------------------------------------------------

    def has(self, name: str) -> bool:
        return name in self.blobs or name in self._xml

    def names(self) -> list[str]:
        return list(self.order)

    def xml(self, name: str) -> etree._Element:
        if name not in self._xml:
            if name not in self.blobs:
                raise KeyError(f"package has no member {name}")
            self._xml[name] = parse_xml(self.blobs[name])
        return self._xml[name]

    def set_xml(self, name: str, root: etree._Element) -> None:
        self._xml[name] = root
        if name not in self.order:
            self.order.append(name)
            self.blobs[name] = b""

    def set_blob(self, name: str, data: bytes) -> None:
        self._xml.pop(name, None)
        if name not in self.order:
            self.order.append(name)
        self.blobs[name] = data

    def remove(self, name: str) -> None:
        self._xml.pop(name, None)
        self.blobs.pop(name, None)
        if name in self.order:
            self.order.remove(name)

    # -- relationships ---------------------------------------------------------

    def relationships(self, part: str) -> etree._Element:
        """The ``<Relationships>`` root for ``part``, created empty when missing."""
        name = rels_name(part)
        if not self.has(name):
            self.set_xml(
                name, etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})
            )
        return self.xml(name)

    def related(self, part: str, rel_type: str) -> list[str]:
        """Internal targets of ``part``'s relationships of ``rel_type``."""
        if not self.has(rels_name(part)):
            return []
        return [
            resolve_target(part, rel.get("Target", ""))
            for rel in self.relationships(part)
            if rel.get("Type") == rel_type and rel.get("TargetMode") != "External"
        ]

    def rel_targets(self, part: str) -> dict[str, tuple[str, str, bool]]:
        """Map relationship id -> (type, target, is_external) for ``part``."""
        if not self.has(rels_name(part)):
            return {}
        result = {}
        for rel in self.relationships(part):
            external = rel.get("TargetMode") == "External"
            target = rel.get("Target", "")
            result[rel.get("Id", "")] = (
                rel.get("Type", ""),
                target if external else resolve_target(part, target),
                external,
            )
        return result

    def add_relationship(
        self, part: str, rel_type: str, target: str, external: bool = False
    ) -> str:
        root = self.relationships(part)
        used = {rel.get("Id") for rel in root}
        number = 1
        while f"rId{number}" in used:
            number += 1
        rel = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
        rel.set("Id", f"rId{number}")
        rel.set("Type", rel_type)
        rel.set("Target", target if external else relative_target(part, target))
        if external:
            rel.set("TargetMode", "External")
        return f"rId{number}"

    def main_part(self) -> str:
        targets = self.related("", REL_OFFICE_DOCUMENT)
        if not targets:
            raise ValueError("package has no officeDocument relationship")
        return targets[0]

    # -- content types ---------------------------------------------------------

    def content_type(self, name: str) -> str | None:
        types = self.xml("[Content_Types].xml")
        for node in types:
            if local(node.tag) == "Override" and node.get("PartName") == "/" + name:
                return node.get("ContentType")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        for node in types:
            if local(node.tag) == "Default" and node.get("Extension", "").lower() == extension:
                return node.get("ContentType")
        return None

    def set_override(self, name: str, content_type: str) -> None:
        types = self.xml("[Content_Types].xml")
        for node in types:
            if local(node.tag) == "Override" and node.get("PartName") == "/" + name:
                node.set("ContentType", content_type)
                return
        node = etree.SubElement(types, f"{{{CT_NS}}}Override")
        node.set("PartName", "/" + name)
        node.set("ContentType", content_type)

    # -- save ------------------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        for name, root in self._xml.items():
            self.blobs[name] = serialize_xml(root)
        ordered = [n for n in FIRST_MEMBERS if n in self.blobs]
        ordered += [n for n in self.order if n not in FIRST_MEMBERS and n in self.blobs]
        write_zip(out, [(name, self.blobs[name]) for name in ordered])
        return out


def write_zip(path: Path, members: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def next_annotation_id(package: Package) -> int:
    """A ``w:id`` value not used by any element in the package's word/*.xml parts.

    Revision, comment and bookmark ids then never collide, whatever their kind.
    """
    highest = 0
    for name in package.names():
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = package.xml(name)
        if ns_of(root.tag) != W_NS:
            continue
        for node in root.iter():
            value = node.get(w("id")) if isinstance(node.tag, str) else None
            if value is not None and value.lstrip("-").isdigit():
                highest = max(highest, int(value))
    return highest + 1
