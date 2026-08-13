#!/usr/bin/env python3
"""Detect censorship and burned-in annotation in clinical before/after photos.

Censored pairs are actively harmful training signal, not merely wasted ones: the
v1 LoRA learned to reproduce a clinic's blur bands at intermediate checkpoints
(`data/ba-viz-lora-v1/report.md` line 78). This module is the automated half of
QA step 10 in the collection ask (`ba-viz-clinic-ask-24/report.md` section 5.3):
reject the pair, never crop or repair it.

Three deterministic heuristics run over the same OpenCV/NumPy stack
`deidentify.py` already depends on. Every one of them is anchored on a skin
silhouette, so a clinic watermark burned into the backdrop -- which the corpus
keeps, and which the v1 model reproduced faithfully -- is out of scope by
construction:

  opaque-mark        a geometrically regular, flat, non-skin patch sitting on
                     the body: sticker circles over the nipples, censorship
                     bars, solid arrowheads.
  detail-suppressed  a patch of body whose texture has collapsed relative to the
                     rest of the body, bounded by a hard detail edge: blur bands
                     and mosaic pixelation.
  burned-in-graphic  ink in a colour photography does not produce on a lit
                     torso: arrows, measurement rules, date stamps and captions
                     drawn in a saturated colour over the body.

Thresholds are tuned against real corpus imagery, not invented. Measured
2026-08-13 over `~/firstmate/data/clinic-corpus/`:

  false positives   6 of 1782 clinic-published images under `*/raw/` (0.34%).
                    All six are drdanielbarrett webp exports so heavily retouched
                    and upscaled that patches of torso carry no texture at all;
                    see BLUR_MAX_BODY_FRACTION for how far that is already
                    suppressed.
  sixsurgery        97 of 117 cached gallery images (every one carries opaque
                    circles over the nipples), essentially all via opaque-mark
  synthetic         a real corpus image with one artefact drawn on, 80 samples
                    each: arrow 80/80, mosaic band 72/80, date stamp 72/80,
                    blur band 49/80, black censorship bar 47/80

Recall is deliberately traded for precision. A false accept still meets the human
reviewer at QA step 10; a false reject silently discards consented data that
cannot be re-collected. Nothing here replaces that human pass.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# --- Skin silhouette -------------------------------------------------------
# Chai & Ngan YCrCb bounds, widened slightly at both ends: the corpus spans very
# pale (drkolker) to deeply tanned (sixsurgery) subjects under clinic lighting.
SKIN_Y_MIN = 50
SKIN_CR = (130, 185)
SKIN_CB = (75, 132)
MIN_SKIN_FRACTION = 0.10  # below this the frame is not a torso photo; skip

# --- opaque-mark -----------------------------------------------------------
# An occlusion worth rejecting is large: equivalent diameter >= 7% of the short
# edge. Below that the rule starts picking up areolae and moles (measured).
MARK_MIN_DIAMETER_FRACTION = 0.07
MARK_MAX_COLOR_STD = 6.0     # per-channel Lab std inside the patch; a fill is flat
MARK_MIN_COLOR_STEP = 15.0   # Lab distance from the skin just outside it
MARK_MIN_CIRCULARITY = 0.85  # 4*pi*A/P^2; a filled disc is ~1.0
MARK_MIN_RECTANGULARITY = 0.91  # A/bbox; a bar is ~1.0
MARK_MIN_RING_SKIN = 0.50    # half the surroundings must be body, not backdrop
# Deliberately NOT excluded: a patch whose colour matches the backdrop. That rule
# reads naturally ("the backdrop showing through an arm/torso gap") but costs the
# commonest censorship style of all, a black bar on a black studio backdrop.
# Measured A/B: synthetic-bar recall 19/60 with the rule, 39/60 without, and zero
# false rejections either way. The shape gate carries that weight instead.

# --- detail-suppressed -----------------------------------------------------
BLUR_MIN_DIAMETER_FRACTION = 0.12  # a censorship band spans a good part of the chest
BLUR_MAX_DETAIL_RATIO = 0.40       # patch detail vs the body's median detail
BLUR_MIN_DETAIL_STEP = 4.0         # surrounding detail / patch detail
BLUR_MIN_SKIN_LIKE = 0.60          # it is still skin-coloured, just smoothed
# A censorship band is a minority of the body, and the textured skin around it is
# what makes it legible as one. drdanielbarrett publishes a heavily retouched and
# upscaled gallery whose torsos are largely texture-free already; measured, those
# images run 0.28-0.40 of the silhouette while a band drawn over a real photo runs
# 0.14-0.23, so 0.25 separates them. Above it the comparison means nothing and the
# rule stands down rather than rejecting a whole clinic; the human pass at QA
# step 10 covers what it lets through.
BLUR_MAX_BODY_FRACTION = 0.25      # of the silhouette, texture-free in total
BLUR_MAX_PATCH_FRACTION = 0.25     # of the silhouette, in one patch

# --- burned-in-graphic -----------------------------------------------------
# Ink is a colour photography does not produce on a lit torso, so this rule keys
# on saturation alone. Neutrals are deliberately NOT ink: blown-out white is a
# specular highlight on skin as often as it is a caption, and dark neutral is hair
# or deep shadow. Including them cost 158 false rejections in 1228 (measured), and
# a solid white or black bar is caught by opaque-mark anyway, which keys on shape.
GRAPHIC_MIN_INK_FRACTION = 0.004  # of the skin silhouette, per cluster
# Chroma of the most saturated skin in the corpus peaks at 82 (sanantonio's warm
# grade, 99.5th percentile inside the body silhouette); 88 clears it. Pure red,
# yellow, green, blue and magenta all sit at 97 or above, so annotation ink in
# any of those is still caught. Cyan (80) is not: an accepted blind spot.
GRAPHIC_MIN_CHROMA = 88.0
GRAPHIC_CLUSTER_GAP = 9           # px; glyphs of one stamp cluster together


def skin_mask(image: np.ndarray) -> np.ndarray:
    """Binary mask of skin-toned pixels (uint8, 0/1). Input is BGR."""
    ycrcb = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycrcb[:, :, 0], ycrcb[:, :, 1], ycrcb[:, :, 2]
    mask = (
        (y > SKIN_Y_MIN)
        & (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1])
        & (cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1])
    ).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def body_silhouette(skin: np.ndarray) -> np.ndarray:
    """Largest skin region with its holes filled - everything the torso covers.

    Anything an overlay hides is a hole in the skin mask, so filling the holes is
    what lets a bar or sticker be found as its own region instead of merging with
    the backdrop it happens to share a colour with.
    """
    n, labels, stats, _ = cv2.connectedComponentsWithStats(skin, 8)
    if n <= 1:
        return skin
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    body = (labels == largest).astype(np.uint8)
    outside = (1 - body).astype(np.uint8)
    pad = np.zeros((body.shape[0] + 2, body.shape[1] + 2), np.uint8)
    cv2.floodFill(outside, pad, (0, 0), 2)
    body[outside == 1] = 1  # unreached by the flood => enclosed hole
    return body


def backdrop_color(lab: np.ndarray) -> np.ndarray:
    """Median Lab colour of the outer frame - the studio backdrop."""
    h, w = lab.shape[:2]
    band = max(2, int(min(h, w) * 0.02))
    edges = np.concatenate([
        lab[:band].reshape(-1, 3), lab[-band:].reshape(-1, 3),
        lab[:, :band].reshape(-1, 3), lab[:, -band:].reshape(-1, 3),
    ])
    return np.median(edges, axis=0)


def _detail_field(image: np.ndarray) -> np.ndarray:
    """Local high-frequency energy: how much texture each neighbourhood carries.

    Pooled with a median rather than a mean so that mosaic pixelation reads as
    texture-free: a mosaic is flat everywhere except on its block grid, and a
    mean would let those sparse block edges disguise it as ordinary skin.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    energy = np.clip(np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)), 0, 255).astype(np.uint8)
    return cv2.medianBlur(energy, 9).astype(np.float32)


