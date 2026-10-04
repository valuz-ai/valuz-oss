#!/usr/bin/env python3
"""Render a deck with dsoffice and compose labelled thumbnail grids for a quick overview.

    valuz-python thumbnails.py deck.pptx --output-dir thumbs
    valuz-python thumbnails.py deck.pptx --output-dir thumbs --slides 4-9 --cols 3
    valuz-python thumbnails.py deck.pptx --output-dir thumbs --from-render preview-v1

--output-dir must not exist yet. It receives slides/ (one PNG per slide plus
dsoffice's manifest.json) and grid-01.png, grid-02.png, ... with up to
cols x rows slides each, every cell labelled with its slide number ("hidden"
is added for hidden slides). --from-render reuses an existing dsoffice render
directory instead of rendering again. Prints a JSON summary (grid paths, the
slides on each grid, per-slide PNGs, missingFonts). Exit 1 if rendering fails.

Grids are for orientation (template inventory, deck flow, obvious layout
breaks). Judge fine detail - clipping, small text, chart labels - on the
per-slide PNGs, rendered at a higher --dpi when needed.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MAX_PAGES_PER_RENDER = 100
BACKGROUND = (226, 229, 234)
BORDER = (150, 156, 166)
LABEL = (31, 41, 55)


def potx_as_pptx(path: Path) -> bytes:
    """The bytes of a .potx template re-labelled as a presentation."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"presentationml.template.main+xml", b"presentationml.presentation.main+xml"
                )
            out.writestr(item, data)
    return buffer.getvalue()


def deck_info(path: Path) -> tuple[float | None, int | None, set[int]]:
    """Slide width in inches, slide count and hidden slide numbers (pptx only)."""
    if path.suffix.lower() != ".pptx":
        return None, None, set()
    from pptx import Presentation

    prs = Presentation(str(path))
    hidden = {n for n, slide in enumerate(prs.slides, 1) if slide._element.get("show") == "0"}
    return prs.slide_width / 914400, len(prs.slides), hidden


def parse_numbers(spec: str) -> list[int]:
    numbers: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start, end = (int(x) for x in chunk.split("-", 1))
            numbers.extend(range(min(start, end), max(start, end) + 1))
        elif chunk:
            numbers.append(int(chunk))
    return sorted(set(numbers))


def run_render(
    dsoffice: str, source: Path, out_dir: Path, pages: list[int] | None, dpi: int
) -> dict:
    argv = [
        dsoffice,
        "render",
        "--input",
        str(source),
        "--output-dir",
        str(out_dir),
        "--dpi",
        str(dpi),
    ]
    if pages:
        argv += ["--pages", ",".join(map(str, pages))]
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(
            f"dsoffice render failed ({done.returncode}): "
            f"{done.stdout.strip()} {done.stderr.strip()}"
        )
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def render(
    source: Path, slides_dir: Path, pages: list[int] | None, count: int | None, dpi: int
) -> list[dict]:
    dsoffice = shutil.which("dsoffice")
    if dsoffice is None:
        raise RuntimeError("dsoffice is not on PATH; cannot render slides")
    wanted = pages or (list(range(1, count + 1)) if count else None)
    if wanted is None or len(wanted) <= MAX_PAGES_PER_RENDER:
        return [run_render(dsoffice, source, slides_dir, pages, dpi)]
    # dsoffice renders at most 100 pages per call: render in batches into sub-directories.
    slides_dir.mkdir(parents=True)
    manifests = []
    for start in range(0, len(wanted), MAX_PAGES_PER_RENDER):
        batch = wanted[start : start + MAX_PAGES_PER_RENDER]
        manifests.append(
            run_render(dsoffice, source, slides_dir / f"batch-{batch[0]:04d}", batch, dpi)
        )
    return manifests


