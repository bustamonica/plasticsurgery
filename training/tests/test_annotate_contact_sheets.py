"""Contact sheets must show exactly what the emit path writes.

A sheet decoded without a clinic's measured crop shows a watermark the emit
removes, and `--min-dim` then measures uncropped pixels - admitting pairs the
400px floor will reject and spending an annotation pass on them.

It also covers the --group-by-case layout, which is what the 2026-08-25 batch's
front-view calls were made off: one case per ROW, its pairs left to right in
published order. A pair rendered on the wrong row is a view label attached to
the wrong case.

The rendering tests build real JPEGs (solid colours stand in for the clinical
frames, which are never committed) and read the pixels back out.
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

def jpeg(rgb: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, "JPEG", quality=100)
    return buf.getvalue()


def tile_colour(sheet: Image.Image, x: int, y: int, tile: int) -> tuple[int, int, int]:
    """The colour at the centre of the tile whose top-left box is (x, y)."""
    return sheet.getpixel((x + tile // 2, y + tile // 2))


def close_to(got: tuple[int, int, int], want: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= 12 for a, b in zip(got[:3], want))


# ---------------------------------------------------------------------------
# group_tiles_by_case: flat '<case>:<pair>' tiles -> one entry per case
# ---------------------------------------------------------------------------


def test_groups_flat_tiles_into_one_entry_per_case_in_published_order():
    tiles = [
        ("case-a:pair1", [b"a1"]),
        ("case-a:pair2", [b"a2"]),
        ("case-b:pair1", [b"b1"]),
        ("case-a:pair3", [b"a3"]),
    ]

    grouped = acs.group_tiles_by_case(tiles)

    assert [case_id for case_id, _ in grouped] == ["case-a", "case-b"]
    assert [key for key, _ in grouped[0][1]] == ["pair1", "pair2", "pair3"]
    assert [key for key, _ in grouped[1][1]] == ["pair1"]
    assert grouped[0][1][0][1] == [b"a1"]


def test_case_id_containing_no_separator_still_groups():
    grouped = acs.group_tiles_by_case([("bare-label", [b"x"])])

    assert grouped == [("bare-label", [("", [b"x"])])]


def test_case_ids_keep_their_own_colon_free_pair_keys():
    # gryskiewicz case ids are long slugs; the split is on the FIRST colon so
    # the pair key stays whole.
    grouped = acs.group_tiles_by_case(
        [("silicone-breast-augmentation-patient-1:pair2", [b"x"])]
    )

    assert grouped[0][0] == "silicone-breast-augmentation-patient-1"
    assert grouped[0][1][0][0] == "pair2"


# ---------------------------------------------------------------------------
# render_case_sheets: one case per row, pairs left to right
# ---------------------------------------------------------------------------


RED = (220, 30, 30)
GREEN = (30, 200, 30)
BLUE = (40, 60, 230)
YELLOW = (230, 210, 40)


def test_each_case_renders_on_its_own_row_pairs_left_to_right(tmp_path):
    tile = 100
    grouped = [
        ("case-a", [("pair1", [jpeg(RED)]), ("pair2", [jpeg(GREEN)])]),
        ("case-b", [("pair1", [jpeg(BLUE)])]),
    ]

    assert acs.render_case_sheets(grouped, tmp_path, tile, cases_per_sheet=8) == 1

    sheet = Image.open(tmp_path / "case_sheet_001.jpg").convert("RGB")
    band = acs.LABEL_H * 2
    row_a, row_b = 0, tile + band
    assert close_to(tile_colour(sheet, 0, row_a, tile), RED)
    assert close_to(tile_colour(sheet, tile, row_a, tile), GREEN)
    assert close_to(tile_colour(sheet, 0, row_b, tile), BLUE)
    # case-b has no second pair: that cell stays background, it does not pull
    # the next case's photograph up into this row.
    assert close_to(tile_colour(sheet, tile, row_b, tile), acs.TILE_BG)


def test_before_and_after_share_one_cell_when_both_sides_rendered(tmp_path):
    tile = 100
    grouped = [("case-a", [("pair1", [jpeg(RED), jpeg(GREEN)]),
                           ("pair2", [jpeg(BLUE), jpeg(YELLOW)])])]

    acs.render_case_sheets(grouped, tmp_path, tile, cases_per_sheet=8)

    sheet = Image.open(tmp_path / "case_sheet_001.jpg").convert("RGB")
    # cell width is 2 tiles: before|after adjacent, then the next pair.
    assert sheet.width == 4 * tile
    assert close_to(tile_colour(sheet, 0, 0, tile), RED)
    assert close_to(tile_colour(sheet, tile, 0, tile), GREEN)
    assert close_to(tile_colour(sheet, 2 * tile, 0, tile), BLUE)
    assert close_to(tile_colour(sheet, 3 * tile, 0, tile), YELLOW)


def test_sheet_height_leaves_a_label_band_under_every_row(tmp_path):
    tile = 100
    grouped = [("case-a", [("pair1", [jpeg(RED)])]),
               ("case-b", [("pair1", [jpeg(BLUE)])])]

    acs.render_case_sheets(grouped, tmp_path, tile, cases_per_sheet=8)

    sheet = Image.open(tmp_path / "case_sheet_001.jpg").convert("RGB")
    # Two label lines per row: the case id and the pair key each get their own,
    # so a long case id cannot overrun the pair key the annotations are keyed on.
    assert sheet.height == 2 * (tile + acs.LABEL_H * 2)


def test_cases_are_paginated_across_sheets(tmp_path):
    tile = 60
    grouped = [(f"case-{i}", [("pair1", [jpeg(RED)])]) for i in range(5)]

    assert acs.render_case_sheets(grouped, tmp_path, tile, cases_per_sheet=2) == 3

    names = sorted(p.name for p in tmp_path.glob("case_sheet_*.jpg"))
    assert names == ["case_sheet_001.jpg", "case_sheet_002.jpg",
                     "case_sheet_003.jpg"]
    # Last sheet holds the one leftover case, not a full-height blank page.
    last = Image.open(tmp_path / "case_sheet_003.jpg")
    assert last.height == tile + acs.LABEL_H * 2


def test_an_unreadable_tile_does_not_abandon_the_sheet(tmp_path, capsys):
    tile = 100
    grouped = [("case-a", [("pair1", [b"not a jpeg"]), ("pair2", [jpeg(GREEN)])])]

    assert acs.render_case_sheets(grouped, tmp_path, tile, cases_per_sheet=8) == 1

    sheet = Image.open(tmp_path / "case_sheet_001.jpg").convert("RGB")
    assert close_to(tile_colour(sheet, tile, 0, tile), GREEN)
    assert "cannot render case-a:pair1" in capsys.readouterr().out


def test_empty_input_writes_no_sheet(tmp_path):
    assert acs.render_case_sheets([], tmp_path, 100, cases_per_sheet=8) == 0
    assert list(tmp_path.glob("*.jpg")) == []