def _components(mask: np.ndarray, open_kernel: int = 5):
    """Connected components of `mask`, opened first to drop speckle."""
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((open_kernel, open_kernel), np.uint8))
    return cv2.connectedComponentsWithStats(cleaned, 8)


def _touches_border(x: int, y: int, bw: int, bh: int, w: int, h: int) -> bool:
    return x == 0 or y == 0 or x + bw >= w or y + bh >= h


def _ring(mask: np.ndarray, inner: int = 3, outer: int = 13) -> np.ndarray:
    """Band of pixels just outside `mask`, used to sample what surrounds it."""
    grow = cv2.dilate(mask, np.ones((outer, outer), np.uint8))
    keep = cv2.dilate(mask, np.ones((inner, inner), np.uint8))
    return (grow - keep).astype(np.uint8)


def _equivalent_diameter_fraction(area: int, h: int, w: int) -> float:
    return float(np.sqrt(4.0 * area / np.pi) / min(h, w))


def _find_opaque_marks(image, lab, skin, body) -> list[str]:
    """Sticker circles, censorship bars, solid arrowheads."""
    h, w = image.shape[:2]
    n, labels, stats, _ = _components(((skin == 0) & (body == 1)).astype(np.uint8))
    reasons = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if _touches_border(x, y, bw, bh, w, h):
            continue
        if _equivalent_diameter_fraction(area, h, w) < MARK_MIN_DIAMETER_FRACTION:
            continue
        mask = (labels == i).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perimeter = max(cv2.arcLength(contours[0], True), 1.0)
        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        rectangularity = area / float(bw * bh)
        if circularity < MARK_MIN_CIRCULARITY and rectangularity < MARK_MIN_RECTANGULARITY:
            continue  # anatomy and clothing are not discs or bars

        ring = _ring(mask)
        if ring.sum() < 30 or float(skin[ring == 1].mean()) < MARK_MIN_RING_SKIN:
            continue
        surround = (ring == 1) & (skin == 1)
        if surround.sum() < 30:
            continue
        core = cv2.erode(mask, np.ones((5, 5), np.uint8))
        if core.sum() < 30:
            core = mask
        pixels = lab[core == 1]
        if float(pixels.std(axis=0).max()) > MARK_MAX_COLOR_STD:
            continue  # photographic content, not a flat fill
        color = pixels.mean(axis=0)
        if float(np.linalg.norm(color - lab[surround].mean(axis=0))) < MARK_MIN_COLOR_STEP:
            continue
        shape = "circle" if circularity >= MARK_MIN_CIRCULARITY else "bar"
        reasons.append(
            f"opaque {shape} covering the body at "
            f"x={x} y={y} {bw}x{bh}px (flat fill, hard edge)"
        )
    return reasons


