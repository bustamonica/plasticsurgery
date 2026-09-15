"""Tests for the legacy BRAG book (`rev*` markup) parser - sarasota.

Fixtures are real markup: the case list `<ul>` of the gallery's first and last
listing pages, and case 13268's headline-to-charts container, with scripts,
styles, the PhotoSwipe dialog and the MyFavorites header stripped. No images
are stored.
"""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import bragbook_rev as br  # noqa: E402
import framing  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"
GALLERY = "https://sarasotaplasticsurgery.com/photo-gallery/breast-augmentation/"


def load(name):
    return (FIXTURES / name).read_text()


@pytest.fixture(scope="module")
def case():
    return br.rev_parse_case(load("sarasota_case_13268.html"), "13268", GALLERY + "13268/")


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def test_first_listing_page_links_ten_cases_and_the_next_page():
    html = load("sarasota_listing_first.html")
    assert br.rev_case_ids(html, GALLERY) == [
        "19003", "13841", "13268", "14434", "15214",
        "5695", "20049", "3277", "10748", "11387"]
    assert br.rev_next_page(html, GALLERY) == (
        "https://sarasotaplasticsurgery.com/photo-gallery/"
        "?revCatname=breast-augmentation&getCategorySets=1&categorySetsStart=1")


def test_last_listing_page_carries_no_next_link():
    html = load("sarasota_listing_last.html")
    assert br.rev_case_ids(html, GALLERY) == ["22074", "22462", "23682"]
    assert br.rev_next_page(html, GALLERY) is None


def _page(ids, next_start=None):
    items = "".join(
        f'<li class="revCatImageSet"><a href="{GALLERY}{i}/">x</a>'
        f'<a href="{GALLERY}{i}/" class="revCaseViewLink">View More</a></li>' for i in ids)
    more = (f'<li class="revCatImageSet" style="display:none"><a class="revJscroll-next" '
            f'href="https://sarasotaplasticsurgery.com/photo-gallery/?revCatname=breast-augmentation'
            f'&#038;getCategorySets=1&#038;categorySetsStart={next_start}">More</a></li>'
            if next_start is not None else "")
    return f"<ul>{items}{more}</ul>"


def test_collect_cases_walks_the_infinite_scroll_to_its_end(tmp_path):
    """The gallery publishes no total, so the walk is what enumerates it:
    every page's own case links, until a page carries no next link. It also
    pins this clinic to its own `kind` - the current-plugin `sanantonio` parser
    finds no case on this markup."""
    (tmp_path / "sarasota_breast-augmentation_listing.html").write_text(
        _page(["11", "12"], next_start=1))
    (tmp_path / "sarasota_breast-augmentation_listing_p1.html").write_text(
        _page(["13"]))
    for i in ("11", "12", "13"):
        (tmp_path / f"sarasota_case_{i}.html").write_text(load("sarasota_case_13268.html"))
    cases = sg.collect_cases(sg.CLINICS["sarasota"],
                             sg.PoliteFetcher(tmp_path, delay=0, offline=True))
    assert [c.case_id for c in cases] == ["11", "12", "13"]
    # Every case republishes 13268's photographs here, but none can be fetched
    # offline, so the duplicate screen keeps them rather than dropping on no
    # evidence.
    assert all(len(c.pairs) == 3 for c in cases)


def _two_page_gallery(tmp_path):
    (tmp_path / "sarasota_breast-augmentation_listing.html").write_text(
        _page(["11", "12"], next_start=1))
    (tmp_path / "sarasota_breast-augmentation_listing_p1.html").write_text(
        _page(["13"]))
    for i in ("11", "12", "13"):
        (tmp_path / f"sarasota_case_{i}.html").write_text(load("sarasota_case_13268.html"))
    return sg.PoliteFetcher(tmp_path, delay=0, offline=True)


def test_a_walk_stopped_by_the_page_ceiling_is_reported_as_a_floor(tmp_path, monkeypatch, capsys):
    """The walk is the only enumeration check, so a truncated one must not
    read as complete."""
    fetcher = _two_page_gallery(tmp_path)
    monkeypatch.setattr(br, "REV_MAX_PAGES", 1)
    cases = sg.collect_cases(sg.CLINICS["sarasota"], fetcher)
    assert [c.case_id for c in cases] == ["11", "12"]
    assert "WARN sarasota: listing walk hit the 1-page ceiling" in capsys.readouterr().out


def test_a_walk_that_reaches_the_last_page_is_not_warned(tmp_path, capsys):
    sg.collect_cases(sg.CLINICS["sarasota"], _two_page_gallery(tmp_path))
    assert "WARN" not in capsys.readouterr().out


