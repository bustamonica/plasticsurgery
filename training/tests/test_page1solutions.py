"""Tests for the Page 1 Solutions family parser (page1solutions.py).

One module and one kind serve four markup templates (see the module's table):
'item' (bandy), 'entry' (ncps), 'holder' (psiw) and 'pager' (ciaravino), and
the tests are grouped the same way. Every clinic is pinned to its own template
through `collect_cases`, because two of these clinics once shared a kind and
one of them silently collected zero cases.

Fixtures under fixtures/gallery/page1_* are trimmed excerpts of the real
pages - spec blocks and image tags. No patient images are stored.

Each of drbandy's three published chart layouts has a fixture, because a
single-layout parser silently loses volumes: reading only layout A drops the 21
`Implant Size Right/Left` cases, only A+B drops the three unlabelled ones, and
reading the side words the way the shared narrative parser does swaps left and
right on every `500cc left & 575cc right` case.
"""

import dataclasses
import re
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


def _fetcher(tmp_path, monkeypatch, session):
    f = sg.PoliteFetcher(tmp_path, delay=0)
    f.session = session
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    return f


def test_an_unknown_template_refuses_the_run():
    """One kind for four markups is only safe if a clinic cannot fall through
    to another template's parser: a template this module does not know stops
    the run instead of collecting nothing."""
    cfg = dataclasses.replace(sg.CLINICS["bandy"], template="inline")
    with pytest.raises(ValueError, match="template 'inline' is not one of"):
        p1.collect_cases(cfg, None)
    cfg = dataclasses.replace(sg.CLINICS["bandy"], template=None)
    with pytest.raises(ValueError, match="template None"):
        p1.collect_cases(cfg, None)


@pytest.mark.parametrize("slug,template", [
    ("bandy", "item"), ("ncps", "entry"), ("psiw", "holder"), ("ciaravino", "pager"),
])
def test_every_page1_clinic_is_one_kind_with_its_own_template(slug, template):
    cfg = sg.CLINICS[slug]
    assert (cfg.kind, cfg.template) == ("page1solutions", template)


def parse(case_id: str) -> sg.CaseData:
    return p1.item_parse_case(
        load(f"page1_bandy_case_{case_id}.html"), case_id,
        f"https://www.drbandy.com/before-after-photos/breast-augmentation/{case_id}/")


# ---------------------------------------------------------------------------
# Listing: case keys
# ---------------------------------------------------------------------------


def test_list_cases_reads_the_slug_not_the_patient_number():
    # 554 publishes 'Patient #: 5540'; the slug is what addresses the page.
    assert p1.item_list_cases(load("page1_bandy_listing.html")) == [
        "5342", "auto-draft", "554"]


def test_full_res_strips_the_wordpress_size_suffix():
    assert p1.wp_original(
        "https://www.drbandy.com/wp-content/uploads/sites/276/2024/09/"
        "Breast-Augmentation-Patient-17-Before-_1-420x315.jpg"
    ) == ("https://www.drbandy.com/wp-content/uploads/sites/276/2024/09/"
          "Breast-Augmentation-Patient-17-Before-_1.jpg")
    # A size-like token that is not the suffix immediately before the
    # extension is left alone.
    assert p1.wp_original("/a/b-2x2-detail.png") == "/a/b-2x2-detail.png"


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
    assert p1.item_is_volume_label(label) is expected


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
    # A real case from the practice's sibling breast-lift-with-augmentation
    # gallery. It publishes a full chart WITH volumes, which is the point: the
    # specs parse fine and the case is excluded anyway, on its text.
    case = p1.item_parse_case(
        load("page1_bandy_case_combined.html"), "11632",
        "https://www.drbandy.com/before-after-photos/breast-lift-with-augmentation/11632/")
    assert case.pairs == []
    assert any("not pure breast augmentation" in w for w in case.warnings)
    assert case.specs.fields["Procedure"] == "Breast Lift With Augmentation"
    # The volumes are readable; being readable is not being collectable.
    assert (case.specs.left_cc, case.specs.right_cc) == (850.0, 800.0)


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
    assert p1.ITEM_COMBINED_RE.search(text)


@pytest.mark.parametrize("text", [
    "Procedure: Breast Augmentation",
    "Implant Size: 550cc Implant Type: Silicone Placement: Post Pectoral",
    "Notes: After photos are 6 weeks post op",
    "Patient #5325 Breast Augmentation Before and After Photos Newport Beach, CA",
])
def test_pure_augmentation_text_is_not_screened_out(text):
    assert not p1.ITEM_COMBINED_RE.search(text)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_bandy_is_registered_with_a_traceable_consent_ref():
    cfg = sg.CLINICS["bandy"]
    assert cfg.kind == "page1solutions"
    assert cfg.consent_ref == "bandy-agreement-2026-08-25"
    assert cfg.gallery_paths == ["/before-after-photos/breast-augmentation/"]


# ---------------------------------------------------------------------------
# Template 'entry' (ncps): paged listing of Case # anchors -> case pages
# ---------------------------------------------------------------------------


def _page1_case(number):
    html = load(f"page1_ncps_case_{number}.html")
    return p1.entry_parse_case(
        html, number,
        "https://www.drgregpark.com/before-after-gallery-san-diego/"
        f"breast-augmentation/{number}/")


