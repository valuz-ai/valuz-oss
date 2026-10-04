# Tracked changes and comments with docx_review.py

`scripts/docx_review.py` edits the package XML directly, so it works on any
.docx (Word, WPS, LibreOffice, python-docx output) and leaves every other part
untouched. Every command reads `IN.docx` and writes a new `OUT.docx`; on any
error nothing is written and the exit code is 1.

## Commands

| Command | Required options | Result |
|---|---|---|
| `replace` | `--find OLD --with NEW` | `OLD` as a deletion, `NEW` as an insertion right after it, `NEW` formatted like the first replaced run |
| `delete` | `--find TEXT` | `TEXT` as a deletion |
| `insert` | `--anchor TEXT --text NEW [--before]` | `NEW` inserted after (or before) `TEXT`, formatted like the neighbouring run |
| `insert-paragraph` | `--anchor TEXT --text NEW [--before] [--style NAME]` | new paragraph after/before the paragraph containing `TEXT`, with its paragraph mark marked inserted; copies that paragraph's formatting unless `--style` is given |
| `delete-paragraph` | `--find TEXT` | whole paragraph containing `TEXT`, including its paragraph mark, as a deletion |
| `comment` | `--find TEXT --text COMMENT` | comment anchored to `TEXT` (`\n` in COMMENT starts a new comment paragraph) |
| `accept-all` / `reject-all` | none | every revision resolved |
| `apply` | `--ops OPS.json` | the listed operations in order, in one output file |

Common options: `--occurrence N` (1-based, counted in document order across
paragraphs; default 1) or `--all`; `--comment TEXT` on replace/delete/insert/
insert-paragraph/delete-paragraph adds an explanatory comment on the change;
`--author NAME` (default "Valuz"), `--initials`, `--date 2026-10-04T09:30:00Z`
(default: now, UTC).

## Matching rules

- Matching is literal and case-sensitive on the visible text of one paragraph
  (deleted text is invisible, inserted text is visible). A match cannot cross a
  paragraph boundary; split multi-paragraph edits into one operation per
  paragraph or use the paragraph operations.
- Tabs match `\t` and line breaks `\n`. Curly quotes, non-breaking spaces and
  full-width punctuation must match exactly: copy the text from
  `docx_inspect.py text` output.
- Body text, tables and text boxes are searched; headers, footers and notes are
  not.
- Runs are split at the match boundaries, so only the matched characters change
  and the surrounding formatting stays. Text already inside someone else's
  tracked insertion, or a field code, is refused (accept/reject first).
- When the matched text is a hyperlink, `insert` puts the new text outside the
  link and gives it the formatting of plain text in the paragraph.

## Ops file for apply

```json
[
  {"op": "replace", "find": "thirty (30) days", "with": "forty-five (45) days",
   "comment": "Aligned with clause 7.2"},
  {"op": "delete", "find": ", without limitation,"},
  {"op": "insert", "anchor": "Annex A", "text": " and Annex B"},
  {"op": "insert-paragraph", "anchor": "Governing law", "text": "Disputes go to arbitration.",
   "style": "Body Text"},
  {"op": "delete-paragraph", "find": "This clause is intentionally left blank"},
  {"op": "comment", "find": "Section 4", "text": "Please confirm", "occurrence": 2},
  {"op": "replace", "find": "Supplier", "with": "Vendor", "all": true, "author": "Legal team"}
]
```

Keys: `op`, `find`, `with`, `anchor`, `text`, `before`, `style`, `comment`,
`occurrence`, `all`, plus per-operation `author`, `initials`, `date`
overriding the command line. `accept-all` / `reject-all` may also appear as
operations. Operations run in order on the result of the previous ones, so a
later operation sees earlier insertions as visible text.

## Output

```json
{"output": "out.docx", "results": [{"op": "replace", "changed": 1}, ...]}
```

Then check:

```sh
valuz-python <skill>/scripts/docx_inspect.py changes out.docx
valuz-python <skill>/scripts/docx_inspect.py comments out.docx
valuz-python <skill>/scripts/docx_inspect.py text out.docx --view markup
valuz-python <skill>/scripts/check_office.py out.docx
dsoffice render --input out.docx --output-dir review-v1
```

In a `dsoffice render` of the .docx, insertions are underlined and deletions
struck through in an author colour with change bars in the margin; commented
ranges are highlighted with an anchor mark. The PDF from `dsoffice convert`
shows the revisions but not the comments.

## Accept / reject all

`accept-all` keeps insertions, drops deletions, joins paragraphs whose mark was
deleted, removes rows marked deleted, and keeps current formatting where a
formatting change was tracked. `reject-all` does the opposite: drops insertions
(including inserted paragraphs and rows), restores deleted text, and restores
the recorded old run, paragraph, section, table, row and cell properties. Moves
(`w:moveFrom`/`w:moveTo`) are treated as a deletion plus an insertion. Both run
over the main document, headers, footers, footnotes, endnotes and comments.
Comments are kept; the settings (for example whether Word keeps tracking new
edits) are not changed.

## How the XML looks (for manual edits)

```xml
<w:p>
  <w:r><w:t xml:space="preserve">deliver within </w:t></w:r>
  <w:commentRangeStart w:id="0"/>
  <w:del w:id="7" w:author="Jane Doe" w:date="2026-10-04T09:30:00Z">
    <w:r><w:rPr><w:b/></w:rPr><w:delText>30 days</w:delText></w:r>
  </w:del>
  <w:ins w:id="8" w:author="Jane Doe" w:date="2026-10-04T09:30:00Z">
    <w:r><w:rPr><w:b/></w:rPr><w:t>45 days</w:t></w:r>
  </w:ins>
  <w:commentRangeEnd w:id="0"/>
  <w:r><w:commentReference w:id="0"/></w:r>
</w:p>
```

- Deleted text is in `w:delText` (field codes in `w:delInstrText`) inside
  `w:del`; inserted runs are ordinary runs inside `w:ins`.
- An inserted or deleted paragraph mark is `w:ins`/`w:del` as the first child of
  that paragraph's `w:pPr/w:rPr`.
- Revision `w:id` values must be unique; the script uses ids above every
  existing `w:id` in the package.
- Comments live in `word/comments.xml` (`w:comment` with `w:id`, `w:author`,
  `w:date`, `w:initials`), registered in `[Content_Types].xml` as
  `application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml`
  and related from `word/_rels/document.xml.rels` with type
  `http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments`.
  The body marks the range with `w:commentRangeStart`/`w:commentRangeEnd` and
  a run containing `w:commentReference`, all with the comment's id.