def _find_detail_suppressed(image, skin, detail) -> list[str]:
    """Blur bands and mosaic pixelation over skin."""
    h, w = image.shape[:2]
    reference = float(np.median(detail[skin == 1]))
    if reference <= 0:
        return []
    low = ((detail < BLUR_MAX_DETAIL_RATIO * reference) & (skin == 1)).astype(np.uint8)
    skin_area = float(skin.sum())
    if low.sum() > BLUR_MAX_BODY_FRACTION * skin_area:
        return []  # retouched or upscaled gallery: no textured skin to compare against
    n, labels, stats, _ = _components(low)
    reasons = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if _touches_border(x, y, bw, bh, w, h):
            continue
        if _equivalent_diameter_fraction(area, h, w) < BLUR_MIN_DIAMETER_FRACTION:
            continue
        if area > BLUR_MAX_PATCH_FRACTION * skin_area:
            continue
        mask = (labels == i).astype(np.uint8)
        ring = _ring(mask, inner=5, outer=21)
        surround = (ring == 1) & (skin == 1)
        if surround.sum() < 50:
            continue
        if float(skin[mask == 1].mean()) < BLUR_MIN_SKIN_LIKE:
            continue
        inside = float(detail[mask == 1].mean())
        outside = float(detail[surround].mean())
        if outside < BLUR_MIN_DETAIL_STEP * inside:
            continue  # a gradual falloff is just smooth skin
        measure = ("no measurable texture at all" if inside <= 0
                   else f"{outside / inside:.0f}x less detail than the surrounding body")
        reasons.append(
            f"texture-free patch of skin at x={x} y={y} {bw}x{bh}px "
            f"(blur band or pixelation; {measure})"
        )
    return reasons


