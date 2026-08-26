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

MEASURED, 2026-08-26 (full working in `data/ba-viz-mosaic-detector-adopt/report.md`)
------------------------------------------------------------------------------
Positives, clinic-published mosaic confirmed by eye:
  aips `_2` re-upload batch  29 of 44 images / 18 of 22 pairs
  sanantonio 8 mosaic cases   0 of 48 images  - NOT caught, see LIMITS
  marina-17784-front          0 of 2          - NOT caught, see LIMITS
Negatives, no false positive at this operating point:
  aips clean halves          0 of 140
  bayside `censorship.py` holds 0 of 152  (the smooth warm-backdrop family)
  harrington-176-front       0 of 2    (the smooth-skin family)
Corpus sweep, 9,442 images over 4,721 finished pairs: 16 images flagged in 14
pairs across 3 clinics. Every one was opened: 8 are real clinic mosaic
(gallatin 6, ciaravino 2, both censored tattoos at the frame edge) and 8 are
drdanielbarrett watermark lettering, the one false-positive family that
survives. Precision 50% by hand count; see the report for the per-clinic table.

WHY THE THRESHOLDS SIT WHERE THEY DO
------------------------------------
Every constant below was moved only with the whole table above re-measured.
Three findings shaped them, each from a family the sweep turned up:
  * STEP_MIN cannot go below 6. At 4 the aips recall barely moves (38 of 44)
    while the aips clean halves pick up 8 false positives, and sanantonio still
    scores zero - so the sanantonio family is not a threshold away.
  * A candidate must sit ON THE BODY, or a flat studio backdrop beside a bright
    body edge is read as a tiling: 38 blaine images (black backdrop), 23
    drrohrich (blue), 29 swan, 22 charlotte, all false.
  * SPREAD_MAX is what separates a mosaic from a burned-in watermark. Without
    it the sweep flags 47 images and 39 of them are false.

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

from censorship import body_silhouette, skin_mask

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
# A cluster must be at least this many cells across in BOTH directions. 2 admits
# a one-cell-wide strip along a body/backdrop edge and the stem of a burned-in
# letter; a mosaic tiles an area.
MIN_SPAN_CELLS = 3
# The cluster's own cell levels must vary. A mosaic hides something, so its cells
# take many values; a strip of flat backdrop beside a body edge takes one, and
# steps only because of the edge. Measured as the std of the cluster's cell means.
SPREAD_MIN = 3.0
# ...but only so far. A mosaic hides detail WITHIN one material - skin, or ink on
# skin - so its cells sit within tens of levels of each other. A cluster whose
# cell levels span much more than that is not one tiling but two materials
# meeting: a burned-in watermark stroke against skin (drdanielbarrett), a blue
# studio backdrop against a shoulder (drrohrich), a white panel divider against a
# torso (drteitelbaum). Measured on the corpus: real mosaic 3.7-25, those three
# families 12-88.
SPREAD_MAX = 20.0

# --- grid alignment --------------------------------------------------------
# |gradient| on the candidate's grid lines over |gradient| off them, measured in
# both axes and taken at the weaker one, inside the box padded by one cell.
ALIGN_PAD_CELLS = 1
# The alignment ratio is a ratio, so it means nothing when its denominator is a
# near-zero gradient: a black or flat studio backdrop divides by noise and scores
# arbitrarily high. Below this the candidate is rejected rather than scored.
ALIGN_OFF_GRID_FLOOR = 0.5
# The region has to be ON THE BODY, as a fraction of its own area. Mosaic on the
# backdrop is a clinic watermark's problem, not this gate's, and the two studio
# backdrops that dominate the false positives here (drrohrich's blue, blaine's
# black) are excluded by construction. Uses censorship.py's silhouette so there
# is one definition of "body" in the pipeline, with its known limits.
MIN_ON_BODY = 0.5
# Two tiers, both measured to zero false positives on the negative sets above:
# a small mosaic must be strongly grid-aligned, a large one may be weaker
# because its size is itself evidence.
# Frames longer than this are searched at an integer reduction (see mosaic_regions).
WORK_LONG_EDGE = 1200
SMALL_MIN_CELLS, SMALL_MIN_ALIGN = 5, 3.5
LARGE_MIN_CELLS, LARGE_MIN_ALIGN = 14, 2.2


