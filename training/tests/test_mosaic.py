"""Contract for the mosaic gate.

Two halves, and both are load-bearing. The POSITIVE half says the gate sees
mosaic at the cell sizes the corpus actually publishes. The NEGATIVE half pins
the false-positive family that has cost this project real data: `censorship.py`
holds 77 bayside pairs and 6 harrington pairs on smooth skin and a smooth warm
backdrop, and a mosaic gate that repeated that mistake would be worse than no
gate at all. The synthetic negatives run everywhere; the real ones run wherever
the consented trees are mounted.
"""
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import make_torso, needs_corpus, CORPUS
from mosaic import detect_mosaic, mosaic_regions, grid_alignment


def tattoo(image, box, seed=1):
    """Ink on skin, which is what a clinic actually mosaics over.

    A synthetic torso is otherwise almost featureless, so pixelating it produces
    cells that differ from their neighbours by less than sensor noise - no step
    between neighbours, and correctly no detection. The damage worth testing is
    a mosaic over something with structure, which is what clinics censor.
    """
    out = image.copy()
    x0, y0, x1, y1 = box
    rng = np.random.default_rng(seed)
    for _ in range(14):
        a = (int(rng.integers(x0, x1)), int(rng.integers(y0, y1)))
        b = (int(rng.integers(x0, x1)), int(rng.integers(y0, y1)))
        cv2.line(out, a, b, (40, 45, 70), int(rng.integers(3, 8)))
    return out