def test_page1_dispatch_routes_ncps_through_its_own_paginated_walk(tmp_path):
    """Each clinic is parsed through its OWN template of the one family parser.

    Before the family was one kind, ncps and psiw were both registered
    `kind="page1"`, so the first matching branch in collect_cases claimed both
    and ncps was enumerated with psiw's inline-listing parser - which finds no
    `div.patient-holder` here and returns zero cases, a silent-zero run that
    looks exactly like a finished collection.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ncps_listing.html").write_text(
        load("page1_ncps_listing.html"))
    for number in ("9325", "11549"):
        (cache / f"ncps_case_{number}.html").write_text(
            load("page1_ncps_case_9325.html"))
    fetcher = sg.PoliteFetcher(cache, delay=0, offline=True)

    cases = sg.collect_cases(sg.CLINICS["ncps"], fetcher)

    assert [c.case_id for c in cases] == ["9325", "11549"]
    assert all(c.pairs for c in cases)


def test_page1_dispatch_routes_psiw_through_its_own_inline_listing(tmp_path):
    """psiw keeps the inline-listing parser it was collected with."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "psiw_listing.html").write_text(
        load("page1_psiw_listing.html"))
    fetcher = sg.PoliteFetcher(cache, delay=0, offline=True)

    cases = sg.collect_cases(sg.CLINICS["psiw"], fetcher)

    expected = p1.holder_parse_listing(
        load("page1_psiw_listing.html"), PSIW_GALLERY)
    assert [c.case_id for c in cases] == [c.case_id for c in expected]
    assert cases[0].pairs[0].before_url == expected[0].pairs[0].before_url


