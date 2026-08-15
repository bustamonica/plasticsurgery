"""De-identification (deidentify.py).

The face-detection and blur stage was removed on the captain's ruling of
2026-08-14: the consented clinics contractually guarantee that no faces appear
in what they publish, so the corpus does not run face detection. The stage had
never once blurred a real face here - every Haar detection across every clinic
examined was a false positive on a breast in profile, a shoulder, a hip or hair -
and the blur that followed destroyed clean clinical photographs.

What deidentify.py still owes the corpus is metadata stripping, and that is what
these tests hold it to. It is not hypothetical: 36 of the 5656 corpus images
carry EXIF, and `harrington-177-front` carries a camera make and model and 2020
capture timestamps. Every path through the stage must strip.

Synthetic torsos only - no patient imagery is committed.
"""

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from conftest import make_torso
from deidentify import JPEG_QUALITY, crop_top, encode_stripped

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deidentify.py"


@pytest.fixture()
def torso():
    return make_torso(seed=7)


def stage_pair(tmp_path, pair_id, image, exif=None):
    """Write a staged pair; `exif` optionally bakes metadata into both images."""
    folder = tmp_path / "staging" / pair_id
    folder.mkdir(parents=True, exist_ok=True)
    for stem in ("before", "after"):
        path = folder / f"{stem}.jpg"
        if exif is None:
            cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        else:
            Image.fromarray(image[:, :, ::-1]).save(
                path, "JPEG", quality=95, exif=exif)
    (folder / "meta.json").write_text(json.dumps({"pair_id": pair_id}))
    return folder