def test_the_current_plugin_parser_finds_nothing_on_this_markup():
    parsed = sg.sanantonio_parse_case(load("sarasota_case_13268.html"), "13268", "x")
    assert parsed.pairs == []


# ---------------------------------------------------------------------------
# Case page
# ---------------------------------------------------------------------------


def test_pairs_are_midpoint_composites_keyed_by_their_asset_token(case):
    assert [p.key for p in case.pairs] == ["YIUnx046KCFJ", "6U0Iblu4XOo8", "nzkV5CE2z6n3"]
    for pair in case.pairs:
        assert pair.split_composite and pair.before_url == pair.after_url
        assert pair.before_url.startswith("https://www.bragbook.gallery/assets/gallery/")
        assert pair.before_url.endswith(f"-{pair.key}_highres.webp")


def test_volume_is_the_narrative_figure_not_the_chart_bucket(case):
    """'Volume: Between 300cc and 350cc' is a bucket; the narrative says 325cc."""
    assert (case.specs.left_cc, case.specs.right_cc) == (325.0, 325.0)
    assert case.specs.fields["Volume"] == "Between 300cc and 350cc"


def test_chart_buckets_are_kept_verbatim_and_never_converted(case):
    specs = case.specs
    assert specs.age == 22  # the narrative's '22-year-old', not 'Under 25'
    assert specs.fields["Age"] == "Under 25 years old"
    assert specs.fields["Weight"] == "Under 100 pounds"
    assert specs.weight_lbs is None and specs.height_cm is None and specs.weight_kg is None


def test_chart_fields(case):
    specs = case.specs
    assert specs.gender == "female"
    assert specs.months_post_op == 12.0
    assert specs.placement == "submuscular"
    assert specs.incision == "inframammary"
    assert specs.brand == "natrelle"
    # 'Style 20' is a model code, not a profile word.
    assert specs.profile is None


def test_display_position_is_notes_only(case):
    """'Patient 3' renumbers when a case is added at the top; the key is the URL id."""
    assert case.case_id == "13268"
    assert case.specs.fields["Gallery Patient"] == "3"


def _case_html(narrative, volume):
    return (
        '<div><h1 id="revPatientHeadline">Breast Augmentation: Patient 9 </h1>'
        '<div class="revBArow revBA-gallery"><figure class="revBAcol">'
        '<a class="psLink" href="https://www.bragbook.gallery/assets/gallery/1/'
        'breast-augmentation-before-and-after-AbC123_highres.webp">i</a></figure></div>'
        f'<div id="revPatientDetails"><p>{narrative}</p></div>'
        '<ul id="revPatientDetailsList2"><li><strong>Volume: </strong>'
        f'{volume}</li></ul></div>')


def test_an_exact_chart_volume_is_used_when_the_narrative_has_none():
    parsed = br.rev_parse_case(_case_html("Bilateral augmentation.", "375cc"), "9", "x")
    assert sg.volume_cc(parsed.specs) == 375


@pytest.mark.parametrize("bucket", [
    "Between 300cc and 350cc", "Under 250cc", "Over 500cc", "300cc - 350cc"])
def test_a_bucket_chart_volume_is_never_a_volume(bucket):
    parsed = br.rev_parse_case(_case_html("Bilateral augmentation.", bucket), "9", "x")
    assert sg.volume_cc(parsed.specs) is None


def test_a_chart_volume_that_disagrees_with_the_narrative_is_warned():
    parsed = br.rev_parse_case(_case_html("Natrelle 325cc implants.", "375cc"), "9", "x")
    assert sg.volume_cc(parsed.specs) == 325
    assert any("disagrees" in w for w in parsed.warnings)


@pytest.mark.parametrize("narrative,reason", [
    ("She underwent a breast augmentation with mastopexy.", "mastopexy"),
    ("Breast augmentation and a tummy tuck.", "abdominoplasty"),
    ("Augmentation with fat transfer to the upper pole.", "fat transfer"),
])
def test_an_impure_case_is_returned_without_pairs(narrative, reason):
    parsed = br.rev_parse_case(_case_html(narrative, "325cc"), "9", "x")
    assert parsed.pairs == []
    assert any(reason in w for w in parsed.warnings)


def test_a_declined_second_procedure_does_not_make_a_case_impure():
    parsed = br.rev_parse_case(
        _case_html("She did not want a lift and chose 325cc implants.", "325cc"), "9", "x")
    assert len(parsed.pairs) == 1


