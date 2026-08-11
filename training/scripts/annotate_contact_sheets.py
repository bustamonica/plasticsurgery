#!/usr/bin/env python3
"""Render labeled contact sheets of scraped 'before' images for visual annotation.

The galleries do not document laterality (drkolker oblique/side) or any view
label at all (drdanielbarrett), so a human/agent must look at the images and
record view/clothing labels in an annotations JSON file consumed by
scrape_gallery.py --annotations. This tool turns the local fetch cache into
labeled contact sheets (case id + pair key under each tile) to make that
inspection fast. It never uploads anything; sheets are written locally and
must stay out of git (patient photos).

Usage:

    python3 training/scripts/annotate_contact_sheets.py \
        --clinic drkolker \
        --cache-dir <raw>/.scraper-cache \
        --out-dir /private/annotation-sheets/drkolker
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_gallery as sg  # noqa: E402

TILE_BG = (24, 24, 24)
LABEL_H = 22


def collect_tiles(cfg: sg.ClinicConfig, cache_dir: Path) -> list[tuple[str, Path]]:
    """(label, image path) for every pair's before image in the cache."""
    fetcher = sg.PoliteFetcher(cache_dir, offline=True)
    tiles = []
    for case in sg.collect_cases(cfg, fetcher):
        for pair in case.pairs:
            url = pair.before_url
            full_url = url if url.startswith("http") else cfg.base_url + url
            path = cache_dir / sg.image_cache_key(cfg.slug, full_url)
            if path.exists():
                tiles.append((f"{case.case_id}:{pair.key}", path))
    return tiles


def render_sheets(tiles: list[tuple[str, Path]], out_dir: Path, tile: int,
                  cols: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_per_sheet = max(1, 30 // cols)
    per_sheet = cols * rows_per_sheet
    count = 0
    for start in range(0, len(tiles), per_sheet):
        batch = tiles[start : start + per_sheet]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile, rows * (tile + LABEL_H)), TILE_BG)
        draw = ImageDraw.Draw(sheet)
        for i, (label, path) in enumerate(batch):
            x, y = (i % cols) * tile, (i // cols) * (tile + LABEL_H)
            try:
                with Image.open(path) as im:
                    im.thumbnail((tile, tile))
                    sheet.paste(im.convert("RGB"), (x, y))
            except Exception as e:  # unreadable tile: keep the label, go on
                print(f"WARN cannot render {path}: {e}")
            draw.text((x + 4, y + tile + 3), label, fill=(255, 220, 0))
        count += 1
        out = out_dir / f"sheet_{count:02d}.jpg"
        sheet.save(out, "JPEG", quality=88)
        print(out)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinic", required=True, choices=sorted(sg.CLINICS))
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tile", type=int, default=150)
    parser.add_argument("--cols", type=int, default=6)
    args = parser.parse_args()

    tiles = collect_tiles(sg.CLINICS[args.clinic], args.cache_dir)
    print(f"{len(tiles)} pair(s) with cached before images")
    if tiles:
        render_sheets(tiles, args.out_dir, args.tile, args.cols)
    return 0


if __name__ == "__main__":
    sys.exit(main())