def label_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def compose(
    images: list[tuple[int, Path]],
    hidden: set[int],
    out_dir: Path,
    cols: int,
    rows: int,
    cell_width: int,
) -> list[dict]:
    pad, label_height = 14, 30
    font = label_font(20)
    grids = []
    per_grid = cols * rows
    for grid_index in range(math.ceil(len(images) / per_grid)):
        chunk = images[grid_index * per_grid : (grid_index + 1) * per_grid]
        thumbs = []
        for number, path in chunk:
            with Image.open(path) as image:
                image = image.convert("RGB")
                height = round(image.height * cell_width / image.width)
                thumbs.append((number, image.resize((cell_width, height), Image.LANCZOS)))
        cell_height = max(t.height for _, t in thumbs)
        used_cols = min(cols, len(thumbs))
        used_rows = math.ceil(len(thumbs) / cols)
        width = pad + used_cols * (cell_width + pad)
        height = pad + used_rows * (label_height + cell_height + pad)
        canvas = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        for position, (number, thumb) in enumerate(thumbs):
            x = pad + (position % cols) * (cell_width + pad)
            y = pad + (position // cols) * (label_height + cell_height + pad)
            text = f"Slide {number}" + ("  (hidden)" if number in hidden else "")
            draw.text((x, y + 4), text, fill=LABEL, font=font)
            top = y + label_height
            canvas.paste(thumb, (x, top))
            draw.rectangle([x - 1, top - 1, x + thumb.width, top + thumb.height], outline=BORDER)
        path = out_dir / f"grid-{grid_index + 1:02d}.png"
        canvas.save(path)
        grids.append({"path": str(path.resolve()), "slides": [n for n, _ in chunk]})
    return grids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path, help=".pptx or .potx (also .pdf / .odp) to preview")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="new directory for slides/ and grid-NN.png"
    )
    parser.add_argument("--slides", help="1-based slides to include, e.g. 1-12 or 2,5,9")
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument(
        "--rows", type=int, default=4, help="rows per grid image before starting a new one"
    )
    parser.add_argument("--cell-width", type=int, default=480, help="thumbnail width in pixels")
    parser.add_argument(
        "--dpi", type=int, help="render resolution (default: enough for --cell-width)"
    )
    parser.add_argument(
        "--from-render",
        type=Path,
        help="existing dsoffice render dir (with manifest.json) to reuse",
    )
    args = parser.parse_args(argv)
    if args.cols < 1 or args.rows < 1 or args.cell_width < 64:
        parser.error("--cols/--rows must be >= 1 and --cell-width >= 64")
    if args.output_dir.exists():
        parser.error(f"--output-dir must be a new directory: {args.output_dir}")

    args.output_dir.mkdir(parents=True)
    source = args.input
    if source.suffix.lower() == ".potx":  # neither python-pptx nor dsoffice reads templates
        source = args.output_dir / (source.stem + "-as-pptx.pptx")
        source.write_bytes(potx_as_pptx(args.input))
    slide_width_in, count, hidden = deck_info(source)
    pages = parse_numbers(args.slides) if args.slides else None
    dpi = args.dpi or max(
        24, min(144, math.ceil(1.5 * args.cell_width / (slide_width_in or 13.333)))
    )
    try:
        if args.from_render:
            manifests = [
                json.loads((args.from_render / "manifest.json").read_text(encoding="utf-8"))
            ]
        else:
            manifests = render(source, args.output_dir / "slides", pages, count, dpi)
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1
    images = sorted(
        (int(item["page"]), Path(item["path"]))
        for manifest in manifests
        for item in manifest.get("images", [])
        if not pages or int(item["page"]) in pages
    )
    if not images:
        print(json.dumps({"status": "error", "error": "render produced no slide images"}))
        return 1
    grids = compose(images, hidden, args.output_dir, args.cols, args.rows, args.cell_width)
    missing = sorted({font for manifest in manifests for font in manifest.get("missingFonts", [])})
    summary = {
        "status": "ok",
        "grids": grids,
        "slide_images": {str(n): str(p) for n, p in images},
        "dpi": manifests[0].get("dpi"),
        "missingFonts": missing,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
