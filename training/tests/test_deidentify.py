"""De-identification safety (deidentify.py).

The blur stage is the only one that can CREATE censorship-like damage, and it
runs after ingest.py's censorship gate, so for a while nothing re-checked its
output. The Haar cascade false-positived on a torso in drmiroshnik case88 and
case112 and pixelated a breast in photographs the clinic publishes clean; both
pairs reached the corpus silently. `blur_is_on_the_body` is the check that turns
that into a rejection. Synthetic torsos only - no patient imagery is committed.
"""

import numpy as np
import pytest

from censorship import detect_censorship
from conftest import make_torso
from deidentify import (BLUR_MARGIN, blur_is_on_the_body, blur_regions,
                        encode_as_written)


@pytest.fixture()
def torso():
    return make_torso(seed=7)


def chest_box(image):
    """A box over the middle of the torso - what the cascade wrongly fires on.

    Sized like the real false positives: drmiroshnik case88's box was 182px on a
    400x449 frame, ~0.2 of the short side. Much larger and censorship.py stops
    reasoning at all (BLUR_MAX_PATCH_FRACTION), which is its own known limit.
    """
    h, w = image.shape[:2]
    side = int(min(h, w) * 0.20)
    return (w // 2 - side // 2, int(h * 0.45), side, side)


class TestBlurLandingOnTheBody:
    def test_pixelating_the_chest_is_reported(self, torso):
        blurred = blur_regions(torso, [chest_box(torso)])
        assert blur_is_on_the_body(torso, blurred)

    def test_the_reason_names_the_damage(self, torso):
        blurred = blur_regions(torso, [chest_box(torso)])
        assert any("texture-free" in r for r in blur_is_on_the_body(torso, blurred))

    def test_an_untouched_image_is_clean(self, torso):
        assert blur_is_on_the_body(torso, torso) == []

    def test_a_blur_over_the_head_is_not_flagged(self, torso):
        """The legitimate case. A pixelated FACE is also a texture-free patch of
        skin, so position is what separates it from a pixelated breast."""
        h, w = torso.shape[:2]
        side = int(min(h, w) * 0.12)
        head = (w // 2 - side // 2, 0, side, side)
        assert blur_is_on_the_body(torso, blur_regions(torso, [head])) == []

    def test_a_head_blur_that_censorship_py_flags_is_still_allowed(self, torso):
        """Guard against regressing to a naive 'any new mark' rule."""
        h, w = torso.shape[:2]
        side = int(min(h, w) * 0.12)
        head = (w // 2 - side // 2, 0, side, side)
        blurred = blur_regions(torso, [head])
        assert detect_censorship(blurred) != []
        assert blur_is_on_the_body(torso, blurred) == []

    def test_only_NEW_marks_count(self, torso):
        """A mark the input already carried is not this stage's doing."""
        already = blur_regions(torso, [chest_box(torso)])
        assert blur_is_on_the_body(already, already) == []

    def test_blur_margin_is_applied_so_the_damage_exceeds_the_box(self, torso):
        """BLUR_MARGIN expands the box; the damage is wider than the detection."""
        x, y, bw, bh = chest_box(torso)
        blurred = blur_regions(torso, [(x, y, bw, bh)])
        changed = np.any(blurred != torso, axis=2)
        cols = np.where(changed.any(axis=0))[0]
        assert cols.min() < x
        assert cols.max() > x + bw
        assert BLUR_MARGIN > 0


class TestTheCheckRunsOnWhatIsWritten:
    """detect_censorship reads a texture field and JPEG quantisation moves it.

    drmiroshnik case71's pixelated chest measured CLEAN as an in-memory array
    and censored after a quality-95 round trip, so checking the array passed a
    pair whose FILE was damaged. The check runs on the encoded bytes now.
    """

    def test_bytes_and_array_agree(self, torso):
        data, written = encode_as_written(torso)
        assert data.startswith(b"\xff\xd8")  # JPEG SOI
        assert written.shape == torso.shape

    def test_the_decoded_array_is_what_the_bytes_hold(self, torso):
        import cv2
        import numpy as np
        data, written = encode_as_written(blur_regions(torso, chest_box(torso)and[chest_box(torso)]))
        again = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert np.array_equal(again, written)

    def test_a_chest_blur_is_still_caught_after_encoding(self, torso):
        _, written = encode_as_written(blur_regions(torso, [chest_box(torso)]))
        assert blur_is_on_the_body(torso, written)
