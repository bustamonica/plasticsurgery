"""Tests for arps_gallery.py against real (trimmed) arplasticsurgery.com.au markup.

Fixtures under fixtures/gallery/arps_*.html are verbatim `div.allslider` case
blocks with the duplicate popup markup removed. No patient images are stored.

Each fixture pins one thing the gallery does that a single-shape parser gets
wrong; the fixture's own header comment says which.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import arps_gallery as ag  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"
SOURCE = "https://arplasticsurgery.com.au/breast-augmentation-gallery/"


def load_case(case_id: str) -> sg.CaseData:
    html = (FIXTURES / f"arps_case_{case_id}.html").read_text()
    cases = ag.arps_parse_listing(html, SOURCE)
    assert len(cases) == 1, f"fixture {case_id} should hold exactly one case"
    return cases[0]


# ---------------------------------------------------------------------------
# Enumeration: case id, pairs, and the consent-scoped listing URL
# ---------------------------------------------------------------------------


def test_case_id_is_the_wordpress_post_id_not_the_listing_position():
    """'patient-gallery-4694-1' -> '4694'.

    The trailing number is the case's running position across the whole
    paginated gallery, so it moves whenever a case is added; keying on it would
    rename every pair on the next collection run.
    """
    assert load_case("4694").case_id == "4694"


def test_pairs_come_from_the_page_s_own_before_after_markup():
    case = load_case("4694")
    assert [p.key for p in case.pairs] == [f"pair{i}" for i in range(1, 6)]
    assert case.pairs[0].before_url.endswith("SURGERY-10-scaled.jpg")
    assert case.pairs[0].after_url.endswith("SURGERY-5-scaled.jpg")
    # No pair is a composite or a grid cell: this gallery publishes separate
    # before and after files.
    assert not any(p.split_composite or p.grid_shape for p in case.pairs)


def test_views_are_never_read_off_the_page():
    """The alt text is one constant string for the whole gallery, so no pair
    carries a view hint and every label must come from the annotation pass."""
    assert all(p.view_hint is None for p in load_case("4694").pairs)


def test_listing_url_is_scoped_to_the_consent_signatory():
    base = "https://arplasticsurgery.com.au"
    assert ag.arps_listing_url(base, 1) == (
        base + "/breast-augmentation-gallery/?surgeon=dr-eddie-cheng")
    assert ag.arps_listing_url(base, 7) == (
        base + "/breast-augmentation-gallery/page/7/?surgeon=dr-eddie-cheng")


def test_a_page_past_the_end_of_the_listing_yields_no_cases():
    """The site answers an over-run page with HTTP 200 and an empty gallery
    rather than a 404, so the walk has to stop on emptiness, not on status."""
    html = (FIXTURES / "arps_listing_empty.html").read_text()
    assert ag.arps_parse_listing(html, SOURCE) == []


def test_a_listing_page_yields_every_case_it_renders():
    html = (FIXTURES / "arps_listing_page.html").read_text()
    assert [c.case_id for c in ag.arps_parse_listing(html, SOURCE)] == ["4694", "2971"]


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id,expected", [
    ("4694", 400),   # '400 cc' in prose, with a space
    ("2866", 325),   # chart field 'Implant size: 325cc'
    ("2606", 455),   # 'UHP 455 CC' - unit after a profile token
    ("5190", 275),   # '275CC' with no space, in the headline and the prose
    ("2599", 325),   # chart written as sentences: 'Implant Size: 325CC.'
])
def test_volume_from_each_published_shape(case_id, expected):
    assert sg.volume_cc(load_case(case_id).specs) == expected


def test_asymmetric_volumes_average_both_sides():
    """'Left 450cc / Right 425cc' must yield 438, not 425.

    The shared parse_fill_volumes() consumes everything up to the next comma as
    the FIRST side's segment and so never reaches the second side; this gallery
    writes almost every asymmetric case that way, and taking one side would put
    a 25cc error straight into the training label.
    """
    specs = load_case("2864").specs
    assert (specs.left_cc, specs.right_cc) == (450, 425)
    assert sg.volume_cc(specs) == 438


def test_sided_volumes_are_read_in_both_written_orders():
    assert ag.arps_sided_volumes("Left 450cc / Right 425cc") == (450, 425)
    assert ag.arps_sided_volumes("Implant Size: Left 350 cc/ Right: 400 cc") == (350, 400)
    assert ag.arps_sided_volumes("L 350cc R 400cc") == (350, 400)
    assert ag.arps_sided_volumes("400cc right 375cc left") == (375, 400)


def test_a_cup_size_beside_a_side_word_is_not_a_volume():
    """'Pre-op bra size: Right-A, Left-B' names both sides and no volume."""
    assert ag.arps_sided_volumes("Pre-op bra size: Right-A, Left-B") == (None, None)


def test_one_side_only_is_left_ambiguous_for_the_caller_s_fallback():
    assert ag.arps_sided_volumes("Left 350cc smooth round") == (None, None)


def test_no_volume_is_recorded_when_the_case_documents_none():
    """Case 2971 publishes 'B cup, Ht: 165 cm, Wt: 66 Kg' and nothing else. A
    bare number in free prose is not a volume, and 165/66 must not become one."""
    specs = load_case("2971").specs
    assert (specs.left_cc, specs.right_cc) == (None, None)
    assert sg.volume_cc(specs) is None


def test_height_and_weight_are_not_mistaken_for_volumes():
    specs = load_case("2910").specs
    assert sg.volume_cc(specs) is None
    assert specs.height_cm == 160
    assert specs.weight_kg == 54


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_uhp_decodes_to_extra_high():
    """The captain's 2026-08-19 ruling maps UHP to extra-high, and this is the
    only extra-high case in the gallery - the corpus's weakest axis."""
    assert load_case("2606").specs.profile == "extra-high"


