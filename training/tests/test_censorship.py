"""Censorship / burned-in annotation detection (censorship.py).

Positives are drawn onto a synthetic torso so that no patient imagery is ever
committed. The real corpus is only referenced by path, in the tests at the
bottom, which skip when it is not mounted.
"""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from censorship import detect_censorship
from conftest import SKIN_BGR, make_torso

CORPUS = Path(os.path.expanduser("~/firstmate/data/clinic-corpus"))


def kinds(reasons):
    """First word of each reason: 'opaque', 'texture-free' or 'burned-in'."""
    return {r.split()[0] for r in reasons}


class TestCleanPhotos:
    def test_clean_torso_is_accepted(self, torso):
        assert detect_censorship(torso) == []

    def test_clean_at_the_400px_ingest_floor(self):
        # MIN_DIMENSION is 400, so the detector has to behave on the smallest
        # frames ingest.py accepts, not just on 1024px ones.
        assert detect_censorship(make_torso(size=(418, 418), seed=3)) == []

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_clean_across_texture_seeds(self, seed):
        assert detect_censorship(make_torso(seed=seed)) == []

    def test_corner_watermark_is_kept(self, torso):
        # Clinic watermarks sit on the backdrop, are preserved intact by the
        # model, and are explicitly NOT a rejection reason.
        h, w = torso.shape[:2]
        cv2.putText(torso, "ADAM R. KOLKER, MD", (int(w * 0.18), h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (170, 170, 170), 2, cv2.LINE_AA)
        assert detect_censorship(torso) == []

    def test_nipple_sized_dark_spot_is_not_a_censorship_mark(self, torso):
        # Areolae, moles and shadows are small, round and dark. The size floor
        # is what keeps them out; it was set from measured corpus anatomy.
        h, w = torso.shape[:2]
        cv2.circle(torso, (int(w * 0.40), int(h * 0.40)), int(min(h, w) * 0.02),
                   (110, 120, 150), -1)
        assert detect_censorship(torso) == []

    def test_non_torso_frame_is_left_to_the_other_gates(self):
        # Too little skin to reason about: `ingest.py`'s size gate and the
        # human visual audit own this case - de-identification does not, since
        # it only strips metadata and never rejects on what the frame shows -
        # and the detector must not invent a reason.
        assert detect_censorship(np.zeros((600, 600, 3), np.uint8)) == []


class TestOpaqueMarks:
    def test_sticker_circle_over_the_nipple(self, torso):
        h, w = torso.shape[:2]
        cv2.circle(torso, (int(w * 0.40), int(h * 0.38)), int(min(h, w) * 0.06),
                   (219, 178, 133), -1)  # sixsurgery's pale blue
        assert "opaque" in kinds(detect_censorship(torso))

    def test_black_censorship_bar(self, torso):
        h, w = torso.shape[:2]
        cv2.rectangle(torso, (int(w * 0.32), int(h * 0.34)),
                      (int(w * 0.68), int(h * 0.45)), (0, 0, 0), -1)
        assert "opaque" in kinds(detect_censorship(torso))

    def test_white_bar_reports_its_position(self, torso):
        h, w = torso.shape[:2]
        cv2.rectangle(torso, (int(w * 0.34), int(h * 0.36)),
                      (int(w * 0.66), int(h * 0.44)), (255, 255, 255), -1)
        reasons = detect_censorship(torso)
        assert reasons and "x=" in reasons[0] and "px" in reasons[0]


class TestDetailSuppressed:
    def test_gaussian_blur_band(self, torso, chest_box):
        x0, y0, x1, y1 = chest_box(torso)
        torso[y0:y1, x0:x1] = cv2.GaussianBlur(torso[y0:y1, x0:x1], (0, 0), 12)
        assert "texture-free" in kinds(detect_censorship(torso))

    def test_mosaic_pixelation_band(self, torso, chest_box):
        x0, y0, x1, y1 = chest_box(torso)
        small = cv2.resize(torso[y0:y1, x0:x1], (8, 8), interpolation=cv2.INTER_LINEAR)
        torso[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0),
                                         interpolation=cv2.INTER_NEAREST)
        assert "texture-free" in kinds(detect_censorship(torso))

    def test_softly_lit_skin_is_not_a_blur_band(self, torso, chest_box):
        # A gradual falloff in texture is ordinary lighting. Only a hard detail
        # edge counts, which is what BLUR_MIN_DETAIL_STEP encodes.
        x0, y0, x1, y1 = chest_box(torso)
        patch = torso[y0:y1, x0:x1].astype(np.float32)
        blurred = cv2.GaussianBlur(patch, (0, 0), 6)
        ramp = np.linspace(0, 1, y1 - y0)[:, None, None]
        torso[y0:y1, x0:x1] = (patch * (1 - ramp) + blurred * ramp).astype(np.uint8)
        assert "texture-free" not in kinds(detect_censorship(torso))


class TestBurnedInGraphics:
    def test_red_arrow_over_the_chest(self, torso):
        h, w = torso.shape[:2]
        cv2.arrowedLine(torso, (int(w * 0.30), int(h * 0.30)),
                        (int(w * 0.48), int(h * 0.42)), (0, 0, 255), 9, tipLength=0.3)
        assert "burned-in" in kinds(detect_censorship(torso))

    def test_burned_in_date_stamp(self, torso):
        h, w = torso.shape[:2]
        cv2.putText(torso, "03/11/2024", (int(w * 0.32), int(h * 0.55)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3, cv2.LINE_AA)
        assert "burned-in" in kinds(detect_censorship(torso))

    def test_measurement_overlay(self, torso):
        h, w = torso.shape[:2]
        cv2.line(torso, (int(w * 0.34), int(h * 0.42)), (int(w * 0.66), int(h * 0.42)),
                 (0, 255, 0), 7)
        assert "burned-in" in kinds(detect_censorship(torso))


class TestReasons:
    def test_reason_names_the_artefact_and_does_not_offer_a_repair(self, torso):
        h, w = torso.shape[:2]
        cv2.circle(torso, (int(w * 0.40), int(h * 0.38)), int(min(h, w) * 0.06),
                   (219, 178, 133), -1)
        reason = detect_censorship(torso)[0]
        assert reason.startswith("opaque circle")
        assert "crop" not in reason  # rejecting is the only outcome


# --- Real corpus references ------------------------------------------------
# Paths only; the corpus lives outside the repo and is never committed.

sixsurgery_dir = CORPUS / ".scraper-cache" / "images" / "sixsurgery"
clean_pair_dir = CORPUS / "drkolker" / "raw" / "drkolker" / "drkolker-74-front"

needs_corpus = pytest.mark.skipif(
    not CORPUS.exists(), reason="clinic corpus not mounted (it lives outside the repo)"
)


@needs_corpus
def test_real_censored_gallery_is_rejected():
    # sixsurgery publishes every photo with opaque circles over the nipples;
    # the whole clinic is withheld from training because of it.
    images = sorted(p for p in sixsurgery_dir.glob("*") if p.suffix in {".png", ".webp", ".jpg"})
    if not images:
        pytest.skip("sixsurgery cache not present")
    flagged = sum(1 for p in images[:20] if detect_censorship(cv2.imread(str(p))))
    assert flagged >= len(images[:20]) * 0.7


@needs_corpus
@pytest.mark.parametrize("stem", ["before", "after"])
def test_real_clean_pair_is_accepted(stem):
    path = clean_pair_dir / f"{stem}.jpg"
    if not path.exists():
        pytest.skip(f"{path} not present")
    assert detect_censorship(cv2.imread(str(path))) == []