def _find_burned_in_graphics(lab, skin, body, backdrop) -> list[str]:
    """Text, arrows, measurement rules and date stamps drawn over the body.

    Strokes are thin and anti-aliased, so they are not looked for as flat solid
    regions; instead ink pixels are clustered by proximity and the cluster is
    judged on how much ink it holds. That is what makes a date stamp -- several
    disconnected glyphs -- a single finding.
    """
    h, w = lab.shape[:2]
    chroma = np.linalg.norm(lab[:, :, 1:] - 128.0, axis=2)
    if float(np.linalg.norm(backdrop[1:] - 128.0)) >= GRAPHIC_MIN_CHROMA:
        return []  # a strongly coloured backdrop makes "ink" meaningless
    # No `skin == 0` term: the skin mask is computed on a blurred copy, which
    # smears a thin anti-aliased stroke into the skin band and hides it. The
    # chroma threshold already sits above every skin tone in the corpus.
    ink = ((chroma >= GRAPHIC_MIN_CHROMA) & (body == 1)).astype(np.uint8)
    if not ink.any():
        return []
    k = GRAPHIC_CLUSTER_GAP
    clusters = cv2.dilate(ink, np.ones((k, k), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(clusters, 8)
    min_ink = GRAPHIC_MIN_INK_FRACTION * float(skin.sum())
    reasons = []
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        if _touches_border(x, y, bw, bh, w, h):
            continue
        ink_area = int(((labels == i) & (ink == 1)).sum())
        if ink_area < min_ink:
            continue
        reasons.append(
            f"burned-in graphic over the body at x={x} y={y} {bw}x{bh}px "
            f"({ink_area}px of non-photographic ink: annotation, arrow or date stamp)"
        )
    return reasons


def detect_censorship(image: np.ndarray) -> list[str]:
    """Reasons this BGR image must not be used for training. Empty means clean.

    Only chest-region occlusion and burned-in annotation count. A clinic
    watermark that sits on the backdrop is preserved by the model and is
    deliberately not a rejection reason.
    """
    if image is None or image.ndim != 3:
        return []
    skin = skin_mask(image)
    if skin.sum() < MIN_SKIN_FRACTION * skin.size:
        # Not enough visible body to reason about; the size and de-identification
        # gates own this case.
        return []
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    body = body_silhouette(skin)
    backdrop = backdrop_color(lab)
    detail = _detail_field(image)
    return (
        _find_opaque_marks(image, lab, skin, body)
        + _find_detail_suppressed(image, skin, detail)
        + _find_burned_in_graphics(lab, skin, body, backdrop)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs="+", help="Image files to check")
    args = parser.parse_args()

    flagged = 0
    for path in args.images:
        image = cv2.imread(str(path))
        if image is None:
            print(f"SKIP   {path}: cannot read")
            continue
        reasons = detect_censorship(image)
        if reasons:
            flagged += 1
            print(f"FLAG   {path}: " + "; ".join(reasons))
        else:
            print(f"clean  {path}")
    print(f"\n{flagged} of {len(args.images)} flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