# ---------------------------------------------------------------------------
# The narrative volume reader - every phrasing below is published on this
# gallery, and the shared reader gets the first five wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    # A saline fill is the final volume, not a second breast (2594).
    ("Natrelle 68HP 350cc Saline breast implants filled to 385cc and were "
     "placed under the muscle.", (385.0, 385.0)),
    # Fill on one side, shell size on the other (2844).
    ("Style 68HP Saline implants, 425cc filled to 475cc on the left and 425cc "
     "on the right.", (475.0, 425.0)),
    # The fill's unit omitted where the shell's states it (12302).
    ("Style 68 saline implants – 360cc filled to 375 on the left and 360cc "
     "filled to 400 on the right.", (375.0, 400.0)),
    # A side word describing the PATIENT is not a sided volume (2595).
    ("27-year-old with a noticeable smaller right breast who desired "
     "enlargement of both breasts and underwent a bilateral submuscular breast "
     "augmentation using Natrelle Style 20 Silicone filled 350cc breast "
     "implants.", (350.0, 350.0)),
    # mL beside an implant (8219, 11201).
    ("She chose Natrelle Style 68 high-profile 400 mL implants.", (400.0, 400.0)),
    ("Natrelle Inspira Style SCX 525 ml Silicone Gel Breast implants.", (525.0, 525.0)),
    ("silicone implants 605cc’s.", (605.0, 605.0)),
    ("filled to 335cc on the right and 360cc on the left.", (360.0, 335.0)),
    ("The right implant was inflated to 515 cc and the left implant was "
     "inflated to 465 cc to correct some minor asymmetry.", (465.0, 515.0)),
    ("a 400cc Style 20 Natrelle Silicone implant for the left side and a 450cc "
     "Style 20 Natrelle Silicone implant for the right side.", (400.0, 450.0)),
    ("Natrelle Inspira Style SRF silicone implants – 365cc on the left and "
     "415cc on the right that were placed under the muscle", (365.0, 415.0)),
    # A style number is never a volume.
    ("Natrelle Inspira style 20-425cc smooth, round silicone implants.", (425.0, 425.0)),
    ("Natrelle Style FM 410 – 310cc Highly Cohesive Anatomic implants", (310.0, 310.0)),
])
def test_rev_volumes_reads_the_galleries_own_phrasings(text, expected):
    left, right, warning = br.rev_volumes(text)
    assert (left, right) == expected and warning is None


def test_one_side_named_and_the_other_not_records_no_volume():
    """12282: '465cc filled to 510cc on the right and 465cc filled to 470cc.'
    The left is implied, not stated; the shared reader recorded 510 for the
    case, which is the right breast alone."""
    left, right, warning = br.rev_volumes(
        "saline implant 465cc filled to 510cc on the right and 465cc filled to 470cc.")
    assert (left, right) == (None, None)
    assert "right breast only" in warning


@pytest.mark.parametrize("text,expected", [
    ("Natrelle style 15 silicone implants 421cc on the left and 533 on the right.",
     (421.0, 533.0)),
    ("Style FX 410cc on the left and 360 on the right placed through an "
     "inframammary incision", (410.0, 360.0)),
])
def test_the_second_sided_figure_may_drop_the_unit(text, expected):
    """13060/13153: the unit the first figure states covers its partner."""
    left, right, warning = br.rev_volumes(text)
    assert (left, right) == expected and warning is None


def test_a_bare_figure_needs_a_stated_unit_somewhere():
    assert br.rev_volumes("Natrelle 385 on the left and 360 on the right.")[:2] == (None, None)


def test_the_same_side_named_twice_records_no_volume():
    """14197: '500cc on the left and 600cc on the left' - a clinic typo."""
    left, right, warning = br.rev_volumes(
        "Natrelle Style 20 silicone implants, 500cc on the left and 600cc on the left.")
    assert (left, right) == (None, None)
    assert "named twice" in warning


def test_separate_before_after_files_are_reported_not_emitted():
    """20920 publishes separate 640x480 befores and 1800x1200 afters."""
    html = (
        '<div><h1 id="revPatientHeadline">Breast Augmentation: Patient 191</h1>'
        '<div class="revBArow revBA-gallery">'
        '<figure class="revBAcol1"><a class="psLink" data-size="640x480" '
        'href="https://www.bragbook.gallery/assets/gallery/159/bIYTOcTFS54F_highres.jpg">b</a></figure>'
        '<figure class="revBAcol2"><a class="psLink" data-size="1800x1200" '
        'href="https://www.bragbook.gallery/assets/gallery/159/hpKkg2W3rzCV_highres.jpg">a</a></figure>'
        '</div><div id="revPatientDetails"><p>Natrelle style 20 350cc’s</p></div></div>')
    parsed = br.rev_parse_case(html, "20920", "x")
    assert parsed.pairs == []
    assert any("separate before/after file couples" in w and "640x480" in w
               for w in parsed.warnings)


