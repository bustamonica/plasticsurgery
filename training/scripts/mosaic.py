#!/usr/bin/env python3
"""Detect burned-in mosaic pixelation in clinical before/after photos.

Standing rule (captain, 2026-08-14): a pixelated or mosaic-damaged image is
dropped at ingest and never emitted. `censorship.py` was supposed to carry that
rule through its `detail-suppressed` heuristic and does not: measured 2026-08-25
on `aips` it flagged **0 of 182** images while `ingest.py` accepted 22 visibly
mosaicked pairs, and the same false negative had already been recorded on
`sanantonio` (24 emitted pairs), `marina` (`marina-17784-front`) and
`drmiroshnik` (`case55-front`). Two reproducible reasons, both in
`_find_detail_suppressed`: it discards any low-detail component touching the
frame edge, and a mosaic's own block edges carry high gradient energy, so
pixelation does not read as texture-free skin at all. This module is the
separate gate, not a patch to that one - keeping them apart is what lets each be
re-measured on its own terms.

WHAT SEPARATES MOSAIC FROM SMOOTH SKIN
--------------------------------------
Mosaic is piecewise-constant on a REGULAR GRID with hard steps between
neighbouring cells. Blown-out or soft-focus skin is also locally flat, which is
exactly why `censorship.py`'s flatness test false-positives on it (77 held
bayside pairs, 6 harrington pairs, both on plain skin or backdrop). Flatness
alone is therefore not the signal. Two things are required together:

  1. a grid of cells that are internally FLAT and differ from their neighbours
     by a hard STEP, searched over cell size and phase; and
  2. grid alignment: inside the candidate box, the image's gradient energy must
     concentrate on the cell boundaries. A soft edge crossing smooth skin
     produces one large step and nothing on the other grid lines, so its
     alignment ratio sits near 1; a mosaic puts a step on EVERY grid line.

Requirement 2 is what makes this usable. Without it a smooth arm at k=6 scores
as high as real mosaic (measured: `aips` case 31 `P3170821`, 31 stepped-flat
cells on plain skin, alignment ratio 1.63 against 6.2 for the case-22 arm).

MEASURED, 2026-08-26 (see `data/ba-viz-mosaic-detector-adopt/report.md`)
-----------------------------------------------------------------------
Positives, all clinic-published mosaic confirmed by eye:
  aips `_2` re-upload batch  39 of 44 images / 21 of 22 pairs
  sanantonio 8 mosaic cases   0 of 48 images  - NOT caught, see LIMITS
  marina-17784-front          0 of 2          - NOT caught, see LIMITS
  drmiroshnik-case55-front    0 of 2          - NOT caught, see LIMITS
Negatives, no false positive at this operating point:
  aips clean halves          0 of 140
  bayside `censorship.py` holds 0 of 152  (the smooth warm-backdrop family)
  harrington-176-front       0 of 2    (the smooth-skin family)

LIMITS - read before trusting a clean run
-----------------------------------------
This gate catches mosaic that is COARSE and HIGH-CONTRAST: cells of 6px and up
carrying a level step of 6 or more against their neighbours, over at least 5
cells. It does NOT catch the sanantonio/marina family - a few dozen cells of
skin-tone-on-skin-tone mosaic over a tattoo, whose neighbour steps measure 2-4
levels at the clinic's published 450px. At their published resolution those sit
below every threshold here that keeps the false-positive rate at zero, and the
measured separation is the wrong way round: sanantonio's best candidate scores
2.9 where a clean `aips` arm scores 2.9 too. One more measured blind spot: an 8px mosaic that is re-encoded OUT OF PHASE with
JPEG's own 8x8 DCT grid is smeared past detection (0 regions at quality 95, 37 at
quality 100 and 394 when the two grids happen to align). aips publishes an 8px
mosaic and is caught 39 of 44, so this is a degradation rather than a wall, but
it is why the gate is not a proof of absence.

Opening the images is still the only complete screen; a clean run from this
module is not evidence a clinic is uncensored, exactly as `censorship.py`'s
docstring says of itself.

RE-MEASUREMENT RECIPE (required before any threshold moves)
-----------------------------------------------------------
    python training/scripts/mosaic.py --corpus ~/firstmate/data/clinic-corpus
Re-run the positive and negative sets named above and report both; a threshold
change that is not accompanied by both halves of that table is not measured.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# --- cell geometry ---------------------------------------------------------
# Cell sizes actually observed on the corpus: 6px (sanantonio at 450px), 8px
# (aips at 750px), ~19px (aips arms), ~30px (marina). Below 5 the search starts
# colliding with JPEG's own 8x8 blocking on flat backdrop; above 48 a "cell"
# is a quarter of a small frame and the evidence is a region, not a mosaic.
CELL_MIN = 5
CELL_MAX = 48
# kmax also scales with the frame so a large image is not searched at cell sizes
# that could not be a mosaic in it.
CELL_MAX_FRACTION = 0.07
# Phase is searched at this many steps per cell size. A coarser search misses a
# grid outright (measured on marina at k=28: offset 4 of 9 reads as unflat).
PHASE_STEPS = 4

# --- what makes a cell a mosaic cell ---------------------------------------
# Peak-to-peak inside the cell, ignoring its 1px border (JPEG rings at a block
# boundary and that ring is not the cell's content).
FLAT_MAX = 2.5
# Level difference to the brightest-differing 4-neighbour. Mosaic over skin runs
# 6-30; a JPEG-blocked flat backdrop runs 1-3.
STEP_MIN = 6.0
# A lone stepped-flat cell is noise. 4 of the 3x3 neighbourhood (itself
# included) forces the evidence to be two-dimensional, which is what kills the
# 1-cell-wide strips the body/backdrop boundary produces.
MIN_NEIGHBOURS = 4
MIN_SPAN_CELLS = 2

# --- grid alignment --------------------------------------------------------
# |gradient| on the candidate's grid lines over |gradient| off them, measured in
# both axes and taken at the weaker one, inside the box padded by one cell.
ALIGN_PAD_CELLS = 1
# Two tiers, both measured to zero false positives on the negative sets above:
# a small mosaic must be strongly grid-aligned, a large one may be weaker
# because its size is itself evidence.
SMALL_MIN_CELLS, SMALL_MIN_ALIGN = 5, 3.5
LARGE_MIN_CELLS, LARGE_MIN_ALIGN = 14, 2.2


def _block_stats(gray: np.ndarray, k: int) -> "tuple[np.ndarray, np.ndarray]":
    """Per-pixel mean of the k x k block anchored there, and its inner range.

    Anchored filters make every phase a strided slice of the same two arrays,
    which is what keeps a full phase search affordable.
    """
    mean = cv2.boxFilter(gray, cv2.CV_32F, (k, k), anchor=(0, 0), normalize=True,
                         borderType=cv2.BORDER_REPLICATE)
    inner = max(1, k - 2)
    kernel = np.ones((inner, inner), np.uint8)
    lo = cv2.erode(gray, kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
    hi = cv2.dilate(gray, kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
    return mean, hi - lo


def _stepped_flat_cells(gray, k, oy, ox, mean_f, rng_f) -> "np.ndarray | None":
    """Cells of the (k, oy, ox) grid that are flat inside and stepped outside."""
    h, w = gray.shape
    ny, nx = (h - oy) // k, (w - ox) // k
    if ny < 3 or nx < 3:
        return None
    ys = oy + np.arange(ny) * k
    xs = ox + np.arange(nx) * k
    mean = mean_f[np.ix_(ys, xs)]
    border = 1 if k >= 5 else 0
    rng = rng_f[np.ix_(ys + border, xs + border)]
    dv = np.abs(np.diff(mean, axis=0))
    dh = np.abs(np.diff(mean, axis=1))
    step = np.zeros_like(mean)
    step[:-1] = np.maximum(step[:-1], dv)
    step[1:] = np.maximum(step[1:], dv)
    step[:, :-1] = np.maximum(step[:, :-1], dh)
    step[:, 1:] = np.maximum(step[:, 1:], dh)
    cells = (rng <= FLAT_MAX) & (step >= STEP_MIN)
    company = cv2.blur(cells.astype(np.float32), (3, 3),
                       borderType=cv2.BORDER_CONSTANT) * 9
    return cells & (company >= MIN_NEIGHBOURS)


def grid_alignment(gray, k, oy, ox, box) -> "tuple[float, float]":
    """|gradient| on the grid lines over |gradient| off them, per axis.

    ~1 means the box's edges fall wherever they like, which is what a soft edge
    on smooth skin looks like. A mosaic puts every edge on a grid line.
    """
    x, y, w, h = box
    pad = ALIGN_PAD_CELLS * k
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(gray.shape[1], x + w + pad), min(gray.shape[0], y + h + pad)
    sub = gray[y0:y1, x0:x1]
    if sub.shape[0] < 3 * k or sub.shape[1] < 3 * k:
        return 0.0, 0.0
    gx = np.abs(np.diff(sub, axis=1))
    gy = np.abs(np.diff(sub, axis=0))
    # the difference at index j spans (j, j+1), so a boundary at absolute
    # column ox + m*k is the difference at index ox + m*k - 1
    cols = np.zeros(gx.shape[1], bool)
    cols[(ox - 1 - x0) % k::k] = True
    rows = np.zeros(gy.shape[0], bool)
    rows[(oy - 1 - y0) % k::k] = True
    if cols.all() or not cols.any() or rows.all() or not rows.any():
        return 0.0, 0.0
    rx = float(gx[:, cols].mean() / max(gx[:, ~cols].mean(), 0.05))
    ry = float(gy[rows, :].mean() / max(gy[~rows, :].mean(), 0.05))
    return rx, ry


def _is_mosaic(cells: int, align: float) -> bool:
    return ((cells >= SMALL_MIN_CELLS and align >= SMALL_MIN_ALIGN)
            or (cells >= LARGE_MIN_CELLS and align >= LARGE_MIN_ALIGN))


def mosaic_regions(image: np.ndarray) -> "list[dict]":
    """Every mosaic region found, strongest first. Empty means clean.

    Each entry carries the pixel box, the cell size and the alignment ratio, so
    a caller can draw the evidence rather than take the verdict on trust.

    `cell_px` may be a DIVISOR of the mosaic's true cell size - a 5px grid tiles
    a 10px mosaic, and every cell of it is flat and stepped. It is evidence for a
    human looking at the box, not a claim about the censoring tool's setting.
    """
    if image is None or image.ndim != 3:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    kmax = int(min(CELL_MAX, max(2 * CELL_MIN, min(h, w) * CELL_MAX_FRACTION)))
    found: list[dict] = []
    for k in range(CELL_MIN, kmax + 1):
        mean_f, rng_f = _block_stats(gray, k)
        stride = max(1, k // PHASE_STEPS)
        for oy in range(0, k, stride):
            for ox in range(0, k, stride):
                cells = _stepped_flat_cells(gray, k, oy, ox, mean_f, rng_f)
                if cells is None or not cells.any():
                    continue
                n, _, stats, _ = cv2.connectedComponentsWithStats(
                    cells.astype(np.uint8), 8)
                for i in range(1, n):
                    cx, cy, cw, ch, area = (int(stats[i, j]) for j in range(5))
                    if area < SMALL_MIN_CELLS:
                        continue
                    if cw < MIN_SPAN_CELLS or ch < MIN_SPAN_CELLS:
                        continue
                    box = (ox + cx * k, oy + cy * k, cw * k, ch * k)
                    rx, ry = grid_alignment(gray, k, oy, ox, box)
                    align = min(rx, ry)
                    if not _is_mosaic(area, align):
                        continue
                    found.append({"x": box[0], "y": box[1], "w": box[2], "h": box[3],
                                  "cells": area, "cell_px": k, "alignment": round(align, 2)})
    found.sort(key=lambda d: (-d["cells"] * d["cell_px"] ** 2, -d["alignment"]))
    return found


def detect_mosaic(image: np.ndarray) -> "list[str]":
    """Reasons this BGR image carries burned-in mosaic. Empty means clean.

    Same shape of contract as `censorship.detect_censorship`, so `ingest.py`
    treats the two gates alike. Regions are merged into one reason per image:
    a mosaic drawn over a tattoo is often several disconnected blocks and
    listing each of them says nothing more than the first does.
    """
    regions = mosaic_regions(image)
    if not regions:
        return []
    top = regions[0]
    extra = f" (+{len(regions) - 1} more)" if len(regions) > 1 else ""
    return [
        f"mosaic pixelation at x={top['x']} y={top['y']} {top['w']}x{top['h']}px "
        f"({top['cell_px']}px cells, {top['cells']} of them, "
        f"grid alignment {top['alignment']}x){extra}"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Flag burned-in mosaic pixelation.")
    parser.add_argument("images", type=Path, nargs="*", help="Image files to check")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="Walk <corpus>/<clinic>/<pair>/{before,after}.* instead")
    parser.add_argument("--clinic", action="append", default=None,
                        help="Restrict --corpus to these clinics (repeatable)")
    parser.add_argument("--jsonl", type=Path, default=None,
                        help="Write one JSON record per flagged image here")
    args = parser.parse_args()

    paths: list[Path] = list(args.images)
    if args.corpus:
        for clinic in sorted(p for p in args.corpus.iterdir() if p.is_dir()):
            # `_staging/`, `.scraper-cache/` and a clinic's own raw/ + staging/
            # intake trees are not the finished corpus; see AGENTS.md.
            if clinic.name.startswith(("_", ".")):
                continue
            if args.clinic and clinic.name not in args.clinic:
                continue
            for pair in sorted(p.parent for p in clinic.glob("*/meta.json")):
                for stem in ("before", "after"):
                    paths.extend(sorted(pair.glob(f"{stem}.*")))
    if not paths:
        parser.error("no images: pass files or --corpus")

    flagged = 0
    records = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            print(f"SKIP   {path}: cannot read")
            continue
        reasons = detect_mosaic(image)
        if reasons:
            flagged += 1
            print(f"FLAG   {path}: " + "; ".join(reasons))
            records.append({"path": str(path), "regions": mosaic_regions(image)})
        else:
            print(f"clean  {path}")
    print(f"\n{flagged} of {len(paths)} flagged")
    if args.jsonl:
        import json
        args.jsonl.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