def test_page1_listing_walk_stops_at_the_page_ceiling(tmp_path, monkeypatch):
    """A gallery that 200s past its last page must not walk forever.

    The platform normally 404s past the end, but a WordPress gallery re-serving
    the same cases on every page would keep the walk fetching: the `seen` set
    dedupes the repeats away, so the "no new cases" test never fires.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    listing = load("page1_ncps_listing.html")
    fetched = []

    class _EndlessSession:
        headers = {}

        def get(self, url, timeout=None):
            fetched.append(url)

            class R:
                status_code = 200
                content = listing.encode()

                def raise_for_status(self):
                    return None

            return R()

    monkeypatch.setattr(p1, "ENTRY_MAX_PAGES", 3)
    fetcher = _fetcher(cache, monkeypatch, _EndlessSession())

    cases = sg.collect_cases(sg.CLINICS["ncps"], fetcher)

    assert len([u for u in fetched if "/page/" in u]) == 2
    assert [c.case_id for c in cases] == ["9325", "11549"]


def test_page1_lists_cases_by_case_number_not_slug():
    """The Case # in the anchor text is the key; the URL slug is not.

    Page 1 of ncps publishes a word slug ('ideal-breast-implant-2') on a case
    whose number is 11549, so keying on the slug would give that case an id
    unrelated to every other case in the gallery.
    """
    cases = p1.entry_list_cases(load("page1_ncps_listing.html"))
    assert [number for number, _ in cases] == ["9325", "11549"]
    assert cases[1][1].endswith("/ideal-breast-implant-2/")


def test_page1_numeric_slug_is_not_the_case_number():
    """Even a numeric slug disagrees with its own case number (slug 8901 is #12688)."""
    cases = p1.entry_list_cases(load("page1_ncps_listing_p20.html"))
    assert [number for number, _ in cases] == ["12688"]
    assert cases[0][1].endswith("/8901/")


def test_page1_parses_the_standard_chart_layout():
    case = _page1_case("9325")
    specs = case.specs
    assert specs.fields["Procedure Type"] == "Breast Augmentation"
    assert sg.volume_cc(specs) == 410
    assert specs.profile == "high"
    assert specs.shape == "round"
    assert specs.placement == "dual-plane"
    assert specs.incision == "inframammary"
    assert specs.months_post_op == 3
    assert specs.weight_lbs == 135
    # 5'6" typed with PRIME/DOUBLE PRIME still converts.
    assert specs.height == "5' 6\""
    assert specs.height_cm == 167.6


def test_page1_parses_the_bare_label_chart_layout():
    """Same site, second layout: 'Profile'/'Shape' without the 'Implant' prefix,
    and a 'Procedure Type:' whose value the template put on the next line."""
    case = _page1_case("10494")
    specs = case.specs
    assert specs.fields["Procedure Type"] == "Gummy Bear Breast Augmentation"
    assert specs.fields["Profile"] == "Medium Height Moderate Profile Implant"
    assert specs.profile == "moderate"
    assert specs.shape == "teardrop"
    assert specs.placement == "subfascial"
    assert sg.volume_cc(specs) == 215
    assert case.warnings == []


def test_page1_pairs_images_positionally_at_full_resolution():
    case = _page1_case("9325")
    assert len(case.pairs) == 2  # fixture keeps the first 2 of the page's 5
    for pair in case.pairs:
        assert "-300x300" not in pair.before_url
        assert "-300x300" not in pair.after_url
    assert case.pairs[0].before_url.endswith("1of10.jpg")
    assert case.pairs[0].after_url.endswith("2of10.jpg")
    assert case.pairs[1].before_url.endswith("3of10.jpg")


def test_wp_original_strips_only_the_size_suffix():
    assert p1.wp_original("/files/2017/08/56674855-1of10-300x300.jpg") == (
        "/files/2017/08/56674855-1of10.jpg")
    assert p1.wp_original("/files/a-2of10.jpg") == "/files/a-2of10.jpg"


def test_page1_rejects_a_chart_documented_revision():
    case = _page1_case("7374")
    assert case.pairs == []
    assert any("chart/revision" in w for w in case.warnings)


def test_page1_rejects_a_lift_that_only_the_narrative_reports():
    """Case 2647's chart says plain 'Breast Augmentation'; the mastopexy is in
    the prose, which is why the narrative is screened at all."""
    case = _page1_case("2647")
    assert case.specs.fields["Procedure Type"] == "Breast Augmentation"
    assert case.pairs == []
    assert any("narrative/combined-procedure:lift" in w for w in case.warnings)


def test_page1_keeps_a_case_that_declined_a_lift():
    """'She did not want to have breast lift ... and opted for a breast
    augmentation alone' names a lift without reporting one."""
    case = _page1_case("15246")
    assert "breast lift" in case.specs.summary.lower()
    assert case.warnings == []
    assert len(case.pairs) == 2


def test_page1_keeps_a_case_whose_prose_mentions_a_previous_tumor_removal():
    """A prior unrelated operation is not this operation - and the case also
    documents a different implant size per side."""
    case = _page1_case("9756")
    assert "tumor removal" in case.specs.summary.lower()
    assert case.warnings == []
    assert case.specs.left_cc == 375
    assert case.specs.right_cc == 350
    # 362.5 -> 362: volume_cc uses Python's round(), which is banker's rounding.
    assert sg.volume_cc(case.specs) == 362


def test_page1_decodes_a_bare_projection_word_in_a_labelled_profile_field():
    assert _page1_case("25590").specs.profile == "high"


def test_page1_leaves_a_per_side_profile_undocumented():
    """'Moderate+ Left, High Profile Right' documents two profiles and the
    schema records one, so neither is written."""
    case = _page1_case("16152")
    assert "Left" in p1.entry_profile_field(case.specs.fields)
    assert case.specs.profile is None


@pytest.mark.parametrize("value,expected", [
    ("High", "high"),
    ("moderate plus", "moderate-plus"),
    ("Moderate High", None),   # not a term in the captain's profile mapping
    ("Classic Profile", None),
    ("See Below", None),
])
def test_page1_bare_profile_decodes_only_documented_terms(value, expected):
    assert p1.entry_bare_profile(value) == expected


@pytest.mark.parametrize("value,profile", [
    ("Full", "high"), ("Extra Full", "extra-high"),
])
def test_entry_bare_full_ladder(value, profile):
    assert p1.entry_bare_profile(value) == profile


# ---------------------------------------------------------------------------
# Template 'holder' (psiw): markup contract, spec layouts, purity screen
# ---------------------------------------------------------------------------

PSIW_GALLERY = "https://www.plasticsurgerynow.com/gallery/breast-procedures/augmentation/"


@pytest.fixture(scope="module")
def psiw_cases():
    html = load("page1_psiw_listing.html")
    return {c.case_id: c for c in p1.holder_parse_listing(html, PSIW_GALLERY)}


def test_page1_reads_lazyloaded_relative_images(psiw_cases):
    """Nothing carries a real `src` until the lazyloader runs, and the paths
    are relative to the GALLERY page rather than the site root."""
    case = psiw_cases["01"]
    assert [p.key for p in case.pairs] == ["pair1", "pair2", "pair3"]
    assert case.pairs[0].before_url == PSIW_GALLERY + "01/01.jpg"
    assert case.pairs[0].after_url == PSIW_GALLERY + "01/02.jpg"
    assert case.pairs[2].before_url == PSIW_GALLERY + "01/05.jpg"


def test_page1_odd_image_is_before_and_even_is_after(psiw_cases):
    for case in psiw_cases.values():
        for pair in case.pairs:
            assert pair.before_url.endswith(("01.jpg", "03.jpg", "05.jpg", "07.jpg",
                                             "09.jpg"))
            assert pair.after_url.endswith(("02.jpg", "04.jpg", "06.jpg", "08.jpg",
                                            "10.jpg"))


def test_page1_case_key_is_the_folder_not_the_clinics_case_number(psiw_cases):
    """`Case # NNN` repeats across different patients, so it cannot be the key."""
    assert psiw_cases["01"].specs.fields["Case #"] == "DF029"
    assert "Case #" not in psiw_cases["01"].specs.summary


def test_page1_no_view_is_derivable_from_the_page(psiw_cases):
    """Image numbering is positional; nothing on the page documents a view."""
    for case in psiw_cases.values():
        for pair in case.pairs:
            assert pair.view_hint is None
            assert sg.resolve_view(pair, {}) == (None, None)


# -- volumes ---------------------------------------------------------------


@pytest.mark.parametrize("case_id,left,right,avg", [
    # Layout C, tab-delimited chart: 'Implant size: Left: 375cc\t\tRight: 350cc'
    ("22", 375, 350, 362),
    # Layout B, run-on chart: 'Implant Size (Left): 275 cc Implant Size (Right): 275 cc'
    ("26", 275, 275, 275),
    # Chart asymmetry the shared reader used to lose entirely.
    ("70", 300, 400, 350),
    ("77", 325, 400, 362),
    ("94", 400, 440, 420),
    # Narrative, volume immediately followed by its side.
    ("11", 405, 360, 382),
    ("12", 350, 325, 338),
    ("84", 225, 250, 238),
    # Narrative, '<side> side <volume>'.
    ("31", 375, 405, 390),
    ("45", 340, 320, 330),
])
def test_page1_reads_both_sides_of_an_asymmetric_case(psiw_cases, case_id,
                                                      left, right, avg):
    """The shared reader's prefix branch consumes the rest of the line as one
    segment, so a two-sided chart loses its right value and reports the left
    figure as the average; its narrative branch assigns '405 cc left' to the
    right. Sixteen of this clinic's 104 cases are asymmetric, so both misreads
    change the caption's cc."""
    specs = psiw_cases[case_id].specs
    assert (specs.left_cc, specs.right_cc) == (left, right)
    assert sg.volume_cc(specs) == avg


@pytest.mark.parametrize("case_id", ["52", "54"])
def test_page1_bare_number_in_free_prose_is_not_a_volume(psiw_cases, case_id):
    """'MP gel 200 bilaterally' and 'Breast Augmentation 350 R 300' name no
    unit and sit in no labelled implant-size field, so they yield nothing."""
    assert sg.volume_cc(psiw_cases[case_id].specs) is None


def test_page1_cup_size_closes_the_sided_block():
    """A bare 'Left:'/'Right:' is an implant size only inside the sided block
    an 'Implant size' label opens. Once 'Cup Size' has closed it, a later bare
    side label is a cup measurement and must not be read back as a volume."""
    assert p1.holder_parse_volumes(
        "Implant size: Left: 375cc Right: 350cc "
        "Cup Size: Left: 340 Right: 300") == (375, 350)
    # With no implant-size label at all, a bare sided number is not a volume.
    assert p1.holder_parse_volumes("Cup Size: Left: 340 Right: 300") == (None, None)


# -- profiles --------------------------------------------------------------


@pytest.mark.parametrize("case_id,profile", [
    ("22", "moderate-plus"),   # 'Moderate Profile Plus', spelled out
    ("26", "moderate"),        # 'Moderate Profile'
    ("55", "moderate-plus"),   # 'MPP gel'
    ("52", "moderate"),        # 'MP gel'
    ("70", None),              # chart with no implant line at all
    ("01", None),              # 'Natrelle Soft Touch SSM' is a model code
])
def test_page1_profile(psiw_cases, case_id, profile):
    assert psiw_cases[case_id].specs.profile == profile


@pytest.mark.parametrize("text,profile", [
    ("Breast Augmentation UHP gel", "extra-high"),
    ("Breast Augmentation HP gel", "high"),
    ("Breast Augmentation MPP gel", "moderate-plus"),
    ("Breast Augmentation MP gel", "moderate"),
    # Lowercase prose must not trip the abbreviations, and a spelled-out
    # profile always wins over one.
    ("she was very happy with the mp result", None),
    ("Moderate Profile Plus, MP chart shorthand", "moderate-plus"),
])
def test_page1_profile_abbreviations(text, profile):
    specs = sg.CaseSpecs()
    p1.holder_parse_details(text, specs)
    assert specs.profile == profile


def test_page1_natrelle_model_code_does_not_decode_to_a_profile():
    """'SSM'/'SRM' encode cc and profile but stay unparseable, per the
    drkolker 'Mini Motiva' precedent."""
    specs = sg.CaseSpecs()
    p1.holder_parse_details("67 year-old 445cc Natrelle SRM", specs)
    assert specs.profile is None
    assert specs.brand == "natrelle"
    assert sg.volume_cc(specs) == 445


# -- purity screen ---------------------------------------------------------


@pytest.mark.parametrize("case_id,reason", [
    ("06", "nipple reduction"),
    ("14", "abdominoplasty"),
    ("96", "liposuction"),
    ("97", "mastopexy"),
    ("104", "congenital deformity"),
])
def test_page1_combined_procedure_cases_emit_no_pairs(psiw_cases, case_id, reason):
    """The gallery is titled 'Augmentation' and still publishes these, so the
    screen reads the case TEXT rather than the gallery or folder name. They
    stay in the enumeration with a warning instead of vanishing from it."""
    case = psiw_cases[case_id]
    assert case.pairs == []
    assert any(reason in w for w in case.warnings)


def test_page1_pure_augmentation_cases_are_not_screened_out(psiw_cases):
    for case_id in ("01", "11", "22", "26", "55", "84", "94"):
        assert psiw_cases[case_id].pairs
        assert psiw_cases[case_id].warnings == []


def test_page1_purity_screen_ignores_the_gallery_slug():
    """A slug-only screen let 86 combined cases through at the Etna clinics."""
    assert p1.holder_combined_procedure("breast-augmentation") is None
    assert p1.holder_combined_procedure(
        "breast augmentation with 255cc implants and a tummy tuck"
    ) == "abdominoplasty"


def test_page1_augmentation_scar_is_not_a_combined_procedure():
    """'breast augmentation scar' (case 32) describes the photo, not a second
    procedure."""
    assert p1.holder_combined_procedure(
        "4 months post-op breast augmentation scar with 440cc implants.") is None


# -- chart metadata --------------------------------------------------------


def test_page1_chart_fields_come_from_labels_only(psiw_cases):
    specs = psiw_cases["26"].specs
    assert specs.age == 50
    assert specs.weight_lbs == 129
    assert specs.incision == "inframammary"
    # No clinic in this family publishes a labelled Placement field, and the
    # narrative's 'subpectoral' is prose, not chart text.
    assert specs.placement is None


def test_page1_narrative_placement_is_not_read_as_chart_metadata():
    specs = sg.CaseSpecs()
    p1.holder_parse_details(
        "35 year old 7 weeks status post subpectoral breast augmentation "
        "Allerghan 255cc moderate profile plus implants.", specs)
    assert specs.placement is None
    assert specs.incision is None
    assert specs.profile == "moderate-plus"


# ---------------------------------------------------------------------------
# Template 'pager' (ciaravino): paginated inline listing enumerated by ul.pager
# ---------------------------------------------------------------------------

P1S_SILICONE = ("https://www.thebodydoc.com/before-after-gallery-houston/breast/"
                "breast-augmentation-silicone-implants/")
P1S_UHP = ("https://www.thebodydoc.com/before-after-gallery-houston/breast/"
           "ultra-high-profile-silicone-implants/")


def _p1s(fixture: str, gallery_url: str, tag: str) -> dict:
    return {c.case_id: c for c in p1.pager_parse_listing_page(
        load(fixture), gallery_url, tag)}


def test_page1solutions_pager_is_the_enumeration_check():
    """The pager enumerates every page, so the gallery states its own extent."""
    html = load("page1solutions_ciaravino_silicone.html")
    assert p1.pager_page_count(html) == 38
    assert p1.pager_page_count("<html>no pager</html>") is None


def test_page1solutions_page_count_reads_the_pager_and_nothing_else():
    """The pager is this family's whole enumeration check.

    A footer nav or a related-content widget carries ?page= links of its own,
    and an inflated count walks pages the gallery does not have - re-collecting
    page 1 under the case ids it already emitted on any CMS that serves it.
    """
    html = load("page1solutions_ciaravino_silicone.html").replace(
        "</body>",
        '<div class="site-footer"><a href="/blog/?page=99">older posts</a></div>'
        '<script>var related = "/news/?page=250";</script></body>')
    assert p1.pager_page_count(html) == 38


def test_page1solutions_keys_a_case_on_its_asset_folder():
    """The printed 'Case #' is not unique and the block's href is the gallery.

    thebodydoc publishes two consecutive saline cases both labelled Case #2547,
    and every div.patient on a silicone page links the same /2890/ URL, so the
    numbered asset folder is the only per-case key.
    """
    cases = _p1s("page1solutions_ciaravino_silicone.html", P1S_SILICONE, "silicone")
    assert set(cases) == {"silicone-378", "silicone-375", "silicone-376"}


def test_page1solutions_reads_every_view_pair_from_the_slides():
    """div.view.s3grid repeats only the first pair; div.slides carries them all."""
    cases = _p1s("page1solutions_ciaravino_silicone.html", P1S_SILICONE, "silicone")
    assert len(cases["silicone-378"].pairs) == 1
    five = cases["silicone-375"].pairs
    assert len(five) == 5
    assert [p.key for p in five] == [f"pair{i}" for i in range(1, 6)]
    # Odd file before, even file after - the convention div.view.s3grid's
    # data-before/data-after documents on the first pair.
    assert five[0].before_url.endswith("/375/01.jpg")
    assert five[0].after_url.endswith("/375/02.jpg")
    assert five[4].before_url.endswith("/375/09.jpg")
    assert five[4].after_url.endswith("/375/10.jpg")
    assert all(p.view_hint is None for p in five)


def test_page1solutions_resolves_assets_against_the_canonical_gallery_url():
    """'./375/01.jpg' must resolve against the listing, not the block's href.

    Every block links .../breast-augmentation-silicone-implants/2890/, which
    serves the same listing; resolving the relative path against that yields a
    URL the site 404s.
    """
    cases = _p1s("page1solutions_ciaravino_silicone.html", P1S_SILICONE, "silicone")
    assert cases["silicone-375"].pairs[0].before_url == P1S_SILICONE + "375/01.jpg"


def test_page1solutions_reads_sided_volumes_and_frame_metrics():
    case = _p1s("page1solutions_ciaravino_silicone.html", P1S_SILICONE,
                "silicone")["silicone-378"]
    assert (case.specs.left_cc, case.specs.right_cc) == (450.0, 415.0)
    assert sg.volume_cc(case.specs) == 432
    assert case.specs.profile == "high"
    assert case.specs.age == 35
    assert case.specs.height_cm == 172.7   # 5'8", curly quotes normalised
    assert case.specs.weight_kg == 74.8


def test_page1solutions_does_not_decode_a_term_the_ruling_omits():
    """Mentor's 'Moderate High Xtra Filled' has no schema profile.

    The volume is still read - its field labels it as an implant size - but the
    projection is left unrecorded rather than guessed at.
    """
    case = _p1s("page1solutions_ciaravino_silicone.html", P1S_SILICONE,
                "silicone")["silicone-376"]
    assert sg.volume_cc(case.specs) == 310
    assert case.specs.profile is None


def test_page1solutions_decodes_the_uhp_chart_abbreviations():
    cases = _p1s("page1solutions_ciaravino_uhp.html", P1S_UHP, "uhp")
    assert cases["uhp-10"].specs.profile == "extra-high"   # spelled out
    assert sg.volume_cc(cases["uhp-10"].specs) == 288       # 275/300 averaged
    assert cases["uhp-09"].specs.profile == "extra-high"   # '430UHP'
    assert sg.volume_cc(cases["uhp-09"].specs) == 430
    # '350HP' decodes to HIGH even inside the ultra-high gallery: the labelled
    # chart field is the clinic's statement, the gallery heading is not.
    assert cases["uhp-08"].specs.profile == "high"
    assert sg.volume_cc(cases["uhp-08"].specs) == 350


def test_page1solutions_records_the_printed_case_number_without_keying_on_it():
    cases = _p1s("page1solutions_ciaravino_uhp.html", P1S_UHP, "uhp")
    assert cases["uhp-09"].specs.fields["Case #"] == "7334"
    assert "Case #" not in cases["uhp-10"].specs.fields   # published as '--'


def test_page1solutions_excludes_a_combined_case_and_says_which_term():
    html = load("page1solutions_ciaravino_silicone.html").replace(
        "Breast Augmentation (Silicone Implants)", "Mommy Makeover", 1)
    cases = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")
    excluded = cases[0]
    assert excluded.pairs == []
    assert any("mommy makeover" in w for w in excluded.warnings)
    assert cases[1].pairs                       # its neighbours are untouched


@pytest.mark.parametrize("chart_line", [
    "<strong>Procedure:</strong> Breast Augmentation with Lift<br/>",
    "<strong>Procedure Performed:</strong> Breast Augmentation with Lift<br/>",
])
def test_page1solutions_screens_the_case_chart_not_just_the_gallery_heading(
        chart_line):
    """The anchor is the gallery's heading, identical on every block.

    A 'Procedure:' chart line is narrative, so it reaches specs.summary and
    never specs.fields; an unknown label lands there too. Screening a heading
    instead of the case's own text is what let 86 combined cases through at
    the Etna clinics.
    """
    html = load("page1solutions_ciaravino_silicone.html").replace(
        "<strong>Height:</strong>", chart_line + "<strong>Height:</strong>", 1)
    cases = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")
    assert cases[0].pairs == []
    assert any("augmentation with lift" in w for w in cases[0].warnings)
    assert cases[1].pairs                       # its neighbours are untouched


def test_page1solutions_reports_a_case_with_no_chart():
    html = re.sub(r'<div class="patient-meta-info">.*?</div>', "",
                  load("page1solutions_ciaravino_silicone.html"),
                  flags=re.S)
    cases = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")
    assert any("no div.patient-meta-info" in w
               for c in cases for w in c.warnings)


def _p1s_swap_slide(html: str, folder: str, index: int) -> str:
    """Republish one case's Nth div.slides pair in after|before DOM order."""
    soup = sg.BeautifulSoup(html, "html.parser")
    for block in soup.select("div.patient"):
        items = block.select("div.slides div.item")
        if len(items) < index:
            continue
        imgs = items[index - 1].select("img")
        if len(imgs) != 2 or p1._pager_asset_folder(imgs[0]["src"]) != folder:
            continue
        imgs[0]["src"], imgs[1]["src"] = imgs[1]["src"], imgs[0]["src"]
    return str(soup)


def test_page1solutions_rejects_a_slide_the_page_marks_the_other_way_round():
    """A reversed pair teaches the model to SHRINK breasts, and passes every gate.

    DOM order alone cannot carry that label. The page marks case 375's first
    pair data-before='./375/01.jpg' / data-after='./375/02.jpg', so a slide
    publishing them the other way round contradicts the clinic's own statement
    and must not be emitted on DOM order.
    """
    html = _p1s_swap_slide(
        load("page1solutions_ciaravino_silicone.html"), "375", 1)
    case = {c.case_id: c for c in p1.pager_parse_listing_page(
        html, P1S_SILICONE, "silicone")}["silicone-375"]
    assert [p.key for p in case.pairs] == ["pair2", "pair3", "pair4", "pair5"]
    assert all(p.before_url.endswith(("03.jpg", "05.jpg", "07.jpg", "09.jpg"))
               for p in case.pairs)
    assert any("data-before" in w and "skipped" in w for w in case.warnings)


def test_page1solutions_rejects_a_reversed_slide_the_grid_never_marks():
    """div.view.s3grid repeats only the FIRST pair, so it cannot mark the rest.

    The asset numbering is the second, independent source: an after asset
    numbered below its before is the pair published back to front.
    """
    html = _p1s_swap_slide(
        load("page1solutions_ciaravino_silicone.html"), "375", 2)
    case = {c.case_id: c for c in p1.pager_parse_listing_page(
        html, P1S_SILICONE, "silicone")}["silicone-375"]
    assert [p.key for p in case.pairs] == ["pair1", "pair3", "pair4", "pair5"]
    assert any("below its before" in w for w in case.warnings)


def _p1s_marked_block(before_asset: str, after_asset: str,
                      *more_slides,
                      marked_before: str = "",
                      marked_after: str = "") -> str:
    """One case whose s3grid marks its FIRST slide pair data-before/data-after.

    marked_before/marked_after override how the grid spells the two images, so
    an install that publishes its markers and its slides in different forms can
    be exercised.
    """
    slides = "".join(
        f'<div class="item"><img class="feat2" src="{b}"/>'
        f'<img class="feat2" src="{a}"/></div>'
        for b, a in ((before_asset, after_asset), *more_slides))
    return (
        '<html><body><div class="patient"><div class="patient-info">'
        '<a>Breast Augmentation (Silicone Implants)</a>'
        '<div class="patient-meta-info">'
        '<strong>Implant Size:</strong> 350 High Profile</div></div>'
        '<div class="view s3grid"><div class="item">'
        f'<img class="feat2" data-before="{marked_before or before_asset}"/>'
        f'<img class="feat2" data-after="{marked_after or after_asset}"/>'
        "</div></div>"
        f'<div class="slides">{slides}</div></div></body></html>')


def test_page1solutions_asset_numbering_never_overrules_the_pages_own_marks():
    """A practice may number its after file first; the page still says which.

    The markers are the clinic's statement of which image is which and the
    numbering is a platform habit. They also state the case's OWN numbering,
    so every slide is read that way - the grid repeats only the first pair, and
    re-deciding per slide would drop every pair past it at such an install.
    """
    html = _p1s_marked_block("./44/02.jpg", "./44/01.jpg",
                             ("./44/04.jpg", "./44/03.jpg"),
                             ("./44/06.jpg", "./44/05.jpg"))
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert [(p.before_url, p.after_url) for p in case.pairs] == [
        (P1S_SILICONE + f"44/0{b}.jpg", P1S_SILICONE + f"44/0{a}.jpg")
        for b, a in ((2, 1), (4, 3), (6, 5))]
    assert not any("skipped" in w for w in case.warnings)


def test_page1solutions_still_rejects_a_slide_against_the_cases_own_numbering():
    """After-first is this case's convention, so ascending is now the reversal."""
    html = _p1s_marked_block("./44/02.jpg", "./44/01.jpg",
                             ("./44/03.jpg", "./44/04.jpg"))
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert [p.key for p in case.pairs] == ["pair1"]
    assert any("above its before" in w and "skipped" in w for w in case.warnings)


def test_page1solutions_matches_markers_and_slides_spelled_differently():
    """The grid publishes data-before/data-after; the slides publish src.

    An install that spells one relative and the other absolute, or appends a
    cache-buster, would make every lookup miss and silently reduce the guard to
    the numbering habit - which cannot see a reversal that the numbering happens
    to agree with.
    """
    html = _p1s_marked_block(
        "./44/02.jpg", "./44/01.jpg",
        marked_before=P1S_SILICONE + "44/01.jpg?v=7",
        marked_after=P1S_SILICONE + "44/02.jpg?v=7")
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert case.pairs == []
    assert any("data-before in the after slot" in w for w in case.warnings)


def test_page1solutions_reads_the_cases_numbering_however_the_grid_spells_it():
    """The grid and the slides need not spell one image the same way.

    _P1SMarks canonicalises every reference for exactly that reason; reading
    the raw attribute for the numbering instead loses the case's own after-first
    convention and drops every pair past the one the grid marks.
    """
    html = _p1s_marked_block(
        "./44/02.jpg", "./44/01.jpg",
        ("./44/04.jpg", "./44/03.jpg"), ("./44/06.jpg", "./44/05.jpg"),
        marked_before=P1S_SILICONE + "44/02.jpg",
        marked_after=P1S_SILICONE + "44/01.jpg")
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert [p.key for p in case.pairs] == ["pair1", "pair2", "pair3"]
    assert not any("skipped" in w for w in case.warnings)


def test_page1solutions_reports_markers_that_match_no_slide_image():
    """A guard that silently checked nothing is invisible from both ends."""
    html = _p1s_marked_block("./44/01.jpg", "./44/02.jpg",
                             marked_before="./99/01.jpg",
                             marked_after="./99/02.jpg")
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert [p.key for p in case.pairs] == ["pair1"]
    assert any("the pairing guard checked nothing" in w for w in case.warnings)


def test_page1solutions_rejects_a_slide_pairing_two_asset_folders():
    """Numbers restart per folder, so a cross-folder couple is not comparable.

    It is also two cases' images in one pair, which is the shape of a
    fabricated before/after and never something to keep on DOM order alone.
    """
    html = _p1s_marked_block("./44/01.jpg", "./44/02.jpg").replace(
        '<div class="slides"><div class="item">'
        '<img class="feat2" src="./44/01.jpg"/>',
        '<div class="slides"><div class="item">'
        '<img class="feat2" src="./45/03.jpg"/>')
    case = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert case.pairs == []
    assert any("all live in one folder" in w for w in case.warnings)


def test_page1solutions_reports_a_block_it_could_not_key(capsys):
    """A block with no numbered asset path cannot be keyed - but it is a case.

    Dropping it silently makes it invisible from both ends: the pager counts
    pages, not cases, so nothing downstream can sum what went missing.
    """
    html = load("page1solutions_ciaravino_silicone.html").replace(
        'src="./376/', 'src="https://cdn.example.com/376/')
    cases = p1.pager_parse_listing_page(html, P1S_SILICONE, "silicone")
    assert [c.case_id for c in cases] == ["silicone-378", "silicone-375"]
    assert p1.pager_listing_case_count(html) == 3
    out = capsys.readouterr().out
    assert "no numbered asset path" in out
    # The block is named by whatever it does publish, so the drop is evidenced.
    assert "https://cdn.example.com/376/01.jpg" in out


P1S_TEST_CFG = sg.ClinicConfig(
    slug="p1sfamily", consent_ref="p1sfamily-agreement",
    base_url="https://p1s.example.com",
    gallery_paths=["/gallery/breast-augmentation-silicone-implants/"],
    kind="page1solutions", template="pager")


def _p1s_listing_page(folder: str, *, cdn: bool = False,
                      blocks: int = 1, pages: int = 3) -> str:
    """One Page 1 Solutions listing page, `blocks` cases and a pager."""
    body = ""
    for n in range(blocks):
        root = (f"https://cdn.example.com/{folder}{n}/" if cdn
                else f"./{folder}{n}/")
        body += (
            '<div class="patient"><div class="patient-info">'
            '<a>Breast Augmentation (Silicone Implants)</a>'
            '<div class="patient-meta-info">'
            '<strong>Implant Size:</strong> 350 High Profile</div></div>'
            f'<div class="slides"><div class="item">'
            f'<img src="{root}01.jpg"/><img src="{root}02.jpg"/>'
            "</div></div></div>")
    pager = "".join(f'<li><a href="?page={n}">{n}</a></li>'
                    for n in range(2, pages + 1))
    return f'<html><body>{body}<ul class="pager">{pager}</ul></body></html>'


class _P1SPagerSession:
    """Serves three listing pages; page 2's assets are absolute CDN URLs."""

    headers: dict = {}

    def __init__(self):
        self.gets: list[str] = []

    def get(self, url, timeout=None):
        self.gets.append(url)
        if "?page=2" in url:
            body = _p1s_listing_page("20", cdn=True)
        elif "?page=3" in url:
            body = _p1s_listing_page("30")
        else:
            body = _p1s_listing_page("10")

        class R:
            status_code = 200
            content = body.encode()

            def raise_for_status(self):
                return None

        return R()


def test_page1solutions_says_when_a_listing_published_no_pager_at_all(
        tmp_path, monkeypatch, capsys):
    """One page and no pager found are different claims about the same gallery.

    The pager is this family's only enumeration signal, so a sweep that stops
    after page 1 because nothing said otherwise must not read as the gallery
    stating it has one page.
    """
    class _NoPagerSession:
        headers: dict = {}

        def get(self, url, timeout=None):
            body = _p1s_listing_page("10", pages=1).replace(
                '<ul class="pager"></ul>', "").encode()

            class R:
                status_code = 200
                content = body

                def raise_for_status(self):
                    return None

            return R()

    cases = sg.collect_cases(
        P1S_TEST_CFG, _fetcher(tmp_path, monkeypatch, _NoPagerSession()))
    assert [c.case_id for c in cases] == ["silicone-100"]
    out = capsys.readouterr().out
    assert "publishes no ul.pager markup" in out
    assert "collected all 1 case block(s)" in out


def test_page1solutions_walks_past_a_page_whose_blocks_were_all_dropped(
        tmp_path, monkeypatch, capsys):
    """A page that renders cases is not the end of the set, whatever we keep.

    Every block of page 2 publishes an absolute CDN path, so all of them are
    dropped - reading that as 'no more cases' abandons page 3 and every page
    after it, and the shortfall never reaches the block reconciliation.
    """
    session = _P1SPagerSession()
    cases = sg.collect_cases(
        P1S_TEST_CFG, _fetcher(tmp_path, monkeypatch, session))
    assert any("?page=3" in url for url in session.gets)
    assert [c.case_id for c in cases] == ["silicone-100", "silicone-300"]
    out = capsys.readouterr().out
    assert "walked all 3 listing page(s)" in out
    assert "collected 2 case(s) from the 3 case block(s)" in out


def test_page1_dispatch_routes_ciaravino_through_the_paginated_inline_parser(
        tmp_path, monkeypatch):
    """ciaravino is parsed by the 'pager' template, not bandy's 'item'.

    ciaravino was once collected as bandy's kind, and because `collect_cases`
    returns from the FIRST matching branch that sent its paginated inline
    listing to a parser that looks for case pages, finds no `div.patient-item`
    anchors and returns zero cases - a silent-zero run that reads exactly like
    a finished collection (the ncps/psiw precedent above).
    """
    class _OnePageSession:
        headers: dict = {}

        def get(self, url, timeout=None):
            body = _p1s_listing_page("77", pages=1).encode()

            class R:
                status_code = 200
                content = body

                def raise_for_status(self):
                    return None

            return R()

    cfg = dataclasses.replace(
        sg.CLINICS["ciaravino"],
        gallery_paths=sg.CLINICS["ciaravino"].gallery_paths[:1])
    cases = sg.collect_cases(cfg, _fetcher(tmp_path, monkeypatch,
                                           _OnePageSession()))

    # The asset folder keys the case, namespaced by its gallery - which only
    # the 'pager' template produces.
    assert [c.case_id for c in cases] == ["silicone-770"]
    assert cases[0].pairs[0].before_url.endswith("/770/01.jpg")