def test_an_unrelated_trailing_number_is_not_a_volume():
    """Many narratives end in a bare five-digit reference ('... implants. 66766')."""
    assert br.rev_volumes("Natrelle Style 15 421cc silicone implants. 66547")[:2] == (421.0, 421.0)


# ---------------------------------------------------------------------------
# Profile and purity
# ---------------------------------------------------------------------------


def _chart_case(narrative, profile):
    return _case_html(narrative, "Between 350cc and 400cc").replace(
        "</ul></div>",
        f"<li><strong>Implant Profile: </strong>{profile}</li></ul></div>")


@pytest.mark.parametrize("chart,expected", [
    ("High", "high"), ("Moderate Plus", "moderate-plus"), ("Moderate", "moderate"),
    ("Full", "high"), ("Extra Full", "extra-high"), ("Round", None)])
def test_the_charts_labelled_profile_is_read(chart, expected):
    parsed = br.rev_parse_case(_chart_case("Natrelle 375cc implants.", chart), "9", "x")
    assert parsed.specs.profile == expected


def test_a_hyphenated_narrative_profile_is_read():
    parsed = br.rev_parse_case(
        _case_html("Natrelle Style 68 high-profile 400 mL implants.", "x"), "9", "x")
    assert parsed.specs.profile == "high"


def test_chart_and_narrative_profiles_that_disagree_record_neither():
    parsed = br.rev_parse_case(
        _chart_case("Natrelle 375cc moderate profile implants.", "High"), "9", "x")
    assert parsed.specs.profile is None
    assert any("disagrees" in w for w in parsed.warnings)


@pytest.mark.parametrize("narrative", [
    # 4724: recommended, then declined.
    "It was explained to the patient that since she has a mild degree of ptosis "
    "(sagging) that a periareolar mastopexy (breast lift) is recommended. The "
    "patient was not interested in a breast lift and understands that she can "
    "undergo a breast lift in the future. She chose 400cc implants.",
    # 19002: 'may sometimes need'.
    "32-year-old female with Pseudoptosis which may sometimes need a breast lift. "
    "She decided on Natrelle Style 15, 421cc silicone implants.",
    # 13665: 'would benefit from', then refused.
    "For best rejuvenation, she would benefit from a mastopexy/augmentation, but "
    "she was against the scars and accepts a more natural result with glandular "
    "ptosis.",
    # 12287: the verb, not the procedure.
    "She chose Natrelle Style FM 410 – 310cc Highly Cohesive Anatomic implants to "
    "lift her pseudoptotic breasts and revitalize their appearance.",
])
def test_a_lift_that_was_not_performed_does_not_exclude_the_case(narrative):
    assert br.rev_impure_reason(narrative) is None


@pytest.mark.parametrize("narrative,reason", [
    ("She underwent a bilateral breast augmentation and a breast lift.", "breast lift"),
    ("Breast augmentation with lift.", "breast lift"),
    # 15214/13646: a nipple reduction changes the after photograph too.
    ("She chose Natrelle SSF 385cc and bilateral nipple reduction.", "reduction"),
])
def test_a_performed_second_procedure_still_excludes_the_case(narrative, reason):
    assert br.rev_impure_reason(narrative) == reason


def test_sarasota_crops_its_corner_block_as_a_fraction_of_height(tmp_path):
    """Every composite, whether or not it carries the block - one publishes it
    on the after half only, and a crop applied by detection would frame that
    pair differently from its neighbours."""
    (tmp_path / "sarasota_breast-augmentation_listing.html").write_text(_page(["11"]))
    (tmp_path / "sarasota_case_11.html").write_text(load("sarasota_case_13268.html"))
    cfg = sg.CLINICS["sarasota"]
    (case,) = sg.collect_cases(cfg, sg.PoliteFetcher(tmp_path, delay=0, offline=True))
    buf = io.BytesIO()
    Image.new("RGB", (1800, 600), (200, 150, 120)).save(buf, format="JPEG")
    for pair in case.pairs:
        images = framing.pair_images(cfg, pair, lambda url: buf.getvalue())
        for half in (images.before, images.after):
            # 600 - ceil(600 * 0.25): the block's worst edge is 0.226 of height.
            assert Image.open(io.BytesIO(half)).size == (900, 450)


def test_sarasota_is_registered_with_its_own_kind():
    cfg = sg.CLINICS["sarasota"]
    assert cfg.kind == "bragbook_rev"
    assert cfg.consent_ref == "sarasota-agreement-2026-08-25"
    assert cfg.gallery_paths == ["/photo-gallery/breast-augmentation/"]
    assert sum(c.kind == "bragbook_rev" for c in sg.CLINICS.values()) == 1
