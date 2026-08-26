#!/usr/bin/env python3
"""Render labeled contact sheets of scraped pairs for visual annotation.

The galleries do not document laterality (drkolker oblique/side) or any view
label at all (drdanielbarrett and most of the 2026-08 batch), so a human/agent
must look at the images and record view/clothing labels in an annotations JSON
file consumed by scrape_gallery.py --annotations. This tool turns the local
fetch cache into labeled contact sheets (case id + pair key under each tile) to
make that inspection fast. It never uploads anything; sheets are written
locally and must stay out of git (patient photos).

Two rendering modes:

- ``--side before`` (default): one tile per pair, the 'before' image only.
  Enough to classify front/oblique/side.
- ``--side both``: one cell per pair holding before|after adjacent, with a
  divider between them. Required for laterality calls: per AGENTS.md a
  left/right label needs a distinguishing feature (piercing, tattoo,
  asymmetry) visible in BOTH images of the pair, which can only be checked
  with the two side by side.

Composites are decoded and cropped through the same two seams the emit path
uses (``decode_pair_halves`` then ``finish_pair_halves``), so every clinic's
measured crop - the composite border/gutter, the caption band and seam trim,
the fractional and pixel bottom crops, the grid gutter, the watermark
postprocess - is already applied to what you label. That is also what
``--min-dim`` measures, so a pair the 400px floor would reject after cropping
is never put in front of you.

Usage:

    python3 training/scripts/annotate_contact_sheets.py \
        --clinic marina --side both \
        --cache-dir <corpus>/.scraper-cache \
        --out-dir <corpus>/../annotation-sheets/marina \
        --cases 6155,6171
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_gallery as sg  # noqa: E402

TILE_BG = (24, 24, 24)
DIVIDER = (255, 60, 60)
LABEL_H = 22


def _cached(cfg: sg.ClinicConfig, cache_dir: Path, url: str) -> bytes | None:
    full_url = url if url.startswith("http") else cfg.base_url + url
    path = cache_dir / sg.image_cache_key(cfg.slug, full_url)
    return path.read_bytes() if path.exists() else None


def _pair_images(cfg: sg.ClinicConfig, cache_dir: Path,
                 pair: sg.ImagePair) -> tuple[bytes, bytes] | None:
    """(before, after) image bytes for a pair, decoded as the emit path does."""
    data = _cached(cfg, cache_dir, pair.before_url)
    if data is None:
        return None
    if pair.grid_shape is not None or pair.split_composite:
        try:
            halves = sg.decode_pair_halves(cfg, pair, data)
        except ValueError:
            return None
    else:
        after = _cached(cfg, cache_dir, pair.after_url)
        if after is None:
            return None
        halves = (data, after)
    before_data, after_data, _ = sg.finish_pair_halves(cfg, *halves)
    return before_data, after_data


def _min_dimension(images: tuple[bytes, bytes]) -> int:
    sizes = []
    for data in images:
        with Image.open(io.BytesIO(data)) as im:
            sizes.append(min(im.size))
    return min(sizes)


def collect_tiles(cfg: sg.ClinicConfig, cache_dir: Path, cases: set[str] | None,
                  both: bool, min_dim: int) -> list[tuple[str, list[bytes]]]:
    """(label, [image bytes]) per pair whose images are all in the cache."""
    fetcher = sg.PoliteFetcher(cache_dir, offline=True)
    tiles = []
    skipped_small = 0
    for case in sg.collect_cases(cfg, fetcher):
        if cases is not None and case.case_id not in cases:
            continue
        for pair in case.pairs:
            try:
                images = _pair_images(cfg, cache_dir, pair)
            except Exception as e:  # unreadable/undecodable cache entry
                print(f"WARN {case.case_id}:{pair.key}: {e}")
                continue
            if images is None:
                continue
            # Annotating a pair ingest.py would reject anyway is wasted work.
            if min_dim and _min_dimension(images) < min_dim:
                skipped_small += 1
                continue
            label = f"{case.case_id}:{pair.key}"
            tiles.append((label, list(images) if both else [images[0]]))
    if skipped_small:
        print(f"{skipped_small} pair(s) skipped: below --min-dim {min_dim}")
    return tiles


def _paste(sheet: Image.Image, data: bytes, box_x: int, box_y: int,
           tile: int) -> None:
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((tile, tile))
        sheet.paste(im, (box_x + (tile - im.width) // 2,
                         box_y + (tile - im.height) // 2))


def render_sheets(tiles: list[tuple[str, list[bytes]]], out_dir: Path, tile: int,
                  cols: int, rows_per_sheet: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    per_cell = max(len(t[1]) for t in tiles)  # 1 (before) or 2 (before|after)
    cell_w = tile * per_cell
    per_sheet = cols * rows_per_sheet
    count = 0
    for start in range(0, len(tiles), per_sheet):
        batch = tiles[start : start + per_sheet]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell_w, rows * (tile + LABEL_H)), TILE_BG)
        draw = ImageDraw.Draw(sheet)
        for i, (label, images) in enumerate(batch):
            x, y = (i % cols) * cell_w, (i // cols) * (tile + LABEL_H)
            for j, data in enumerate(images):
                try:
                    _paste(sheet, data, x + j * tile, y, tile)
                except Exception as e:  # unreadable tile: keep the label, go on
                    print(f"WARN cannot render {label}[{j}]: {e}")
            if len(images) > 1:
                draw.line([(x + tile, y), (x + tile, y + tile)], fill=DIVIDER)
            draw.line([(x, y + tile + LABEL_H - 1),
                       (x + cell_w, y + tile + LABEL_H - 1)], fill=(70, 70, 70))
            draw.text((x + 4, y + tile + 3), label, fill=(255, 220, 0))
        count += 1
        out = out_dir / f"sheet_{count:02d}.jpg"
        sheet.save(out, "JPEG", quality=90)
        print(out)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinic", required=True, choices=sorted(sg.CLINICS))
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tile", type=int, default=150)
    parser.add_argument("--cols", type=int, default=6)
    parser.add_argument("--rows", type=int, default=0,
                        help="Rows per sheet (default: 30 // cols)")
    parser.add_argument("--side", choices=("before", "both"), default="before",
                        help="'both' renders before|after adjacent, needed for "
                             "laterality calls")
    parser.add_argument("--cases", default=None,
                        help="Comma-separated case ids to restrict to")
    parser.add_argument("--min-dim", type=int, default=0,
                        help="Skip pairs whose smaller image side is under this "
                             "(use ingest.py's MIN_DIMENSION to skip pairs that "
                             "would be rejected anyway)")
    args = parser.parse_args()

    cases = set(args.cases.split(",")) if args.cases else None
    tiles = collect_tiles(sg.CLINICS[args.clinic], args.cache_dir, cases,
                          args.side == "both", args.min_dim)
    print(f"{len(tiles)} pair(s) with cached images")
    if tiles:
        render_sheets(tiles, args.out_dir, args.tile, args.cols,
                      args.rows or max(1, 30 // args.cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())
