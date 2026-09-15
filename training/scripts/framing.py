#!/usr/bin/env python3
"""How one fetched gallery image becomes the two halves the corpus stores.

This module owns every pixel operation between a clinic's published image and
the `before`/`after` files `scrape_gallery.py` writes into the raw intake tree,
and it is the only place those operations live. `scrape_gallery.main` (the
emit), `annotate_contact_sheets.py` (the sheets a view call is made from) and
the duplicate-patient check all go through it, so a sheet can never show a
frame the emit does not write, and `--min-dim` measures the pixels that will
actually reach `ingest.py`'s 400px floor.

Three things are configured, each measured per clinic and never transferred
from one clinic to another:

- The pair's LAYOUT is per pair, set by the parser on `ImagePair`: two separate
  files, one side-by-side before|after composite (`split_composite`), or two
  cells of a multi-panel grid (`grid_shape` + `before_cell`/`after_cell`).
- The clinic's presentation TEMPLATE is `ClinicConfig.frame`, a `Frame`: a
  printed border and centre gutter around a composite, a divider strip either
  side of its seam, or a blank divider between grid panels. That is template
  geometry, not a watermark, and a clinic without one splits the raw image.
- A burned-in WATERMARK is `ClinicConfig.crop`, one `Crop`. This is the single
  crop mechanism: a clinic carries at most one, which trims the SAME rows off
  both halves of every pair. The captain's rulings are the reason it exists: a
  corner or edge mark is cropped, never masked or tolerated (2026-08-19), and a
  mark on only one half is a label the model can learn instead of the anatomy.
  `training/clinic_watermarks.md` records each clinic's measurement.

A `Crop` is one rule from `CROP_RULES` - how that clinic's mark was measured -
applied at one `stage`:

- `"composite"`: cut from the whole composite BEFORE the split, inside the one
  JPEG encode the split already does. For a mark drawn across the seam, which
  must stay identical on both halves (mwps, tcclinic).
- `"half"`: cut from each emitted image after the split or as fetched (every
  other clinic with a mark).

The rules are not interchangeable, and that is the point of naming them: a
mark stamped at a fixed size is `px`; one drawn in proportion to the frame's
width across several export sizes is `width_frac` (arps: the same mark is
3.8%-7.2% of width but 24px-115px); one scaled to the frame's height is
`height_frac` (mwps); one whose position is read off each image's own pixels is
`caption_band` (tcclinic's badge rises out of its band); and `keep_height_frac`
keeps the top of the frame (bandy's measured ink start, 401 of 491 rows). Each
rule reproduces the exact arithmetic its clinic was measured and emitted with -
`ceil` for one, `round` for another - because a shifted row changes the bytes,
and `emit_corpus.py` treats a finished pair whose bytes differ as a conflict,
never as a re-run.

Every operation re-encodes as a quality-95 JPEG, and a clinic with nothing to
trim keeps the bytes it was served (and a two-file pair keeps its source
extension), since `ingest.py` re-encodes on the way to staging anyway.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import numpy as np
from PIL import Image


class CropConfigError(Exception):
    """A clinic's crop cannot apply to the layout its parser produced.

    Deliberately not a ValueError: a ValueError from `pair_images` is one image
    that cannot take its frame or crop, which a run reports as a skipped pair
    and survives. A misconfigured clinic fails every pair the same way, so it
    must stop the run rather than become a tally of skips and a zero exit.
    """


@dataclass(frozen=True)
class Frame:
    """A clinic's presentation template around its photographs.

    `border`/`gutter`: a printed frame around a composite - `border` px off each
    outer edge and `gutter` px off each side of the midpoint, before the split
    (sculpted's ~8px white frame and ~9px centre gutter). Both halves lose the
    same amount, so the pair stays dimension-matched.

    `seam_trim`: a divider strip drawn between a composite's two photos -
    that many columns off each side of the midpoint, with both halves cut to
    the same width so an odd-width composite cannot emit a before one pixel
    wider than its after (tcclinic). A white divider left in place is also a
    known false positive for `censorship.py`'s bar detector.

    `grid_gutter`: a blank divider between the panels of a grid composite -
    that many px off each cell edge that touches an INTERIOR seam (wyten's 2x2).
    Outer edges have no divider and are never trimmed.

    All zero is the raw midpoint split and the exact even grid division, which
    is what every clinic without a template publishes.
    """

    border: int = 0
    gutter: int = 0
    seam_trim: int = 0
    grid_gutter: int = 0


def _rows_px(width: int, height: int, amount: float, data: bytes) -> int:
    if amount >= height:
        raise ValueError(f"bottom crop of {int(amount)}px exceeds image height {height}")
    return height - int(amount)


def _rows_width_frac(width: int, height: int, amount: float, data: bytes) -> int:
    keep = height - int(round(width * amount))
    if keep <= 0:
        raise ValueError(
            f"bottom crop of {amount} of the width removes the whole "
            f"{width}x{height} frame")
    return keep


def _rows_height_frac(width: int, height: int, amount: float, data: bytes) -> int:
    return height - math.ceil(height * amount)


def _rows_keep_height_frac(width: int, height: int, amount: float, data: bytes) -> int:
    return round(amount * height)


def _rows_caption_band(width: int, height: int, amount: float, data: bytes) -> int:
    return height - caption_band_crop(data)


# rule -> (width, height, amount, encoded image) -> the row the kept frame ends at.
CROP_RULES: dict[str, Callable[[int, int, float, bytes], int]] = {
    # `amount` pixel rows off the bottom (tccs, roth, camp, wny, choice, coberly).
    "px": _rows_px,
    # `amount` of the image's WIDTH off the bottom, rounded (arps, folk).
    "width_frac": _rows_width_frac,
    # `amount` of the image's HEIGHT off the bottom, rounded UP (mwps).
    "height_frac": _rows_height_frac,
    # keep the top `amount` of the image's HEIGHT, rounded (bandy).
    "keep_height_frac": _rows_keep_height_frac,
    # the caption band and any logo rising out of it, measured per image; no
    # amount, and 0 rows on an image without a band (tcclinic).
    "caption_band": _rows_caption_band,
}
CROP_STAGES = ("composite", "half")


@dataclass(frozen=True)
class Crop:
    """A burned-in mark trimmed off the bottom of both halves of every pair.

    See the module docstring for the rules and stages. Constructed in
    `ClinicConfig.crop` with the clinic's measurement beside it.
    """

    rule: str
    amount: float = 0.0
    stage: str = "half"

    def __post_init__(self):
        if self.rule not in CROP_RULES:
            raise ValueError(f"unknown crop rule {self.rule!r}; one of {sorted(CROP_RULES)}")
        if self.stage not in CROP_STAGES:
            raise ValueError(f"unknown crop stage {self.stage!r}; one of {CROP_STAGES}")
        if (self.rule == "caption_band") != (self.amount == 0):
            raise ValueError(
                f"crop rule {self.rule!r} with amount {self.amount}: every rule but "
                "caption_band needs the clinic's measured amount, and caption_band "
                "measures its own")

    def bottom(self, data: bytes, width: int, height: int) -> int:
        """The row the kept frame ends at, for an image of this size."""
        return CROP_RULES[self.rule](width, height, self.amount, data)


def _jpeg(img: Image.Image, box: tuple[int, int, int, int]) -> bytes:
    buf = io.BytesIO()
    img.crop(box).convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def crop_image(data: bytes, crop: Crop) -> bytes:
    """One image with `crop` applied: the full width, down to the crop's row."""
    img = Image.open(io.BytesIO(data))
    return _jpeg(img, (0, 0, img.width, crop.bottom(data, img.width, img.height)))


# A caption band is a solid strip the clinic composites UNDER the photos to
# carry its logo. It is an edge watermark, so the corpus rule is to crop it
# (never mask it, never tolerate it) and to exclude what falls below the 400px
# floor afterwards rather than shipping a shrunken pair.
#
# Measured, not assumed: the crop is read off each image's own pixels, because
# a logo that overlaps the photo above the band (tcclinic's badge straddles the
# seam and rises 44px into the frame) makes the band height alone the wrong
# answer.
BAND_WHITE = 245          # a band pixel is near-white on every channel
BAND_ROW_FRACTION = 0.85  # ... and a band row is almost entirely such pixels
LOGO_VALUE = 80           # the badge is near-black ...
LOGO_NEUTRAL = 12         # ... and neutral (R, G and B within this of each other)
LOGO_HALF_WIDTH = 90      # searched only this far either side of the midpoint
LOGO_MAX_ROWS = 120       # ... and only this far above the band


def caption_band_crop(data: bytes) -> int:
    """Rows to drop off the bottom to remove a caption band and its logo.

    Returns 0 when the image carries no band, so the same call is safe on a
    gallery that mixes banded and unbanded images (tcclinic publishes both).
    """
    with Image.open(io.BytesIO(data)) as im:
        arr = np.asarray(im.convert("RGB")).astype(int)
    height, width, _ = arr.shape
    near_white = (arr >= BAND_WHITE).all(axis=2).mean(axis=1)
    band_top = height
    while band_top > 0 and near_white[band_top - 1] >= BAND_ROW_FRACTION:
        band_top -= 1
    if band_top == height:
        return 0
    # Walk up from the band while the logo still intrudes into the photo.
    mid = width // 2
    lo, hi = max(0, mid - LOGO_HALF_WIDTH), min(width, mid + LOGO_HALF_WIDTH)
    window = arr[:, lo:hi]
    logo = ((window.max(axis=2) < LOGO_VALUE)
            & (window.max(axis=2) - window.min(axis=2) < LOGO_NEUTRAL)).any(axis=1)
    top = band_top
    while top > 0 and band_top - top < LOGO_MAX_ROWS and logo[top - 1]:
        top -= 1
    return height - top


def split_composite(data: bytes, frame: Frame = Frame(),
                    crop: Crop | None = None) -> tuple[bytes, bytes]:
    """Split a side-by-side before|after composite into (before, after) JPEGs.

    The split is the exact horizontal midpoint. Raises ValueError for
    portrait/square images, where a left|right split cannot be assumed, and for
    a frame or crop that would leave nothing; a trim is refused rather than
    silently clamped.

    `frame` trims the clinic's printed template (see `Frame`). `crop`, when
    given, is a composite-stage watermark crop: it sets the bottom of the whole
    composite before the split, so both halves lose exactly the same rows. A
    half-stage crop is not applied here; `pair_images` applies it to each half.
    """
    img = Image.open(io.BytesIO(data))
    if img.width <= img.height:
        raise ValueError(
            f"composite image is not landscape ({img.width}x{img.height}); "
            "cannot assume a left|right before|after split")
    border, gutter, seam_trim = frame.border, frame.gutter, frame.seam_trim
    if border < 0 or gutter < 0:
        raise ValueError("composite border/gutter must not be negative")
    half = img.width // 2
    if border + gutter >= half:
        raise ValueError(
            f"composite trim (border={border}, gutter={gutter}) leaves no image "
            f"in a {img.width}x{img.height} composite")
    floor_y = img.height if crop is None else crop.bottom(data, img.width, img.height)
    top, bottom = border, floor_y - border
    if bottom <= top:
        raise ValueError(
            f"composite trim (border={border}, crop={crop}) leaves nothing of a "
            f"{img.width}x{img.height} composite")
    if seam_trim:
        # Both halves are cut to the SAME width, so an odd-width composite
        # cannot emit a before one pixel wider than its after.
        width = min(half, img.width - half) - seam_trim - border
        if width <= 0:
            raise ValueError(
                f"seam trim of {seam_trim}px leaves no image either side of "
                f"the midpoint of a {img.width}px-wide composite")
        boxes = ((half - seam_trim - width, top, half - seam_trim, bottom),
                 (half + seam_trim, top, half + seam_trim + width, bottom))
    else:
        # Untrimmed, the split stays exactly what it has always been: an
        # odd-width composite gives an after one pixel wider. Every clinic
        # already in the corpus was emitted this way and emit_corpus.py treats
        # a byte difference as a clash, so this path must not shift.
        boxes = ((border, top, half - gutter, bottom),
                 (half + gutter, top, img.width - border, bottom))
    return _jpeg(img, boxes[0]), _jpeg(img, boxes[1])


def crop_grid_cell(data: bytes, rows: int, cols: int, cell: tuple[int, int],
                   gutter_px: int = 0) -> bytes:
    """Crop one (row, col) cell out of a rows x cols grid composite image.

    Used for multi-panel composites (drrohrich's 2x2 front/side x
    before/after; drteitelbaum/skplastic's 2x3 front/oblique/side x
    before/after) where a single fetched image yields several pairs.

    `gutter_px` is `Frame.grid_gutter`: it trims that many pixels off each cell
    edge that touches an INTERIOR seam, and every cell in a given row or column
    loses the same amount, which keeps the two halves of a pair dimensionally
    matched. Left at 0 the crop is the exact even division it has always been.

    Trimming rather than tolerating matters beyond tidiness: a white divider
    strip merged into the skin silhouette is one of the known false-positive
    families in `censorship.py` (see its module docstring), so a cell shipped
    with the seam still attached can be rejected at ingest for a mark the
    clinic never put on the patient.
    """
    img = Image.open(io.BytesIO(data))
    cell_w, cell_h = img.width // cols, img.height // rows
    row, col = cell
    left, top = col * cell_w, row * cell_h
    right, bottom = left + cell_w, top + cell_h
    if gutter_px:
        if col > 0:
            left += gutter_px
        if col < cols - 1:
            right -= gutter_px
        if row > 0:
            top += gutter_px
        if row < rows - 1:
            bottom -= gutter_px
    return _jpeg(img, (left, top, right, bottom))


def image_url(cfg, url: str) -> str:
    """A pair's image URL made absolute against the clinic's base_url."""
    return url if url.startswith("http") else cfg.base_url + url


@dataclass(frozen=True)
class PairImages:
    """The two files one pair emits: bytes and the filename each is written as."""

    before: bytes
    after: bytes
    before_name: str
    after_name: str


def pair_images(cfg, pair, fetch: Callable[[str], bytes]) -> PairImages:
    """Decode, frame and crop one pair exactly as the corpus stores it.

    `cfg` is the clinic's `ClinicConfig` and `pair` its `ImagePair`; `fetch`
    maps an absolute image URL to its bytes (the emit's polite cached fetcher,
    or a cache-only reader for the contact sheets). Fetch errors propagate
    untouched - the caller decides which of them a run may survive. Raises
    ValueError when the frame or crop cannot be applied to this image, which
    the caller reports as a skipped pair.

    A composite-stage crop only has a composite to cut, so a clinic that sets
    one on a pair of any other layout is a configuration error and raises
    `CropConfigError`, which no per-pair handler catches.

    A composite or grid pair is always written as `.jpg`: its halves are
    re-encoded by the split. A two-file pair keeps its source extension until a
    crop re-encodes it, because the corpus legitimately mixes
    .jpg/.jpeg/.png/.webp and a scan that globs `before.*` depends on the name
    being true to the bytes.
    """
    crop = cfg.crop
    composite_crop = crop if crop is not None and crop.stage == "composite" else None
    half_crop = crop if crop is not None and crop.stage == "half" else None
    if composite_crop is not None and not pair.split_composite:
        raise CropConfigError(
            f"{cfg.slug} crops at the composite stage but pair {pair.key} is not "
            "a side-by-side composite")

    if pair.grid_shape is not None or pair.split_composite:
        data = fetch(image_url(cfg, pair.before_url))
        if pair.grid_shape is not None:
            rows, cols = pair.grid_shape
            before = crop_grid_cell(data, rows, cols, pair.before_cell, cfg.frame.grid_gutter)
            after = crop_grid_cell(data, rows, cols, pair.after_cell, cfg.frame.grid_gutter)
        else:
            before, after = split_composite(data, cfg.frame, composite_crop)
        names = ("before.jpg", "after.jpg")
    else:
        before_url, after_url = image_url(cfg, pair.before_url), image_url(cfg, pair.after_url)
        before, after = fetch(before_url), fetch(after_url)
        names = tuple(f"{stem}{(Path(urlsplit(url).path).suffix or '.jpg').lower()}"
                      for stem, url in (("before", before_url), ("after", after_url)))
    if half_crop is not None:
        before, after = crop_image(before, half_crop), crop_image(after, half_crop)
        names = ("before.jpg", "after.jpg")
    return PairImages(before, after, *names)