def test_mentor_m_plus_decodes_to_moderate_plus():
    assert load_case("5190").specs.profile == "moderate-plus"
    assert load_case("5190").specs.brand == "mentor"


def test_spelled_out_profiles_still_come_from_the_shared_table():
    assert load_case("4694").specs.profile == "high"


def test_profile_is_omitted_when_the_case_documents_none():
    """Never defaulted: an unrecorded profile must not become a moderate one."""
    assert load_case("2971").specs.profile is None
    assert load_case("2910").specs.profile is None


# ---------------------------------------------------------------------------
# Purity: screened on the case TEXT, never the filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id,procedure", [
    ("2611", "fat transfer"),
    ("2608", "accessory breast tissue excision"),
])
def test_a_combined_case_emits_no_pairs(case_id, procedure):
    case = load_case(case_id)
    assert case.pairs == []
    assert any(procedure in w and "excluded by captain ruling" in w
               for w in case.warnings)


def test_a_pure_case_keeps_its_pairs():
    case = load_case("4694")
    assert len(case.pairs) == 5
    assert not any("not pure breast augmentation" in w for w in case.warnings)


def test_a_filename_naming_another_procedure_warns_but_never_excludes():
    """Case 2971's BEFORE is published as '...-Abdominoplasty-7-...jpg' while
    its own text documents a breast augmentation. The slug is what let 86
    combined cases through at the Etna clinics, so it is reported and the case
    text is what decides."""
    case = load_case("2971")
    assert len(case.pairs) == 1
    assert any("Abdominoplasty" in w for w in case.warnings)
    assert not any("excluded by captain ruling" in w for w in case.warnings)


# ---------------------------------------------------------------------------
# Chart metadata (curation only - never reaches a caption)
# ---------------------------------------------------------------------------


def test_chart_fields_split_on_the_next_label_not_on_the_comma():
    """'Placement: Sub-pectoral, dual plane, Incision: Inframammary folds'
    carries a comma INSIDE the placement value."""
    specs = load_case("2866").specs
    assert specs.fields["Placement"] == "Sub-pectoral, dual plane"
    assert specs.placement == "dual-plane"
    assert specs.incision == "inframammary"
    assert specs.age == 27