def run(tmp_path, *extra):
    """Run the stage as a user would, and return (returncode, output)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "staging"),
         str(tmp_path / "clean"), *extra],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def exif_with_camera_and_timestamp():
    """The `harrington-177-front` shape: make, model and a capture timestamp."""
    exif = Image.Exif()
    exif[271] = "Canon"                     # Make
    exif[272] = "Canon EOS Rebel T6"        # Model
    exif[306] = "2020:03:04 16:30:45"       # DateTime
    exif[305] = "Photos 1.5"                # Software
    return exif


GPS_IFD = 34853


def exif_with_gps():
    exif = Image.Exif()
    exif[GPS_IFD] = {1: "N", 2: (IFDRational(51), IFDRational(30), IFDRational(0)),
                     3: "W", 4: (IFDRational(0), IFDRational(7), IFDRational(0))}
    exif[306] = "2020:03:04 16:30:45"
    return exif


def read_exif(path):
    with Image.open(path) as im:
        return dict(im.getexif())


def read_gps(path):
    """The GPS sub-IFD, which the top-level tag only points at by offset."""
    with Image.open(path) as im:
        return dict(im.getexif().get_ifd(GPS_IFD))


class TestMetadataStripping:
    """The one privacy property this stage still enforces itself.

    ingest.py also re-encodes, so stripping happens twice by design; these tests
    hold this stage to it independently, because that redundancy is the point.
    """

    def test_camera_make_model_and_timestamp_do_not_survive(self, tmp_path, torso):
        staged = stage_pair(tmp_path, "clinic01-0001-front", torso,
                            exif=exif_with_camera_and_timestamp())
        assert read_exif(staged / "before.jpg"), "fixture must start with EXIF"

        assert run(tmp_path)[0] == 0
        for stem in ("before", "after"):
            assert read_exif(tmp_path / "clean" / staged.name / f"{stem}.jpg") == {}

    def test_gps_does_not_survive(self, tmp_path, torso):
        staged = stage_pair(tmp_path, "clinic01-0002-front", torso,
                            exif=exif_with_gps())
        assert read_gps(staged / "before.jpg"), "fixture must start with GPS"

        assert run(tmp_path)[0] == 0
        out = tmp_path / "clean" / staged.name / "before.jpg"
        assert read_exif(out) == {}
        assert read_gps(out) == {}

    def test_stripping_also_happens_on_the_crop_top_path(self, tmp_path, torso):
        """--crop-top is a second path through the stage; it must strip too."""
        staged = stage_pair(tmp_path, "clinic01-0003-front", torso,
                            exif=exif_with_camera_and_timestamp())
        assert run(tmp_path, "--crop-top", "0.2")[0] == 0
        assert read_exif(tmp_path / "clean" / staged.name / "before.jpg") == {}

    def test_no_output_image_is_a_byte_copy_of_its_source(self, tmp_path, torso):
        """The property that makes 'every path strips' structural.

        A pass-through that copied source bytes would carry metadata with them,
        so no path may emit the input file verbatim.
        """
        staged = stage_pair(tmp_path, "clinic01-0004-front", torso,
                            exif=exif_with_camera_and_timestamp())
        assert run(tmp_path)[0] == 0
        for stem in ("before", "after"):
            src = (staged / f"{stem}.jpg").read_bytes()
            out = (tmp_path / "clean" / staged.name / f"{stem}.jpg").read_bytes()
            assert out != src

    def test_encode_stripped_returns_a_bare_jpeg(self, torso):
        data = encode_stripped(torso)
        assert data.startswith(b"\xff\xd8")  # JPEG SOI
        decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape == torso.shape


class TestNoFaceStageRemains:
    """The removed behaviour, asserted by what the stage now does to pixels.

    None of these read the source: they run the stage and compare its output to
    a plain re-encode of the input, which is what "no detection, no blur" means
    observably.
    """

    def test_the_image_is_only_re_encoded(self, tmp_path, torso):
        staged = stage_pair(tmp_path, "clinic01-0005-front", torso)
        assert run(tmp_path)[0] == 0
        for stem in ("before", "after"):
            source = cv2.imread(str(staged / f"{stem}.jpg"))
            out = (tmp_path / "clean" / staged.name / f"{stem}.jpg").read_bytes()
            assert out == encode_stripped(source)

    def test_a_frame_with_a_face_like_pattern_is_not_pixelated(self, tmp_path):
        """The false-positive shape that used to destroy tissue.

        A light/dark pattern like a breast in profile with a nipple is what the
        frontal-face cascade fired on. Whatever it looks like, the stage must
        leave the pixels alone: a mosaic block would show up as a patch with no
        variance, since the old blur resized the region to 8x8.
        """
        image = make_torso(seed=11)
        h, w = image.shape[:2]
        cv2.circle(image, (w // 2, int(h * 0.45)), int(min(h, w) * 0.05),
                   (60, 70, 90), -1)
        staged = stage_pair(tmp_path, "clinic01-0006-side-right", image)
        assert run(tmp_path)[0] == 0

        out = cv2.imread(str(tmp_path / "clean" / staged.name / "before.jpg"))
        source = cv2.imread(str(staged / "before.jpg"))

        # No 8x8-pixelated block anywhere: the old blur resized its region to
        # 8x8 and blew it back up, so a damaged tile would carry no texture at
        # all. Every tile still varies.
        tile = 32
        variances = [out[y:y + tile, x:x + tile].var()
                     for y in range(0, h - tile, tile)
                     for x in range(0, w - tile, tile)]
        assert min(variances) > 1

        # And the frame as a whole moved only as far as a quality-95 round trip
        # can move it - nothing was redrawn.
        delta = np.abs(out.astype(int) - source.astype(int))
        assert delta.mean() < 1
        assert delta.max() <= 16

    def test_a_headless_frame_is_accepted_without_any_flag(self, tmp_path, torso):
        """The old stage REJECTED every headless image unless --allow-no-face.

        That default cost whole clinics: the backlog run emitted 0 of 9 pairs on
        its first pass, charlotte 0 of 10, austinweston 0 of 34. There is no
        detector now, so a headless frame is simply accepted.
        """
        stage_pair(tmp_path, "clinic01-0007-front", torso)
        code, out = run(tmp_path)
        assert code == 0
        assert "1 accepted, 0 rejected" in out
        assert "no face detected" not in out

    def test_allow_no_face_is_no_longer_accepted(self, tmp_path, torso):
        """The flag only existed to steer the detector, so it is gone."""
        stage_pair(tmp_path, "clinic01-0008-front", torso)
        code, out = run(tmp_path, "--allow-no-face")
        assert code != 0
        assert "unrecognized arguments" in out

    def test_the_run_log_records_why_no_face_stage_ran(self, tmp_path, torso):
        """An auditor must be able to see the basis, not infer it from silence."""
        stage_pair(tmp_path, "clinic01-0009-front", torso)
        _, out = run(tmp_path)
        assert "No face detection was run" in out
        assert "clinics guarantee no faces are published" in out


class TestEverythingElseThisStageDoes:
    def test_meta_json_is_carried_through_unchanged(self, tmp_path, torso):
        staged = stage_pair(tmp_path, "clinic01-0010-front", torso)
        assert run(tmp_path)[0] == 0
        assert ((tmp_path / "clean" / staged.name / "meta.json").read_bytes()
                == (staged / "meta.json").read_bytes())

    def test_crop_top_removes_the_top_of_the_frame(self, tmp_path, torso):
        stage_pair(tmp_path, "clinic01-0011-front", torso)
        assert run(tmp_path, "--crop-top", "0.2")[0] == 0
        out = cv2.imread(str(tmp_path / "clean" / "clinic01-0011-front" / "before.jpg"))
        assert out.shape[0] == torso.shape[0] - int(torso.shape[0] * 0.2)
        assert out.shape[1] == torso.shape[1]

    def test_crop_top_helper_matches_the_fraction(self, torso):
        assert crop_top(torso, 0.25).shape[0] == torso.shape[0] - int(torso.shape[0] * 0.25)

    def test_an_unreadable_image_rejects_the_pair_and_leaves_nothing_behind(
            self, tmp_path, torso):
        staged = stage_pair(tmp_path, "clinic01-0012-front", torso)
        (staged / "after.jpg").write_bytes(b"not a jpeg")
        code, out = run(tmp_path)
        assert code == 1
        assert "cannot read after.jpg" in out
        assert not (tmp_path / "clean" / staged.name).exists()

    def test_an_empty_staging_tree_is_an_error(self, tmp_path):
        (tmp_path / "staging").mkdir()
        code, out = run(tmp_path)
        assert code == 1
        assert "run ingest.py first" in out

    def test_every_staged_pair_is_emitted(self, tmp_path):
        for n in range(3):
            stage_pair(tmp_path, f"clinic01-002{n}-front", make_torso(seed=n))
        code, out = run(tmp_path)
        assert code == 0
        assert "3 accepted, 0 rejected" in out
        assert len(list((tmp_path / "clean").iterdir())) == 3

    def test_output_quality_is_the_documented_constant(self, torso):
        assert JPEG_QUALITY == 95
