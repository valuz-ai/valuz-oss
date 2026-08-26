# Local parser: MarkItDown → anydoc

> Why the LightLocal parser's office backend changed, what it bought, what it
> cost, and the invariants that must not regress.

Status: **landed**. Supersedes MarkItDown entirely — there is no fallback.

---

## 1. What changed

`LightLocalParser` (`backend/valuz_agent/integrations/parser_light_local.py`)
dispatches by extension family. Only the office family moved:

| Family | Before | After |
|--------|--------|-------|
| `.pdf` | pymupdf4llm | *unchanged* |
| **office / spreadsheet / ODF / RTF / EPUB** | **MarkItDown 0.1.5** | **anydoc (`firecrawl-anydoc`)** |
| `.html` `.htm` | html-to-markdown | *unchanged* |
| `.md .txt .csv .json .xml` | raw UTF-8 | *unchanged* |
| images | RapidOCR | *unchanged* |

[anydoc](https://github.com/firecrawl/anydoc) is a Rust library (MIT) with
PyO3 bindings, shipping `cp310-abi3` wheels for every platform we package:
mac arm64/x64, linux x64/aarch64, win x64.

## 2. Why

Measured on Chinese fixtures (research docx, valuation xlsx, strategy pptx)
plus anydoc's own legacy-format corpus. Three findings, in order of weight:

**a. Format coverage.** MarkItDown converted three formats. Everything else in
the office family fell through to `*Unsupported file type*` — and `.rtf` was
worse than that (§3).

| | anydoc | MarkItDown |
|---|---|---|
| `.doc` `.ppt` (legacy OLE) | ✅ | ❌ no converter |
| `.xls` | ✅ | ⚠️ needs an `xls` extra we never installed |
| `.rtf` `.odt` `.ods` `.odp` `.epub` | ✅ | ❌ |
| `.docx` `.xlsx` `.pptx` | ✅ | ✅ |

**b. Spreadsheet fidelity.** MarkItDown routes sheets through pandas, so its
output carried pandas artifacts into the model verbatim:

```
MarkItDown                              anydoc
| 营业收入 | 8.630000e+10 | NaN |        | 营业收入 | 86300000000 |
| 毛利率   | 2.236000e-01 | NaN |        | 毛利率   | 0.2236 |
```

Scientific notation for a revenue figure and `NaN` for every empty cell are
both actively wrong for a research knowledge base.

**c. Speed.** docx 4.0 ms vs 102.3 ms; xlsx 0.1 ms vs 10.0 ms; pptx 0.5 ms vs
8.8 ms (median of 5, same files).

**d. Dependency surface.** Removing MarkItDown pruned **12 packages**:
`markitdown`, `magika`, `mammoth`, `markdownify`, `openpyxl`, `lxml`,
`et-xmlfile`, `pandas`, `python-pptx`, `xlsxwriter`, `cobble`, `defusedxml`.

magika is the one that matters: MarkItDown constructed it eagerly, its ONNX
model ships as *package data*, and a frozen build that missed it broke **all**
office parsing while PDF and text kept working (PR #231). anydoc is a compiled
extension with no data files, so that entire failure mode is gone along with
the `_data_pkgs` entry that guarded it.

## 3. The `.rtf` pollution this also fixed

`.rtf` was not merely unsupported — it was **silently wrong**. RTF source is
ASCII, so it passed the unknown-extension strict-UTF-8 guard and was stored as
its own markup:

```
text.rtf   engine=plain_text   -> '{\rtf1\ansi\deff3\n{\fonttbl{\f0\froman...'
```

Control words went into the knowledge base as if they were prose, with no
error anywhere. anydoc now converts RTF properly; the durable invariant is
pinned by `tests/providers/test_parser_light_local_unsupported.py`, which
asserts control words never reach the caller **whatever** the backend state.

## 4. What it cost

**PowerPoint loses slide boundaries.** MarkItDown emitted
`<!-- Slide number: N -->` between slides. anydoc renders a slide title as an
`h2` and emits nothing structural for a title-less slide, so consecutive
title-less slides merge into an undifferentiated paragraph stream.

Two separate losses were conflated here at first, and only one is permanent:

**Slide numbers — not recoverable.** The slide anchor exists in anydoc's parser
(`pptx/mod.rs:99`) but is gated twice: once there, and once in the renderer,
whose `anchors.rs` states outright that *anchors nothing links to render
nothing*. Removing only the parser gate and rebuilding produced **byte-identical
output**, confirming the renderer gate is the binding one. `to_markdown()` takes
no options, so an opt-in would be a public API change across four bindings, and
upstream's accepted framing (their issue #26, fixed) is that *"Markdown has no
pages, so dropping the break itself is the right call"*. Nothing in this repo
consumed the marker anyway (whole-tree grep, py/ts/tsx).

**Slide boundaries — fixable, patch prepared.** This is the loss that actually
degrades quality: content from two slides reading as one. It is not the
pagination upstream declined — a slide is a *container* in the source model, and
`Block::Rule` is a separator anydoc already emits (for `<hr>`, rendered as
`---`), so the fix introduces no new concept and implies no page number. A patch
emitting it between slides in all three presentation parsers (`pptx`, `ppt`,
`odp` — all shared the defect) is verified against upstream's own suite: **191
passed, 0 failed**, snapshot delta **+6 `---` lines, +6 blank lines, 0
removals**. 33 source lines. Not yet submitted upstream; this migration does not
depend on it, and if it lands the loss goes away with a version bump.

## 5. Known upstream issues, and why they did not block

Four open anydoc issues touch spreadsheets. Each was reproduced locally against
**both** engines:

| Issue | anydoc | MarkItDown |
|-------|--------|------------|
| #27 percent format dropped (`7.5%` → `0.075`) | affected | **affected, plus scientific notation** |
| #9 hidden rows/columns rendered as visible | affected | affected |
| #8 merged spans clipped to populated range | affected | affected |
| #14 nested tables flattened | flattened but readable | **emits malformed markdown** |

Not one is a regression; in every case MarkItDown's failure mode is equal or
worse. #27 is the one worth watching for a research product — but the fix is
blocked two layers up: anydoc reads sheets via **calamine 0.36.1**, which
reduces every `numFmt` to a three-value `CellFormat` enum and keeps the raw
format string private. Fixing it needs a calamine API change *plus* an Excel
number-format renderer. Out of reach for a drop-in dependency swap.

## 6. Invariants (each has a test)

1. **The ingestion gate mirrors the parser; it is not an allow-list.**
   `docs.service.is_ingestible(filename, data)` accepts a file when its
   extension is in `SUPPORTED_EXTS` **or** its bytes decode as UTF-8 — the same
   rule as `LightLocalParser`'s unknown-extension fallback. Both callers use
   it: the directory scan (phase 3) and the upload endpoint. Any disagreement
   between them shows up as a file that uploads cleanly and then never becomes
   a document.

   This corrected two defects found while widening the tables:

   - `.htm` parsed fine but was absent from `SUPPORTED_EXTS`, so the scan
     skipped it silently while `.html` worked. Caught by the drift test on its
     first run, not by a user.
   - Source files (`.py`, `.go`, `.sh`), config and logs were skipped even
     though the parser reads any UTF-8 file as text — the KB was refusing
     content it could index perfectly well. They are now ingested. Only
     genuinely binary payloads (`.zip`, `.exe`, media) are refused, and the
     upload endpoint refuses them **loudly** with a 400 naming the file,
     instead of writing them and letting the scan drop them in silence. The
     frontend surfaces that reason rather than a generic "import failed".

   Note the blast radius: a KB bound to a source tree now ingests its text
   files. Hidden entries (including `.git`) were already excluded; large
   non-hidden vendor directories such as `node_modules` are not.
2. **`classify()` buckets by what a format is**, not by who can read it. ODF /
   RTF / EPUB are `office`; ODS is `spreadsheet`. A cloud plugin that claims
   `office` but cannot read them costs one failed attempt before
   `_runtime_fallback_async` demotes to LightLocal. Filing them under `text`
   would dodge that round-trip but lock them out of cloud parsing forever.
3. **Existing documents are re-parsed via `_RETIRED_ENGINE_FOR_KIND`.** This is
   the upgrade path, and it needed new machinery. `_run_rescan`'s Trigger 3
   compares **plugin ids**, and every LightLocal engine — `markitdown`,
   `plain_text`, `anydoc` — maps to `light_local`, so an engine swap *inside*
   the plugin is invisible to it. Without Trigger 4 the migration would never
   reach data already in a knowledge base:

   | already-ingested doc | recorded `parser_mode` | old status | requeued by |
   |---|---|---|---|
   | `.doc` `.ppt` `.xls` `.odt` | — | `failed` | Trigger 2 (unconditional) ✅ |
   | `.docx` `.xlsx` `.pptx` | `markitdown` | `ready` | **Trigger 4** — Trigger 3 sees no change |
   | `.rtf` (control-word pollution) | `plain_text` | `ready` | **Trigger 4** — same |

   `_ENGINE_TO_PLUGIN_ID` still maps `"markitdown"` so those rows resolve to a
   plugin rather than `None`, but that mapping alone requeues nothing.
   Entries in `_RETIRED_ENGINE_FOR_KIND` are permanent: an install can skip any
   number of releases, so the ability to recognise a stale engine must outlive
   the engine. The pass converges — a re-parse writes `anydoc`, which is not in
   the set.
4. **The office backend fails loudly.** anydoc raises a typed `ConvertError`
   (`Encrypted` / `Unsupported` / `Malformed` / `ResourceLimit` /
   `MissingPart`); `_parse_office` returns it as `metadata["error"]`, which the
   attachment pipeline surfaces as `error_message`. It must never degrade into
   echoing the source — that is the `.rtf` failure mode of §3.
5. **The markitdown tail stays out of the bundle.**
   `tests/test_pyinstaller_spec_bundles.py` asserts `anydoc` is in
   `hiddenimports` and that markitdown / mammoth / magika / markdownify /
   et_xmlfile are not.

## 7. Notes for the next change

- `openpyxl` is a **dev-only** dependency now — it builds the xlsx test
  fixture. It must not return to the runtime list; anydoc reads sheets in Rust.
- `parse_pool`'s process offload is still required, but only for **PDF**.
  pymupdf4llm is pure Python and GIL-bound; anydoc releases the GIL and runs in
  sub-millisecond time.
- The spec's `"beautifulsoup4"` hiddenimport was corrected to `"bs4"` — only a
  *module* name means anything to PyInstaller, so the old spelling collected
  nothing. It mattered little while MarkItDown imported bs4 anyway; with
  MarkItDown gone, html-to-markdown is its only requester.
