"""Contact sheets must show exactly what the emit path writes.

A sheet decoded without a clinic's measured crop shows a watermark the emit
removes, and `--min-dim` then measures uncropped pixels - admitting pairs the
400px floor will reject and spending an annotation pass on them.
"""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import annotate_contact_sheets as acs  # noqa: E402
import scrape_gallery as sg  # noqa: E402


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def _cache(tmp_path: Path, cfg: sg.ClinicConfig, url: str,
           data: bytes) -> Path:
    path = tmp_path / sg.image_cache_key(cfg.slug, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return tmp_path


def test_composite_bottom_frac_is_applied_before_the_sheet_is_measured(tmp_path):
    """mwps burns its logo across 20.7% of the composite's height.

    Its real geometry: a 1500x499 composite whose halves are 750x499 uncropped
    and 750x389 once the band is trimmed - i.e. below ingest.py's 400px floor.
    A sheet built without the crop shows the watermark and passes --min-dim 400.
    """
    cfg = sg.CLINICS["mwps"]
    url = "https://www.mountainwestplasticsurgery.com/x/case-1.jpg"
    _cache(tmp_path, cfg, url, _jpeg(1500, 499))
    pair = sg.ImagePair(key="pair1", before_url=url, after_url=url,
                        split_composite=True, composite_bottom_frac=0.22)

    before, after = acs._pair_images(cfg, tmp_path, pair)

    assert _size(before) == (750, 389)
    assert _size(after) == (750, 389)
    assert acs._min_dimension((before, after)) < 400


def test_pixel_bottom_crop_is_applied_to_both_halves_of_a_sheet(tmp_path):
    """roth's one-sided watermark crop is 175 absolute rows, after the split."""
    cfg = sg.CLINICS["roth"]
    assert cfg.bottom_crop_px == 175
    url = "https://x.test/roth/case-1-detail.jpg"
    _cache(tmp_path, cfg, url, _jpeg(1200, 800))
    pair = sg.ImagePair(key="front", before_url=url, after_url=url,
                        split_composite=True)

    before, after = acs._pair_images(cfg, tmp_path, pair)

    assert _size(before) == (600, 625)
    assert _size(after) == (600, 625)


def test_fractional_bottom_crop_is_applied_to_a_two_file_sheet(tmp_path):
    """arps crops a fraction of the image's WIDTH off a pair of separate files."""
    cfg = sg.CLINICS["arps"]
    assert cfg.bottom_crop_frac == 0.10
    before_url = "https://arplasticsurgery.com.au/x/a-before.jpg"
    after_url = "https://arplasticsurgery.com.au/x/a-after.jpg"
    _cache(tmp_path, cfg, before_url, _jpeg(500, 900))
    _cache(tmp_path, cfg, after_url, _jpeg(500, 900))
    pair = sg.ImagePair(key="front", before_url=before_url, after_url=after_url)

    before, after = acs._pair_images(cfg, tmp_path, pair)

    assert _size(before) == (500, 850)
    assert _size(after) == (500, 850)


def test_grid_gutter_is_applied_to_a_sheet(tmp_path):
    """wyten's 2x2 template draws a blank divider the emit path trims away."""
    cfg = sg.CLINICS["wyten"]
    assert cfg.grid_gutter_px == 8
    url = "https://x.test/wyten/case-1.jpg"
    _cache(tmp_path, cfg, url, _jpeg(800, 800))
    pair = sg.ImagePair(key="front", before_url=url, after_url=url,
                        grid_shape=(2, 2), before_cell=(0, 0),
                        after_cell=(0, 1))

    before, after = acs._pair_images(cfg, tmp_path, pair)

    assert _size(before) == _size(after)
    assert _size(before) == (392, 392)


@pytest.mark.parametrize("slug", sorted(sg.CLINICS))
def test_a_sheet_half_matches_what_the_emit_path_would_write(slug, tmp_path):
    """The two paths share `decode_pair_halves`/`finish_pair_halves`, so a
    clinic that adds a crop knob cannot apply it on one side only."""
    cfg = sg.CLINICS[slug]
    url = f"https://x.test/{slug}/case-1.jpg"
    _cache(tmp_path, cfg, url, _jpeg(1400, 900))
    pair = sg.ImagePair(key="front", before_url=url, after_url=url,
                        split_composite=True)

    sheet = acs._pair_images(cfg, tmp_path, pair)
    emitted = sg.finish_pair_halves(
        cfg, *sg.decode_pair_halves(cfg, pair, _jpeg(1400, 900)))[:2]

    assert sheet == emitted