def _block_stats(gray8: np.ndarray, k: int) -> "tuple[np.ndarray, np.ndarray]":
    """Per-pixel mean of the k x k block anchored there, and its inner range.

    Anchored filters make every phase a strided slice of the same two arrays,
    which is what keeps a full phase search affordable. Morphology runs on the
    uint8 image rather than a float copy: same result on integer pixel values,
    several times the throughput, and the sweep is ~90k images.
    """
    mean = cv2.boxFilter(gray8, cv2.CV_32F, (k, k), anchor=(0, 0), normalize=True,
                         borderType=cv2.BORDER_REPLICATE)
    inner = max(1, k - 2)
    kernel = np.ones((inner, inner), np.uint8)
    lo = cv2.erode(gray8, kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
    hi = cv2.dilate(gray8, kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
    return mean, hi.astype(np.float32) - lo.astype(np.float32)


def _stepped_flat_cells(gray, k, oy, ox, mean_f, rng_f):
    """Cells of the (k, oy, ox) grid that are flat inside and stepped outside,
    with the grid's cell means alongside."""
    h, w = gray.shape
    ny, nx = (h - oy) // k, (w - ox) // k
    if ny < 3 or nx < 3:
        return None, None
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
    return cells & (company >= MIN_NEIGHBOURS), mean


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
    off_x = float(gx[:, ~cols].mean())
    off_y = float(gy[~rows, :].mean())
    if off_x < ALIGN_OFF_GRID_FLOOR or off_y < ALIGN_OFF_GRID_FLOOR:
        return 0.0, 0.0  # nothing to be aligned against; see the constant
    return float(gx[:, cols].mean() / off_x), float(gy[rows, :].mean() / off_y)


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
    gray8 = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # A frame much larger than the corpus norm is searched at a reduced scale.
    # Cell size scales with the frame, so a 2560px gallery export whose mosaic
    # runs 60-100px is OUT of the CELL_MAX range at native resolution and only
    # comes into it here; the sweep also costs ~9x less. The reported boxes are
    # mapped back to the original pixel grid.
    scale = 1
    while max(gray8.shape) // (scale + 1) >= WORK_LONG_EDGE:
        scale += 1
    if scale > 1:
        gray8 = cv2.resize(gray8, (gray8.shape[1] // scale, gray8.shape[0] // scale),
                           interpolation=cv2.INTER_AREA)
    gray = gray8.astype(np.float32)
    h, w = gray.shape
    kmax = int(min(CELL_MAX, max(2 * CELL_MIN, min(h, w) * CELL_MAX_FRACTION)))
    found: list[dict] = []
    for k in range(CELL_MIN, kmax + 1):
        mean_f, rng_f = _block_stats(gray8, k)
        stride = max(1, k // PHASE_STEPS)
        for oy in range(0, k, stride):
            for ox in range(0, k, stride):
                cells, cell_mean = _stepped_flat_cells(gray, k, oy, ox, mean_f, rng_f)
                if cells is None or not cells.any():
                    continue
                n, labels, stats, _ = cv2.connectedComponentsWithStats(
                    cells.astype(np.uint8), 8)
                for i in range(1, n):
                    cx, cy, cw, ch, area = (int(stats[i, j]) for j in range(5))
                    if area < SMALL_MIN_CELLS:
                        continue
                    if cw < MIN_SPAN_CELLS or ch < MIN_SPAN_CELLS:
                        continue
                    spread = float(cell_mean[labels == i].std())
                    if not SPREAD_MIN <= spread <= SPREAD_MAX:
                        continue
                    box = (ox + cx * k, oy + cy * k, cw * k, ch * k)
                    rx, ry = grid_alignment(gray, k, oy, ox, box)
                    align = min(rx, ry)
                    if not _is_mosaic(area, align):
                        continue
                    found.append({"x": box[0] * scale, "y": box[1] * scale,
                                  "w": box[2] * scale, "h": box[3] * scale,
                                  "cells": area, "cell_px": k * scale,
                                  "alignment": round(align, 2),
                                  "spread": round(spread, 1)})
    if not found:
        return []
    body = body_silhouette(skin_mask(image))
    on_body = []
    for d in found:
        window = body[d["y"]:d["y"] + d["h"], d["x"]:d["x"] + d["w"]]
        if window.size == 0:
            continue
        d["on_body"] = round(float(window.mean()), 2)
        if d["on_body"] >= MIN_ON_BODY:
            on_body.append(d)
    on_body.sort(key=lambda d: (-d["cells"] * d["cell_px"] ** 2, -d["alignment"]))
    return on_body


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
