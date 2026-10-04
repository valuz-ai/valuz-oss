# Editing the XML of a .docx

Use this when python-docx has no API for the change and none of the scripts
covers it: content controls, fields, text boxes, custom XML, numbering
definitions, compatibility settings, or a precise fix that must not disturb
anything else.

## Workflow

```sh
S=<skill>/scripts
valuz-python $S/docx_pack.py unpack contract.docx contract-x      # new or empty folder
# edit contract-x/word/document.xml (and other parts) with a script or by hand
valuz-python $S/docx_pack.py pack contract-x contract-edited.docx
valuz-python $S/check_office.py contract-edited.docx --contains "new wording"
dsoffice convert --input contract-edited.docx --output contract-edited.pdf   # proves it opens
```

- `unpack` indents every `.xml`/`.rels` file so it can be read and diffed;
  `pack` removes that indentation again (whitespace inside `w:t`, `w:delText`,
  `w:instrText` and other text elements is never touched). Use `--raw` on both
  to keep bytes exactly as they are.
- `pack` refuses to write when an XML file does not parse, a part has no
  content type, or an internal relationship points at a missing file, and it
  writes `[Content_Types].xml` first and `_rels/.rels` second. It never
  overwrites an existing output unless `--force`.
- Prefer a small `valuz-python` script with lxml for edits over hand editing
  when there are many changes; parse with `lxml.etree`, change, and write back
  with `xml_declaration=True, encoding="UTF-8", standalone=True`.

## Package map

| Member | Holds |
|---|---|
| `[Content_Types].xml` | content type of every part (`Default` by extension, `Override` by part name) |
| `_rels/.rels` | package relationships (main document, core/app properties) |
| `word/document.xml` | body: paragraphs `w:p`, runs `w:r`, text `w:t`, tables `w:tbl`, final section `w:sectPr` |
| `word/_rels/document.xml.rels` | ids (`rId..`) used by the body: styles, numbering, headers, footers, images, hyperlinks, notes, comments |
| `word/styles.xml` | styles and document defaults (`w:docDefaults`) |
| `word/numbering.xml` | list definitions: `w:abstractNum` (all of them first) then `w:num` |
| `word/settings.xml` | document settings (order of children is fixed by the schema) |
| `word/header*.xml`, `word/footer*.xml` | header/footer stories, referenced from each `w:sectPr` |
| `word/footnotes.xml`, `word/endnotes.xml`, `word/comments.xml` | notes and comments |
| `word/media/*` | images |

## Rules that keep Word happy

- Text is split across runs unpredictably (spell-check marks, revision ids,
  formatting changes). Search the paragraph's concatenated `w:t` text, not one
  `w:t`. Keep `xml:space="preserve"` on `w:t` that starts or ends with a space.
- Child order inside property elements is fixed (`w:pPr`: `w:pStyle` first,
  ..., `w:rPr`, `w:sectPr`, `w:pPrChange` last; `w:rPr`: `w:rStyle`,
  `w:rFonts`, `w:b`, ..., `w:sz`, ..., `w:lang` ...). Word reports a damaged
  file for out-of-order children even when LibreOffice opens it.
- A new part needs three things: the file, an `Override` in
  `[Content_Types].xml` (unless a `Default` for its extension fits), and a
  `Relationship` from the part that uses it with a unique `Id`.
- Every `r:id`/`r:embed` in a part must exist in that part's `.rels`.
- A table cell must contain at least one `w:p`, and a table must be followed by
  a paragraph before the end of a cell or of the body.
- Bookmark, comment and revision `w:id` values must be unique per kind; ids
  above the current maximum are always safe.
- Section properties: each `w:p/w:pPr/w:sectPr` ends a section; the body's last
  `w:sectPr` describes the final section.
- Complex fields are `w:fldChar begin` / `w:instrText` / `w:fldChar separate` /
  result runs / `w:fldChar end`, possibly across runs and paragraphs; edit the
  instruction and the cached result together.

Run `check_office.py` and `dsoffice convert` on every packed file; if Word
compatibility matters and something is unusual, also reopen the file with
python-docx (`Document(path)`) to make sure it parses.
