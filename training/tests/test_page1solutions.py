"""Tests for the Page 1 Solutions gallery parser (page1solutions.py).

Fixtures under fixtures/gallery/page1_* are trimmed excerpts of real drbandy.com
pages - the `#patient-info` spec block and the `div.row.image-pair` image tags.
No patient images are stored.

Each of drbandy's three published chart layouts has a fixture, because a
single-layout parser silently loses volumes: reading only layout A drops the 21
`Implant Size Right/Left` cases, only A+B drops the three unlabelled ones, and
reading the side words the way the shared narrative parser does swaps left and
right on every `500cc left & 575cc right` case.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import page1solutions as p1  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def parse(case_id: str) -> sg.CaseData:
    return p1.page1_parse_case(
        load(f"page1_bandy_case_{case_id}.html"), case_id,
        f"https://www.drbandy.com/before-after-photos/breast-augmentation/{case_id}/")


# ---------------------------------------------------------------------------
# Listing: case keys
# ---------------------------------------------------------------------------


def test_list_cases_reads_the_slug_not_the_patient_number():
    # 554 publishes 'Patient #: 5540'; the slug is what addresses the page.
    assert p1.page1_list_cases(load("page1_bandy_listing.html")) == [
        "5342", "auto-draft", "554"]


def test_full_res_strips_the_wordpress_size_suffix():
    assert p1.page1_full_res(
        "https://www.drbandy.com/wp-content/uploads/sites/276/2024/09/"
        "Breast-Augmentation-Patient-17-Before-_1-420x315.jpg"
    ) == ("https://www.drbandy.com/wp-content/uploads/sites/276/2024/09/"
          "Breast-Augmentation-Patient-17-Before-_1.jpg")
    # A size-like token that is not the suffix immediately before the
    # extension is left alone.
    assert p1.page1_full_res("/a/b-2x2-detail.png") == "/a/b-2x2-detail.png"


# ---------------------------------------------------------------------------
# Images: five positional pairs, before then after, at full resolution
# ---------------------------------------------------------------------------


def test_case_publishes_five_positional_pairs_before_then_after():
    case = parse("5325")
    assert [pair.key for pair in case.pairs] == [
        "pair1", "pair2", "pair3", "pair4", "pair5"]
    assert case.pairs[0].before_url.endswith("Patient-17-Before-_1.jpg")
    assert case.pairs[0].after_url.endswith("Patient-17-After-_2.jpg")
    assert case.pairs[4].before_url.endswith("Patient-17-Before-4-_9.jpg")
    assert case.pairs[4].after_url.endswith("Patient-17-After-4-_10.jpg")
    # Lazy-loaded: the parser must read data-src, never the base64 placeholder.
    assert not any("base64" in pair.before_url for pair in case.pairs)


def test_pairs_carry_no_view_hint_so_a_view_needs_an_annotation():
    case = parse("5325")
    assert all(pair.view_hint is None for pair in case.pairs)
    assert sg.resolve_view(case.pairs[0], {}) == (None, None)
    annotations = {"pairs": {"pair1": {"view": "front"}}}
    view, source = sg.resolve_view(case.pairs[0], annotations)
    assert view == "front"
    assert source == "visual inspection of downloaded images"


def test_laterality_is_never_taken_from_pair_position():
    # A case-level laterality annotation must not turn a positional key into a
    # sided view: resolve_view only applies it to an 'oblique'/'side' hint,
    # and this family publishes none.
    case = parse("5325")
    assert sg.resolve_view(case.pairs[3], {"laterality": "left"}) == (None, None)


# ---------------------------------------------------------------------------
# Chart layouts: volumes
# ---------------------------------------------------------------------------


def test_layout_a_labelled_sides():
    case = parse("5325")
    assert (case.specs.left_cc, case.specs.right_cc) == (400.0, 425.0)
    assert sg.volume_cc(case.specs) == 412
    assert case.warnings == []


def test_layout_a_symmetric_single_volume():
    case = parse("5342")
    assert (case.specs.left_cc, case.specs.right_cc) == (550.0, 550.0)
    assert sg.volume_cc(case.specs) == 550


def test_layout_a_side_carrying_labels():
    case = parse("3633")
    assert (case.specs.left_cc, case.specs.right_cc) == (350.0, 375.0)
    assert case.specs.fields["Implant Type"] == "Saline"


def test_layout_b_label_in_its_own_element():
    # '<strong>Implant Size:</strong> 350cc' - cutting the paragraph at text
    # nodes instead of at <br> separates the label from its value and loses it.
    case = parse("3636")
    assert case.specs.fields.get("Implant Size") == "350cc"
    assert sg.volume_cc(case.specs) == 350


def test_layout_c_unlabelled_lines_put_the_volume_before_the_side():
    case = parse("554")
    assert (case.specs.left_cc, case.specs.right_cc) == (465.0, 545.0)
    assert sg.volume_cc(case.specs) == 505


def test_layout_c_bilateral_fill_volume():
    case = parse("503")
    assert (case.specs.left_cc, case.specs.right_cc) == (650.0, 650.0)
    assert case.specs.incision == "transaxillary"
    assert case.specs.placement == "submuscular"


def test_fill_volume_wins_over_the_shell_size():
    # '650cc bags filled to 675 on left and 750cc on right'
    case = parse("5337")
    assert (case.specs.left_cc, case.specs.right_cc) == (675.0, 750.0)
    assert sg.volume_cc(case.specs) == 712


def test_two_volumes_with_no_side_named_are_averaged_not_assigned():
    case = parse("auto-draft")
    # 385cc and 405cc, neither line saying which breast: recording them as
    # left and right would invent a laterality, and volume_cc is the same
    # average either way.
    assert (case.specs.left_cc, case.specs.right_cc) == (395.0, 395.0)
    assert sg.volume_cc(case.specs) == 395
    # Both published figures survive verbatim, so the notes still carry them.
    assert case.specs.fields["Implant Size"] == "385cc / 405cc"


def test_a_case_with_no_chart_publishes_no_volume():
    case = parse("5321")
    assert sg.volume_cc(case.specs) is None
    assert case.warnings == ["no chart text published"]
    # The photographs are still parsed; it is the missing volume that makes the
    # pairs unemittable (volume_cc is a required schema field).
    assert len(case.pairs) == 5


@pytest.mark.parametrize("label,expected", [
    ("Implant Size", True),
    ("Implant Size Right", True),
    ("Right Implant", True),
    ("Implant Left", True),
    ("Left Implant Size", True),
    ("Fill Volume", True),
    ("Implant Type", False),
    ("Implant Types", False),
    ("Placement", False),
    ("Notes", False),
    ("Patient #", False),
])
def test_is_volume_label(label, expected):
    assert p1.is_volume_label(label) is expected


# ---------------------------------------------------------------------------
# Chart layouts: everything else
# ---------------------------------------------------------------------------


def test_post_pectoral_reads_as_submuscular_and_notes_give_months_post_op():
    case = parse("5325")
    assert case.specs.placement == "submuscular"
    assert case.specs.months_post_op == 6.0


def test_weeks_post_op_convert_to_months():
    case = parse("5337")
    assert case.specs.months_post_op == pytest.approx(1.38, abs=0.01)


def test_age_bucket_is_never_read_as_an_age():
    case = parse("5325")
    assert case.specs.age is None
    assert case.specs.fields["Age"] == "26 - 30"


def test_mentor_xtra_is_a_product_line_not_a_profile():
    # Captain's 2026-08-19 ruling: 'Xtra' does not decode to extra-high.
    case = parse("554")
    assert case.specs.brand == "mentor"
    assert case.specs.profile is None


def test_no_case_in_this_gallery_publishes_a_profile_or_a_shape():
    for case_id in ("5325", "5342", "3633", "3636", "554", "503", "5337"):
        case = parse(case_id)
        assert case.specs.profile is None
        assert case.specs.shape is None


def test_patient_number_and_procedure_come_off_the_list_block():
    case = parse("554")
    assert case.specs.fields["Patient #"] == "5540"
    assert case.specs.fields["Procedure"] == "Breast Augmentation"


# ---------------------------------------------------------------------------
# Purity: screened on the case's text
# ---------------------------------------------------------------------------


def test_combined_procedure_case_is_excluded():
    case = p1.page1_parse_case(
        load("page1_bandy_case_combined.html"), "5301",
        "https://www.drbandy.com/before-after-photos/breast-lift-with-augmentation/5301/")
    assert case.pairs == []
    assert any("not pure breast augmentation" in w for w in case.warnings)


@pytest.mark.parametrize("text", [
    "Procedure: Breast Lift With Augmentation",
    "Procedure: Mommy Makeover",
    "Breast Augmentation and Lift",
    "Breast Augmentation with a Lift",
    "Procedure: Breast Implant Exchange With Breast Lift",
    "Augmentation/Mastopexy",
    "Breast Augmentation and Abdominoplasty",
    "Implant Removal and Replacement",
    "Fat Transfer To Breasts",
    "Breast Reduction With Breast Augmentation",
])
def test_combined_procedure_vocabulary_matches(text):
    assert p1.COMBINED_PROCEDURE_RE.search(text)


@pytest.mark.parametrize("text", [
    "Procedure: Breast Augmentation",
    "Implant Size: 550cc Implant Type: Silicone Placement: Post Pectoral",
    "Notes: After photos are 6 weeks post op",
    "Patient #5325 Breast Augmentation Before and After Photos Newport Beach, CA",
])
def test_pure_augmentation_text_is_not_screened_out(text):
    assert not p1.COMBINED_PROCEDURE_RE.search(text)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_bandy_is_registered_with_a_traceable_consent_ref():
    cfg = sg.CLINICS["bandy"]
    assert cfg.kind == "page1solutions"
    assert cfg.consent_ref == "bandy-agreement-2026-08-25"
    assert cfg.gallery_paths == ["/before-after-photos/breast-augmentation/"]
