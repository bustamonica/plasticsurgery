"""Tests for scrape_gallery.py parsing against real (trimmed) gallery snapshots.

Fixtures under fixtures/gallery/ are text/HTML excerpts of real case pages
(gallery image tags + spec blocks). No patient images are stored.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# drkolker: case-image extraction (views, before/after split)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["01", "02", "50"])
def test_kolker_parse_case_finds_three_views(case):
    data = sg.kolker_parse_case(load_fixture(f"case_{case}.html"), case, "x")
    assert [p.key for p in data.pairs] == ["front", "oblique", "side"]
    for pair in data.pairs:
        # The thumbs strip duplicates images with buggy all-'After' alts; the
        # main gallery must win, so 01/03/05 are before and 02/04/06 after.
        assert pair.before_url.endswith(("01.jpg", "03.jpg", "05.jpg"))
        assert pair.after_url.endswith(("02.jpg", "04.jpg", "06.jpg"))
        assert pair.view_hint == pair.key


def test_kolker_parse_case_ignores_other_cases():
    html = load_fixture("case_01.html") + (
        '<div class="swiper-container procedure-gallery__swiper-gallery">'
        '<img src="/gallery/breast/breast-augmentation/99/01.jpg" '
        'alt="Breast Augmentation Before Image Patient 99 Front View"></div>'
    )
    data = sg.kolker_parse_case(html, "01", "x")
    for pair in data.pairs:
        assert "/99/" not in pair.before_url


# ---------------------------------------------------------------------------
# drkolker: spec parsing
# ---------------------------------------------------------------------------


def test_kolker_specs_motiva_case():
    specs = sg.kolker_parse_case(load_fixture("case_01.html"), "01", "x").specs
    assert specs.age == 24
    assert specs.height == "5'5"
    assert specs.weight_lbs == 125
    assert specs.left_cc == 170
    assert specs.right_cc == 185
    assert specs.brand == "motiva"
    # 'Mini Motiva' is Motiva's documented projection tier: Mini -> moderate.
    assert specs.profile == "moderate"
    # Shape is never stated for this case and must not be invented.
    assert specs.shape is None
    assert sg.volume_cc(specs) == 178  # average of 170/185, per schema


@pytest.mark.parametrize(
    "fixture,expected_profile",
    [
        ("case_motiva_demi.html", "moderate-plus"),
        ("case_motiva_full.html", "high"),
        ("case_motiva_corse.html", "extra-high"),
    ],
)
def test_kolker_motiva_projection_families(fixture, expected_profile):
    # Motiva projection families map onto the schema's profile enum:
    # Demi -> moderate-plus, Full -> high, Corsé -> extra-high.
    specs = sg.kolker_parse_case(load_fixture(fixture), "x", "x").specs
    assert specs.brand == "motiva"
    assert specs.profile == expected_profile


def test_kolker_non_motiva_never_invents_profile():
    # The bare words 'Demi'/'Full'/'Corsé' are ambiguous outside Motiva; a
    # non-Motiva case using them must keep profile unset.
    specs = sg.CaseSpecs()
    sg.classify_brand_shape_profile(
        specs, "310cc Full round saline implants, bilateral")
    assert specs.brand == "unknown"
    assert specs.shape == "round"
    assert specs.profile is None


def test_kolker_specs_silicone_round_case():
    specs = sg.kolker_parse_case(load_fixture("case_02.html"), "02", "x").specs
    assert specs.age == 36
    assert specs.left_cc == 240
    assert specs.right_cc == 265
    assert specs.brand == "unknown"  # 'Silicone Breast Implants', no brand named
    assert specs.shape == "round"
    assert specs.profile is None  # no projection documented
    assert sg.volume_cc(specs) == 252


def test_kolker_specs_saline_filled_to_case():
    specs = sg.kolker_parse_case(load_fixture("case_50.html"), "50", "x").specs
    # 'R 270 filled to 285cc, L 300 filled to 285cc': the filled-to volume is
    # the final implant volume.
    assert specs.left_cc == 285
    assert specs.right_cc == 285
    assert specs.shape == "round"
    assert sg.volume_cc(specs) == 285


def test_kolker_specs_missing_block():
    specs = sg.kolker_parse_case("<html><body><p>none</p></body></html>", "01", "x").specs
    assert specs.brand == "unknown"
    assert specs.shape is None
    assert sg.volume_cc(specs) is None


# ---------------------------------------------------------------------------
# drdanielbarrett: parsing
# ---------------------------------------------------------------------------


def test_barrett_parse_sientra_case():
    cases = sg.barrett_parse_listing(load_fixture("barrett_case_50342.html"), "x")
    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "50342"
    assert case.warnings == []
    # 6 images -> 3 pairs, before = odd sequence index (verified convention).
    assert len(case.pairs) == 3
    for pair in case.pairs:
        assert pair.before_url != pair.after_url
    specs = case.specs
    assert specs.age == 39
    assert specs.gender == "Female"
    assert specs.height == "5'8"
    assert specs.weight_lbs == 118
    assert specs.left_cc == 300 and specs.right_cc == 300
    assert specs.brand == "sientra"
    assert specs.profile == "moderate"


def test_barrett_parse_mentor_moderate_plus_case():
    cases = sg.barrett_parse_listing(load_fixture("barrett_case_92012.html"), "x")
    (case,) = cases
    specs = case.specs
    assert specs.age == 24
    assert specs.left_cc == 250 and specs.right_cc == 250
    assert specs.brand == "mentor"
    assert specs.profile == "moderate-plus"
    # 'Smooth Silicone' does not document a shape; it must not be invented.
    assert specs.shape is None


@pytest.mark.parametrize(
    "filename,case_id,expected",
    [
        ("64dfd_Patient%20%2394712.webp", "94712", None),
        ("64dfd_Patient%20%2394712%20(4).webp", "94712", 4),
        ("683e_50342%20BAM.jpg", "50342", None),
        ("683e_50342%20BAM%20(5).jpg", "50342", 5),
        ("67d9_11.webp", "60932", 11),
        ("6896_665421.webp", "66542", 1),
        ("6864_10924.webp", "42901", None),  # bare number unrelated to case id
    ],
)
def test_barrett_image_index(filename, case_id, expected):
    assert sg._barrett_image_index(filename, case_id) == expected


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Right: 185cc Mini Motiva; Left: 170cc Mini Motiva", (170.0, 185.0)),
        ("L 240cc, R 265cc", (240.0, 265.0)),
        ("R 270 filled to 285cc, L 300 filled to 285cc", (285.0, 285.0)),
        ("270cc", (270.0, 270.0)),
        ("250cc Bilateral Mentor Moderate Profile Plus", (250.0, 250.0)),
        ("300cc (R) 270cc (L) Bilateral Mentor", (270.0, 300.0)),
        ("320 (L) 300cc (R) Bilateral Sientra", (320.0, 300.0)),
        ("350cc on the right. 325cc on the left.", (325.0, 350.0)),
        ("350cc bilateral implants. 1,600cc of fat was removed", (350.0, 350.0)),
        ("no volumes mentioned", (None, None)),
    ],
)
def test_parse_fill_volumes(text, expected):
    assert sg.parse_fill_volumes(text) == expected


def test_resolve_view():
    front = sg.ImagePair("front", "b", "a", view_hint="front")
    oblique = sg.ImagePair("oblique", "b", "a", view_hint="oblique")
    pair1 = sg.ImagePair("pair1", "b", "a")
    assert sg.resolve_view(front, {}) == ("front", None)
    assert sg.resolve_view(oblique, {}) == (None, None)
    assert sg.resolve_view(oblique, {"laterality": "left"})[0] == "oblique-left"
    assert sg.resolve_view(pair1, {}) == (None, None)
    assert sg.resolve_view(pair1, {"pairs": {"pair1": {"view": "side-right"}}})[0] == "side-right"


def test_build_meta_full():
    specs = sg.kolker_parse_case(load_fixture("case_02.html"), "02", "x").specs
    meta = sg.build_meta(
        "drkolker-02-front", "front", specs,
        {"clothing": "nude"}, {}, None, "drkolker-agreement-2026-08",
    )
    assert meta["pair_id"] == "drkolker-02-front"
    assert meta["view"] == "front"
    assert meta["shape"] == "round"
    assert meta["volume_cc"] == 252
    assert meta["clothing"] == "nude"
    assert meta["consent_ref"] == "drkolker-agreement-2026-08"
    assert "brand" not in meta  # undocumented optional fields are omitted
    assert "asymmetric" in meta["notes"]


def test_build_meta_undocumented_shape_is_unknown_not_omitted():
    # shape is required; when the clinic does not document it we emit the
    # schema's 'unknown' rather than dropping the pair.
    specs = sg.kolker_parse_case(load_fixture("case_01.html"), "01", "x").specs
    assert specs.shape is None  # Motiva case: shape never stated
    meta = sg.build_meta("drkolker-01-front", "front", specs, {}, {}, None,
                         "drkolker-agreement-2026-08")
    assert meta["shape"] == "unknown"


def test_kolker_list_cases():
    html = (
        '<a href="/gallery/breast/breast-augmentation/02/">x</a>'
        '<a href="/gallery/breast/breast-augmentation/10/">y</a>'
        '<a href="/gallery/breast/breast-augmentation/01/#nav">z</a>'
    )
    assert sg.kolker_list_cases(html) == ["01", "02", "10"]


# ---------------------------------------------------------------------------
# Notes hygiene
# ---------------------------------------------------------------------------


def test_notes_omitted_when_no_clinical_description():
    # Cases with no patient-details block (real Kolker cases 03, 83-105) must
    # not emit a notes field containing only the 'view labels' boilerplate.
    specs = sg.kolker_parse_case(load_fixture("case_no_details.html"), "03", "x").specs
    assert not specs.summary and not specs.fields
    meta = sg.build_meta(
        "drkolker-03-side-left", "side-left", specs,
        {"laterality": "left", "clothing": "nude"}, {},
        "laterality from visual inspection of downloaded images",
        "drkolker-agreement-2026-08",
    )
    assert "notes" not in meta


def test_notes_keep_view_label_provenance_with_description():
    specs = sg.kolker_parse_case(load_fixture("case_02.html"), "02", "x").specs
    meta = sg.build_meta(
        "drkolker-02-side-left", "side-left", specs, {}, {},
        "laterality from visual inspection of downloaded images",
        "drkolker-agreement-2026-08",
    )
    assert "Clinic description" in meta["notes"]
    assert "view labels" in meta["notes"]