def pixelate(image, box, cell):
    """Burn a mosaic of exactly `cell`-px squares over `box` = (x0, y0, x1, y1).

    The box is trimmed to a whole number of cells: resizing back to a size that
    is not a multiple of the cell stretches the cells to a non-integer pitch,
    which is not what a censoring tool produces.
    """
    out = image.copy()
    x0, y0, x1, y1 = box
    x1 = x0 + (x1 - x0) // cell * cell
    y1 = y0 + (y1 - y0) // cell * cell
    patch = out[y0:y1, x0:x1]
    h, w = patch.shape[:2]
    small = cv2.resize(patch, (w // cell, h // cell), interpolation=cv2.INTER_AREA)
    out[y0:y1, x0:x1] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return out


def jpeg_roundtrip(image, quality=95):
    """What ingest.py actually hands the gate: a re-encoded JPEG, not raw pixels."""
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


# --- positives -------------------------------------------------------------

@pytest.mark.parametrize("cell", [6, 10, 12, 19, 28])
def test_mosaic_over_the_chest_is_flagged(torso, chest_box, cell):
    """Every cell size the corpus publishes, from sanantonio's 6px to marina's 28px.

    8 is deliberately absent: an 8px mosaic re-encoded out of phase with JPEG's
    own 8x8 DCT grid is smeared past detection (measured, and recorded under
    LIMITS in `mosaic.py`). In phase it is found at alignment 17.8.
    """
    box = chest_box(torso)
    damaged = jpeg_roundtrip(pixelate(tattoo(torso, box), box, cell))
    assert detect_mosaic(damaged), f"{cell}px mosaic not flagged"


def test_the_reason_names_the_region_and_the_cell_size(torso, chest_box):
    box = chest_box(torso)
    x0, y0, x1, y1 = box
    damaged = jpeg_roundtrip(pixelate(tattoo(torso, box), box, 10))
    (reason,) = detect_mosaic(damaged)[:1]
    assert "mosaic pixelation" in reason
    region = mosaic_regions(damaged)[0]
    # The reported cell size may be a DIVISOR of the true one: a 5px grid tiles a
    # 10px mosaic too, and every one of its cells is flat and stepped. The size is
    # evidence for a human, not a claim about the censoring tool's setting.
    assert 10 % region["cell_px"] == 0, region
    # the reported box must land on the damage, not somewhere else in the frame
    assert x0 - 20 <= region["x"] <= x1 and y0 - 20 <= region["y"] <= y1


def test_mosaic_on_a_dark_region_is_flagged():
    """The prototype's `mean > 40` gate dropped aips case 11 - a dark mosaic bar
    across the sternum. Brightness is not evidence either way."""
    torso = make_torso()
    h, w = torso.shape[:2]
    bar = (int(w * 0.40), int(h * 0.36), int(w * 0.60), int(h * 0.44))
    torso[bar[1]:bar[3], bar[0]:bar[2]] = (60, 58, 64)
    damaged = jpeg_roundtrip(pixelate(tattoo(torso, bar, seed=9), bar, 8))
    assert detect_mosaic(damaged)


# --- negatives: the known false-positive families --------------------------

def test_a_clean_torso_is_not_flagged(torso):
    assert detect_mosaic(jpeg_roundtrip(torso)) == []


def test_smooth_soft_focus_skin_is_not_flagged():
    """The harrington family: evenly lit, retouched skin with no texture at all.
    Flat is not mosaic - the gate must also see a grid."""
    torso = make_torso()
    smooth = cv2.GaussianBlur(torso, (31, 31), 0)
    assert detect_mosaic(jpeg_roundtrip(smooth)) == []


def test_a_blown_out_highlight_is_not_flagged():
    """A specular highlight is a texture-free patch bounded by a hard edge, which
    is exactly what `censorship.py` rejects harrington-176-front for."""
    torso = make_torso()
    h, w = torso.shape[:2]
    cv2.ellipse(torso, (w // 2, int(h * 0.38)), (int(w * 0.16), int(h * 0.08)),
                0, 0, 360, (252, 252, 252), -1)
    torso = cv2.GaussianBlur(torso, (9, 9), 0)
    assert detect_mosaic(jpeg_roundtrip(torso)) == []


def test_a_smooth_skin_toned_backdrop_is_not_flagged():
    """The bayside family: a warm, evenly lit wall the skin mask swallows. 77 of
    that clinic's pairs are held on this shape and every reported box was wall."""
    rng = np.random.default_rng(11)
    image = np.full((640, 480, 3), (150, 172, 198), np.uint8)
    cv2.ellipse(image, (240, 320), (150, 260), 0, 0, 360, (146, 168, 196), -1)
    image = np.clip(image.astype(np.float32) + rng.normal(0, 1.2, image.shape), 0, 255).astype(np.uint8)
    assert detect_mosaic(jpeg_roundtrip(image)) == []


def test_heavy_jpeg_blocking_alone_is_not_flagged(torso):
    """JPEG's own 8x8 DCT blocking is piecewise-constant on a regular grid too.
    What it is not is a hard STEP between neighbouring cells."""
    assert detect_mosaic(jpeg_roundtrip(torso, quality=25)) == []


def test_a_flat_dark_backdrop_is_not_flagged():
    rng = np.random.default_rng(5)
    image = np.clip(np.full((600, 500, 3), 26, np.float32)
                    + rng.normal(0, 1.0, (600, 500, 3)), 0, 255).astype(np.uint8)
    assert detect_mosaic(jpeg_roundtrip(image)) == []


def test_a_non_image_is_not_flagged():
    assert detect_mosaic(None) == []
    assert detect_mosaic(np.zeros((10, 10), np.uint8)) == []


# --- the grid-alignment discriminator itself -------------------------------

def test_alignment_separates_a_mosaic_from_a_soft_edge(torso, chest_box):
    """The measurement the whole gate turns on, stated on its own."""
    box = chest_box(torso)
    x0, y0, x1, y1 = box
    damaged = jpeg_roundtrip(pixelate(tattoo(torso, box), box, 10))
    gray = cv2.cvtColor(damaged, cv2.COLOR_BGR2GRAY).astype(np.float32)
    on_grid = grid_alignment(gray, 10, y0 % 10, x0 % 10, (x0, y0, x1 - x0, y1 - y0))
    clean = cv2.cvtColor(jpeg_roundtrip(cv2.GaussianBlur(tattoo(torso, box), (21, 21), 0)),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
    off_grid = grid_alignment(clean, 10, y0 % 10, x0 % 10, (x0, y0, x1 - x0, y1 - y0))
    assert min(on_grid) > 3.0
    assert min(off_grid) < 2.0


# --- real corpus cases -----------------------------------------------------
# Measured 2026-08-26. These pin the operating point against real imagery; the
# synthetic tests above cannot, because a synthetic mosaic is cleaner than a
# clinic's.

AIPS_CACHE = Path(os.path.expanduser(
    "~/firstmate/data/ba-viz-collect-aips/raw/.scraper-cache/images/aips"))
BAYSIDE_INTAKE = Path(os.path.expanduser(
    "~/firstmate/data/ba-viz-collect-bayside/intake/bayside"))

needs_aips = pytest.mark.skipif(not AIPS_CACHE.exists(),
                                reason="aips scrape cache not mounted")
needs_bayside = pytest.mark.skipif(not BAYSIDE_INTAKE.exists(),
                                   reason="bayside intake tree not mounted")


@needs_aips
def test_flags_the_aips_censored_re_upload_batch():
    """aips republished 8 tattooed patients' photos with mosaic burned in, under a
    `_2` filename suffix. 44 images, every one confirmed by eye in
    `ba-viz-collect-aips/report.md`; `censorship.py` flags 0 of them."""
    positives = sorted(AIPS_CACHE.glob("*_2.jpg"))
    assert len(positives) == 44
    flagged = sum(1 for p in positives if detect_mosaic(cv2.imread(str(p))))
    assert flagged >= 39, f"regression: {flagged}/44 (measured 39 on 2026-08-26)"


@needs_aips
def test_does_not_flag_the_aips_uncensored_halves():
    """The same clinic, same lighting, same camera - only the mosaic differs."""
    negatives = sorted(p for p in AIPS_CACHE.glob("*.jpg") if not p.name.endswith("_2.jpg"))
    assert len(negatives) == 140
    flagged = [p.name for p in negatives if detect_mosaic(cv2.imread(str(p)))]
    assert flagged == []


@needs_bayside
def test_does_not_flag_the_bayside_pairs_censorship_holds():
    """77 pairs `censorship.py` rejects as texture-free skin, every reported box
    on the practice's warm-toned wall. The mosaic gate must not repeat that."""
    import json
    rejects = json.loads(Path(os.path.expanduser(
        "~/firstmate/data/ba-viz-collect-bayside/rejects.json")).read_text())
    held = [p for p, r in rejects.items() if r.startswith("censorship-detector")]
    images = [g for p in held for s in ("before", "after")
              for g in BAYSIDE_INTAKE.glob(f"{p}/{s}.*")]
    assert len(images) >= 150
    flagged = [str(p) for p in images if detect_mosaic(cv2.imread(str(p)))]
    assert flagged == []


@needs_corpus
def test_does_not_flag_harrington_176_front():
    """Already in the corpus, and `censorship.py` would reject it today - the
    open decision `ba-viz-emit-harrington-decision-smooth-skin-censorship-fp`."""
    pair = CORPUS / "harrington" / "harrington-176-front"
    if not pair.exists():
        pytest.skip("harrington-176-front not in the mounted corpus")
    for stem in ("before", "after"):
        for path in pair.glob(f"{stem}.*"):
            assert detect_mosaic(cv2.imread(str(path))) == []
