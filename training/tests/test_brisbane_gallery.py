"""Tests for the Brisbane Cosmetic Clinic Elementor gallery parser.

`fixtures/gallery/brisbane_listing_excerpt.html` is the real page body (the
`data-elementor-type="wp-page"` container) with scripts, styles and Elementor's
`data-settings` JSON stripped; nothing else is altered and no images are stored.
It carries all ten published cases, the boilerplate paragraph BEFORE the first
gallery and the two paragraphs AFTER the last caption, so both edges of the
caption pairing are exercised on the real page.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import brisbane_gallery as bg  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"
PAGE_ORDER = ["1", "14", "12", "22", "8", "7", "6", "10", "18", "3"]


@pytest.fixture(scope="module")
def listing():
    return (FIXTURES / "brisbane_listing_excerpt.html").read_text()


@pytest.fixture(scope="module")
def cases(listing):
    return {c.case_id: c for c in bg.brisbane_parse_listing(listing, "https://x/g/")}


def test_every_published_case_is_returned_in_page_order(listing):
    """Ten gallery widgets and no declared total: the widget count IS the
    enumeration check. Keyed by the filename number, which is not page order."""
    assert [c.case_id for c in bg.brisbane_parse_listing(listing, "x")] == PAGE_ORDER


def test_caption_is_the_text_that_FOLLOWS_its_gallery(cases):
    """Checked against the photographs: the `_22` fronts show a pectus deformity
    and the `_6` before-front shows the patient's right breast far larger.
    Pairing the caption BEFORE each gallery would give every case its
    neighbour's implant and the first case the page's boilerplate."""
    assert "Pectus excavatum" in cases["22"].specs.summary
    assert "right breast twice size of the left" in cases["6"].specs.summary
    assert cases["1"].specs.summary.startswith("23 yrs A cup")
    assert "brands, sizes, shapes" not in cases["1"].specs.summary
    assert "Pseudoptosis" in cases["3"].specs.summary


def test_photographs_come_from_data_thumbnail_not_img(listing):
    items, _ = bg.brisbane_cases(listing)[0]
    assert [i["label"] for i in items] == [f"Breast augmentation 1{x}" for x in "abcd"]
    assert all(i["url"].endswith(".jpg") and "/wp-content/uploads/" in i["url"]
               for i in items)


def test_pairs_are_a_c_and_b_d_with_the_before_first(cases):
    """a before-front, b before-oblique, c after-front, d after-oblique -
    verified by eye on all ten cases; the page documents none of it."""
    for case in cases.values():
        assert [p.key for p in case.pairs] == ["ac", "bd"]
        ac, bd = case.pairs
        assert ac.before_url.endswith(f"_{case.case_id}a.jpg")
        assert ac.after_url.endswith(f"_{case.case_id}c.jpg")
        assert bd.before_url.endswith(f"_{case.case_id}b.jpg")
        assert bd.after_url.endswith(f"_{case.case_id}d.jpg")
        assert not ac.split_composite and ac.view_hint is None


# ---------------------------------------------------------------------------
# Volumes: read only where the clinic wrote the unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id,expected", [
    ("14", (330.0, 330.0)),
    ("12", (305.0, 305.0)),
    ("7", (345.0, 345.0)),
    ("18", (390.0, 390.0)),
    ("3", (330.0, 330.0)),
    # Sided, with a style number between the code and the size.
    ("6", (330.0, 245.0)),
    # Sided Motiva codes that run straight into the size.
    ("22", (300.0, 285.0)),
])
def test_volume_is_the_figure_before_cc(cases, case_id, expected):
    specs = cases[case_id].specs
    assert (specs.left_cc, specs.right_cc) == expected


@pytest.mark.parametrize("case_id", ["1", "8", "10"])
def test_a_size_inside_a_unitless_model_code_is_not_decoded(cases, case_id):
    """`ERSF – 315Q`, `CPG 323-300`, `CPG 323-390 left and right breasts`: the
    Natrelle `SRM-445` precedent - decoding a model code is a captain call."""
    case = cases[case_id]
    assert sg.volume_cc(case.specs) is None
    assert any("model code, with no unit" in w for w in case.warnings)


def test_the_style_number_is_never_read_as_the_volume():
    assert bg.brisbane_volumes("CPG 323-345 cc") == (345.0, 345.0)
    assert bg.brisbane_volumes("CPG 323-390 left and right breasts") == (None, None)
    assert bg.brisbane_volumes("ERSF – 315Q (round, full volume)") == (None, None)


# ---------------------------------------------------------------------------
# Profile, shape, brand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id,profile", [
    ("14", "moderate"), ("7", "high"), ("18", "high"),
    ("1", None),   # 'full volume' is not a profile word
    ("22", None),  # 'round low profile' has no schema value
    ("6", None),   # 'implants of different sizes and profiles' names none
])
def test_profile_comes_from_the_clinics_own_gloss(cases, case_id, profile):
    assert cases[case_id].specs.profile == profile


def test_shape_from_the_gloss_and_brand_never_from_a_style_code(cases):
    assert cases["1"].specs.shape == "round"
    assert cases["14"].specs.shape == "teardrop"
    assert {c.specs.brand for c in cases.values()} == {"unknown"}


def test_age_is_read(cases):
    assert cases["22"].specs.age == 31
    assert cases["8"].specs.age == 47


# ---------------------------------------------------------------------------
# Purity, registration, dispatch
# ---------------------------------------------------------------------------


def test_every_published_case_is_pure(cases):
    assert not any("not a pure augmentation" in w
                   for c in cases.values() for w in c.warnings)


def test_an_impure_caption_is_returned_without_pairs():
    html = ('<div class="elementor-widget" data-widget_type="gallery.default">'
            + "".join(f'<div class="e-gallery-image" data-thumbnail="/u/Breast_Augmentation_9{x}.jpg"></div>'
                      for x in "abcd")
            + '</div><div class="elementor-widget" data-widget_type="text-editor.default">'
              '<p>40 yrs augmentation with mastopexy CPG 322-330 cc</p></div>')
    (case,) = bg.brisbane_parse_listing(html, "x")
    assert case.case_id == "9" and case.pairs == []
    assert any("mastopexy" in w for w in case.warnings)


def test_brisbane_is_registered_with_its_own_kind():
    cfg = sg.CLINICS["brisbane"]
    assert cfg.kind == "brisbane"
    assert cfg.consent_ref == "brisbane-agreement-2026-08-25"
    assert cfg.base_url == "https://www.brisbanecosmetic.com.au"
    assert sum(c.kind == "brisbane" for c in sg.CLINICS.values()) == 1


def test_collect_cases_routes_brisbane_through_its_own_parser(tmp_path, listing):
    """A kind another branch already claims would send this page to a parser
    that finds zero cases (the ncps/psiw precedent)."""
    (tmp_path / "brisbane_listing.html").write_text(listing)
    cases = sg.collect_cases(sg.CLINICS["brisbane"],
                             sg.PoliteFetcher(tmp_path, delay=0, offline=True))
    assert [c.case_id for c in cases] == PAGE_ORDER
    assert sum(len(c.pairs) for c in cases) == 20