def test_months_post_op_is_not_taken_from_an_age_field():
    """'Age: 27 years old' is not a post-op interval."""
    assert load_case("2866").specs.months_post_op is None
    assert load_case("4694").specs.months_post_op == 6


def test_weeks_post_op_converts_to_months():
    assert load_case("2599").specs.months_post_op == pytest.approx(1.8, abs=0.05)


# ---------------------------------------------------------------------------
# Integrity checks the gallery turned out to need
# ---------------------------------------------------------------------------


def test_one_photograph_used_by_two_pairs_is_reported():
    """Case 4578 publishes its oblique-right AFTER as the after of both pair2
    and pair4, so pair2 is a before and an after of two different views. Every
    downstream gate passes it: both halves are clean, full-resolution,
    correctly-labelled photographs of the same patient."""
    case = load_case("4578")
    shared = [w for w in case.warnings if "same photograph as" in w]
    assert len(shared) == 1
    assert "pair4 after" in shared[0] and "pair2 after" in shared[0]


def test_two_cases_publishing_all_but_the_same_text_are_reported():
    """2610 and 2603 differ by one word ('with'/'using') and are two different
    patients, so one of them carries the other's volume - the training label."""
    html = "\n".join((FIXTURES / f"arps_case_{c}.html").read_text()
                     for c in ("2610", "2603"))
    cases = ag.arps_parse_listing(html, SOURCE)
    ag.arps_flag_shared_case_text(cases)
    assert {c.case_id for c in cases} == {"2610", "2603"}
    for case in cases:
        assert any("all but the same Case Details text" in w
                   for w in case.warnings), case.case_id


def test_distinct_case_texts_are_not_reported_as_shared():
    html = "\n".join((FIXTURES / f"arps_case_{c}.html").read_text()
                     for c in ("4694", "2866"))
    cases = ag.arps_parse_listing(html, SOURCE)
    ag.arps_flag_shared_case_text(cases)
    assert not any("all but the same Case Details text" in w
                   for case in cases for w in case.warnings)


# ---------------------------------------------------------------------------
# The watermark crop
# ---------------------------------------------------------------------------


def test_bottom_crop_is_a_fraction_of_width():
    """The mark is drawn proportional to frame width, and this gallery
    publishes the two halves of a pair at different resolutions (case 5179:
    790x1186 before, 1707x2560 after). A fraction of width crops both to the
    same FRAMING; a fixed pixel count would not."""
    from PIL import Image
    import io

    def jpeg(w, h):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (180, 160, 150)).save(buf, format="JPEG")
        return buf.getvalue()

    small = Image.open(io.BytesIO(sg.crop_bottom(jpeg(790, 1186), 0.10)))
    large = Image.open(io.BytesIO(sg.crop_bottom(jpeg(1707, 2560), 0.10)))
    assert small.size == (790, 1107)
    assert large.size == (1707, 2389)
    # Same framing: the aspect ratios stay within a rounding pixel of each other.
    assert small.width / small.height == pytest.approx(
        large.width / large.height, abs=0.002)


def test_no_crop_returns_the_delivered_bytes_untouched():
    data = b"not even an image"
    assert sg.crop_bottom(data, 0) is data


def test_arps_is_registered_with_its_measured_crop():
    cfg = sg.CLINICS["arps"]
    assert cfg.kind == "arps"
    assert cfg.consent_ref == "arps-agreement-2026-08-25"
    # Measured mark top edge is 3.8%-7.2% of width above the bottom.
    assert cfg.bottom_crop_frac == 0.10


def test_clinics_without_a_measured_mark_are_not_cropped():
    assert all(cfg.bottom_crop_frac == 0
               for slug, cfg in sg.CLINICS.items() if slug != "arps")
