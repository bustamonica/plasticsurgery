"""Tests for the RM Gallery 2 (Rosemont Media) family parser (rm_gallery2.py).

Fixtures under `fixtures/gallery/rmg2_*` are the real single-case region of a
real case page from each of the clinics that exercises a distinct layout. No
patient images are stored - only the markup.

Every fixture here exists because some clinic in the family breaks an
assumption a single-clinic parser would have made:

- `weston` is the only theme wrapping the case in `article.content`, and the
  only one with `img-set` wrappers.
- `pscarolina` is the only one with no `single-case-content`, so its region has
  to come from `case-wrap`.
- `leber` carries the trailing `ul.archive-grandchildren` category list INSIDE
  the case container; not stripping it rejects all 94 of its cases as lifts.
- `coberly`/`jkps` print the spec block ABOVE the photographs, everyone else
  below, and `sbbreast` puts it in a SIBLING of `case-wrap`.
- `boynton`/`sbps` lazy-load, so the photograph URL is on `data-src` and `src`
  holds a base64 placeholder.
- `najera` renders each chart label on its own line and publishes RANGES.
- `bottger`/`sbbreast` are impure cases filed in the augmentation category.
"""

import io
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rm_gallery2 as rm  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"


def load(clinic: str) -> str:
    return (FIXTURES / f"rmg2_{clinic}_patient-1.html").read_text()


def parse(clinic: str) -> sg.CaseData:
    return rm.rm_parse_case(load(clinic), "patient-1", f"https://x/{clinic}/patient-1")


ALL_CLINICS = ["weston", "pscarolina", "leber", "jkps", "boynton", "coberly",
               "sbps", "sbbreast", "najera", "bottger", "savetsky"]


# ---------------------------------------------------------------------------
# Region detection: one selector is not one markup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clinic", ALL_CLINICS)
def test_every_theme_yields_a_case_region(clinic):
    """All three theme layouts must resolve, or the clinic reads as empty."""
    assert rm.rm_case_region(load(clinic)) != ""


@pytest.mark.parametrize("clinic,pairs", [
    ("weston", 2), ("pscarolina", 4), ("leber", 3), ("jkps", 6),
    ("boynton", 3), ("coberly", 3), ("sbps", 2), ("najera", 5),
])
def test_pair_counts_match_the_published_page(clinic, pairs):
    assert len(rm.rm_pair_urls(load(clinic))) == pairs


# ---------------------------------------------------------------------------
# The category list: leber's 94-of-94 false lift rejection
# ---------------------------------------------------------------------------


def test_trailing_category_list_is_not_read_as_the_case_text():
    """`ul.archive-grandchildren` names Breast Lift/Reduction on every page.

    Reading it as the case's own text rejected all 94 doctorleber cases as
    lifts - the single highest-cost parsing error the prospecting run found.
    """
    text = rm.rm_case_text(load("leber"))
    assert "Breast Reduction" not in text
    assert "Breast Lift" not in text
    assert rm.rm_impure_reason(text) is None


def test_leber_is_pure_despite_the_category_list_naming_a_lift():
    assert parse("leber").warnings == []


# ---------------------------------------------------------------------------
# Images: lazy-loading and the original variant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clinic", ["boynton", "sbps"])
def test_lazy_loaded_photographs_are_read_from_data_src(clinic):
    """`src` holds a base64 GIF on these themes; a naive read gets no photo."""
    for before, after in rm.rm_pair_urls(load(clinic)):
        for url in (before, after):
            assert not url.startswith("data:")
            assert "rmgallery2" in url


def test_medium_variant_is_substituted_for_the_original():
    """doctorleber serves medium.jpeg (600x897); original.jpeg is 2592x3872."""
    assert rm.rm_full_res(
        "https://x/wp-content/uploads/rmgallery2/RMG1-1230-b/medium.jpeg"
    ).endswith("/original.jpeg")
    for before, after in rm.rm_pair_urls(load("leber")):
        assert before.endswith("/original.jpeg")
        assert after.endswith("/original.jpeg")


def test_full_res_keeps_the_assets_own_extension():
    """One clinic publishes .png assets beside .jpeg; the extension is not
    normalised, and an already-original URL is left alone."""
    assert rm.rm_full_res("https://x/RMG1-1-b/medium.png").endswith("/original.png")
    assert rm.rm_full_res("https://x/RMG1-1-b/original.jpg").endswith("/original.jpg")


def test_before_and_after_slots_pair_in_document_order():
    """The plugin's own classes are the pairing signal on every theme."""
    pairs = rm.rm_pair_urls(load("weston"))
    assert [p[0].rsplit("/", 2)[1] for p in pairs] == [
        "RMG2671321081-2149-b", "RMG2685025008-2149-b"]
    assert [p[1].rsplit("/", 2)[1] for p in pairs] == [
        "RMG2671321090-2149-a", "RMG2685025016-2149-a"]


# ---------------------------------------------------------------------------
# Case identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clinic,case_id", [
    ("weston", "2149"), ("pscarolina", "14535"), ("leber", "1230"),
    ("jkps", "5090"), ("coberly", "1707"), ("savetsky", "366"),
])
def test_case_key_is_the_rm_case_number_not_the_display_slug(clinic, case_id):
    """`patient-<n>` is a curated display position (irasavetskymd runs
    366, 328, 2009, 1193 ... in slug order); keying on it would re-point every
    pair id in the clinic the moment the practice reorders its gallery."""
    assert parse(clinic).case_id == case_id


def test_display_slug_is_kept_in_the_case_fields():
    assert parse("weston").specs.fields["gallery slug"] == "patient-1"


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


def test_volume_then_side_layout_keeps_both_sides():
    """'295cc Left and 275cc Right' - reading side-then-volume gives 275/275."""
    specs = parse("jkps").specs
    assert (specs.left_cc, specs.right_cc) == (295.0, 275.0)


def test_side_then_volume_layout_keeps_both_sides():
    assert rm.rm_parse_volumes("Left: 400cc Right: 425cc") == (400.0, 425.0)
    assert rm.rm_parse_volumes("R) 457cc L) 492cc") == (492.0, 457.0)


def test_millilitres_read_as_cc():
    """drcoberly publishes exclusively in mL (captain's 2026-08-15 ruling)."""
    assert parse("coberly").specs.left_cc == 425.0


def test_a_published_range_is_not_a_volume():
    """drbottger's '400-425 cc' is not a measurement (sixsurgery precedent)."""
    assert rm.rm_parse_volumes("400-425 cc smooth round high profile gel") == (None, None)
    assert rm.rm_parse_volumes("Between 450cc to 500cc") == (None, None)


def test_bilateral_volume_applies_to_both_breasts():
    assert parse("weston").specs.left_cc == 421.0
    assert parse("weston").specs.right_cc == 421.0


def test_bare_number_only_counts_under_a_labelled_implant_field():
    """The mwps rule: a bare number in prose is not a volume."""
    assert rm.rm_parse_volumes("Implant Size: 421") == (421.0, 421.0)
    assert rm.rm_parse_volumes("Smooth round 385 implants") == (None, None)


@pytest.mark.parametrize("text,volumes", [
    # leber 1390: a bare R/L leading into each figure, after a bilateral
    # headline that the sided figures override.
    ("500 cc Moderate Plus Silicone\n"
     "5ft 6 - 130 lbs, R14, L 13.5, R 450 cc MPP Silicone, L 500cc MPP Silicone",
     (500.0, 450.0)),
    # pscarolina 10985 and 11014: bare figures sided inside the labelled field.
    ("Implant Size: 350 Moderate Plus Profile on Right//325 Moderate Plus Profile on Left",
     (325.0, 350.0)),
    ("Implant Size: 400 Moderate Plus Profile on the right, "
     "375 Moderate Plus Profile on the left", (375.0, 400.0)),
    # leber 2280: each side labelled, its figure far along its own field.
    ("Right Breast Implant: Smooth Round Moderate Plus Profile 450 cc Silicone\n"
     "Left Breast Implant: Smooth Round Moderate Plus Profile 450 cc Silicone",
     (450.0, 450.0)),
    ("Right implant: 295 cc\nLeft implant: 240 cc", (240.0, 295.0)),
])
def test_sided_layouts_keep_each_breast_on_its_own_side(text, volumes):
    assert rm.rm_parse_volumes(text) == volumes


def test_a_breast_width_is_not_a_side_marker():
    """leber 1288: 'L 12.5cm R 13.5cm' measures the breasts; it sides nothing."""
    assert rm.rm_parse_volumes("BL 350cc MP Silicone, L 12.5cm R 13.5cm, 32 A") == (350.0, 350.0)


@pytest.mark.parametrize("text", [
    # Unsided slash pairs: boynton 7575/7594/7580 and bottger 633.
    "IMPLANT SIZE: 325/375cc",
    "IMPLANT SIZE: 300/325 cc",
    "IMPLANT SIZE: 300/340 c",
    "Bilateral Breast Augmentation with 275/300 CC smooth round moderate plus gel implants",
    # Saline fills: leber 2266, 2267 and 1243, and coberly's single-figure form.
    "Right Breast: Smooth Round Moderate Profile Saline 425cc Overfilled to 450cc\n"
    "Left Breast: Smooth Round Moderate Profile Saline 425cc Overfilled to 450cc",
    "Right Breast Implant: Smooth Round Moderate Profile 425 cc Saline over filled to 475 cc\n"
    "Left Breast Implant: Smooth Round Moderate Profile 425 cc Saline over filled to 450 cc",
    "Right Breast Width: 12.5 cm - Left Breast Width: 13 cm - "
    "325 cc Moderate profile overfilled to 350 cc Original Bra Size: 32 a",
    "after breast augmentation with smooth round saline implants filled to 400 mL",
])
def test_two_figures_that_do_not_say_which_is_the_implant_record_no_volume(text):
    """Neither one of the figures nor their average is a published volume."""
    assert rm.rm_parse_volumes(text) == (None, None)


def test_a_bare_implant_figure_before_moderate_is_not_months_post_op():
    """'450 Moderate' once read as 450 months post-op on 20 emitted pairs."""
    assert rm.rm_parse_specs("Implant Size: 450 Moderate Plus Profile").months_post_op is None
    assert rm.rm_parse_specs("After photos taken at 6 mos. post-op").months_post_op == 6.0


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,profile", [
    ("Smooth Round Moderate 445cc Silicone Implants", "moderate"),
    ("Smooth Round Moderate Plus 415cc Silicone Implants", "moderate-plus"),
    ("Implant Size: 325cc MP", "moderate"),
    ("Implant Size: 430 Ultra High Profile", "extra-high"),
    ("400 cc smooth round high profile gel implant", "high"),
    ("Profile: Moderate", "moderate"),
    ("Implants: Silicone Profile: Moderate Plus", "moderate-plus"),
])
def test_profile_vocabulary_this_family_actually_publishes(text, profile):
    assert rm.rm_profile(text) == profile


@pytest.mark.parametrize("text", [
    # drtabbal's own prose - a projection word that is not a projection.
    "enhanced projection and a moderate increase in breast size",
    "she was highly satisfied with her 350cc silicone implants",
    # Below the schema's enum floor: 'low' is not a profile value, so a
    # Low Plus implant documents no profile rather than a moderate one.
    "Smooth Round Low Plus 280 cc (Right) 265 cc (Left)",
    # A Natrelle style code is NOT decoded (captain's confirmation).
    "650cc SRF",
    # Nor is an Inspira fill name (coberly 3576).
    "Allergan Inspira 340 cc extra full gel Inspira implants",
    # Mentor's moderate-high has no schema value (boynton 8644, 7289).
    "310 cc smooth round moderate-high profile silicone gel Mentor XTRA breast implant",
    "Mentor MH (moderate High) smooth round silicone gel XTRA breast implants",
])
def test_profile_is_omitted_rather_than_guessed(text):
    assert rm.rm_profile(text) is None


# ---------------------------------------------------------------------------
# Ranges are not measurements
# ---------------------------------------------------------------------------


def test_najera_buckets_are_not_read_as_measurements():
    """najera publishes 'Age: 30 - 39', 'Weight: 151-160 lbs', '5' 0" - 5' 5"'."""
    specs = parse("najera").specs
    assert specs.age is None
    assert specs.weight_lbs is None
    assert specs.height_cm is None


@pytest.mark.parametrize("text,cm", [
    # weston: the abbreviation's full stop once made the inches vanish.
    ("5 ft. 9 in.", 175.3),
    ("5 ft.", 152.4),
    # pscarolina runs the figure into the unit.
    ("5ft. 2in.", 157.5),
    ("5ft.", 152.4),
    ("5' 4\"", 162.6),
    ("5 feet 6 inches", 167.6),
    ("5'", 152.4),
])
def test_height_reads_the_inches_in_every_published_spelling(text, cm):
    assert rm._rm_height_cm(text) == cm


def test_height_does_not_take_the_next_figure_as_inches():
    """A three-digit weight after the feet is not 13 inches."""
    assert rm._rm_height_cm("5 ft. 130 lbs") == 152.4


@pytest.mark.parametrize("narrative,lbs", [
    # sbschooler 1722: a weight CHANGE, not a weight.
    ("This active sixty year old lady lost 70 pounds after weight loss surgery.", None),
    ("She had gained 25 lbs since her pregnancy.", None),
    ("after losing 40 lbs she is 130 lbs and 5'4\"", 130),
    ("5’4”, 130lbs, 23-year-old breast augmentation", 130),
])
def test_narrative_weight_skips_a_weight_change(narrative, lbs):
    assert rm._rm_weight_lbs({}, narrative) == lbs


def test_najera_label_on_its_own_line_still_reads_as_a_field():
    assert parse("najera").specs.fields["implant details"] == "650cc SRF"


# ---------------------------------------------------------------------------
# Purity, screened on the case's own text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clinic,reason", [
    ("bottger", "breast lift"),
    ("sbbreast", "breast lift"),
])
def test_combined_case_filed_in_the_augmentation_category_is_excluded(clinic, reason):
    """Both are filed under breast augmentation and say otherwise themselves."""
    case = parse(clinic)
    assert case.pairs == []
    assert any(reason in w for w in case.warnings)


def test_excluded_case_is_still_returned_so_the_run_accounts_for_it():
    """The wyten rule: a screened-out case is returned with a reason and no
    pairs, never dropped, so a run reconciles against the published gallery."""
    case = parse("bottger")
    assert case.case_id == "3884"
    assert case.warnings


@pytest.mark.parametrize("text", [
    "she did not want a breast lift and opted for augmentation alone",
    "we discussed a lift as an option but she chose augmentation",
    "she may need a lift in the future",
])
def test_a_declined_or_hypothetical_second_procedure_is_not_a_combined_case(text):
    """The ncps lesson - this surgeon population routinely writes what the
    patient declined, and screening those costs pure cases outright."""
    assert rm.rm_impure_reason(text) is None


@pytest.mark.parametrize("text,reason", [
    ("breast augmentation with mastopexy", "mastopexy"),
    ("augmentation and abdominoplasty", "abdominoplasty"),
    ("implant exchange for larger implants", "implant exchange"),
    ("revision of a previous augmentation", "revision"),
    ("breast augmentation with fat transfer", "fat transfer"),
    # bottger 2097: the plural, emitted as pure before the screen read it.
    ("Bilateral Breast Augmentation\n"
     "225 cc smooth round moderate plus gel implants with bilateral mastopexies",
     "mastopexy"),
    # coberly 2270.
    ("60 year old woman before and 6 months after Breast Augmentation with fat injections.",
     "fat transfer"),
])
def test_named_second_procedure_is_screened_out(text, reason):
    assert rm.rm_impure_reason(text) == reason


# ---------------------------------------------------------------------------
# Duplicate-patient fingerprint (one practice, two domains)
# ---------------------------------------------------------------------------


def _solid(seed):
    """A deterministic pseudo-photo, as encoded bytes."""
    import io

    from PIL import Image
    image = Image.new("L", (64, 64))
    image.putdata([(seed * 7 + x * 3 + y * 5) % 256
                   for y in range(64) for x in range(64)])
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_the_same_photograph_hashes_identically():
    """A republished patient is the same pixels under a different asset id."""
    data = _solid(3)
    assert rm.rm_hash_distance(rm.rm_image_hash(data),
                               rm.rm_image_hash(data)) == 0


def test_different_photographs_are_far_apart():
    distance = rm.rm_hash_distance(rm.rm_image_hash(_solid(3)),
                                   rm.rm_image_hash(_solid(29)))
    assert distance > rm.DUPLICATE_HAMMING_MAX


def test_duplicate_threshold_is_well_below_measured_different_patients():
    """weston 52/53 and leber 30/38 publish near-identical chart text and are
    four different women; their photographs sit 108 and 94 bits apart out of
    256. The threshold has to stay far below that, which is why duplicates are
    decided on pixels and never on text."""
    assert rm.DUPLICATE_HAMMING_MAX < 94


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


def test_fourteen_clinics_share_this_one_parser():
    clinics = [k for k, v in sg.CLINICS.items() if v.kind == "rm_gallery2"]
    assert len(clinics) == 14


def test_every_rosemont_clinic_carries_a_traceable_consent_ref():
    for slug, cfg in sg.CLINICS.items():
        if cfg.kind in ("rm_gallery2", "folk"):
            assert cfg.consent_ref == f"{slug}-consent-2026-08-25-rosemont-16"


def test_tabbal_is_deliberately_not_registered():
    """Withheld by captain ruling 2026-08-26: its burned-in Before/After word
    contradicts the gallery's own slots. Deferred to the crop-spec
    consolidation, not abandoned - see DECISION-2026-08-26-tabbal.md."""
    assert "tabbal" not in sg.CLINICS
    assert "drtabbal" not in sg.CLINICS
    for cfg in sg.CLINICS.values():
        urls = [cfg.base_url] + [sg.gallery_url(cfg, p) for p in cfg.gallery_paths or []]
        assert not any("tabbal" in urlsplit(url).netloc for url in urls), cfg.slug


def test_santa_barbara_is_one_clinic_across_two_domains():
    cfg = sg.CLINICS["sbschooler"]
    assert len(cfg.gallery_paths) == 2
    assert cfg.gallery_paths[1].startswith("https://www.santabarbarabreast.com")
    assert sg.gallery_url(cfg, cfg.gallery_paths[1]).startswith(
        "https://www.santabarbarabreast.com")
    assert sg.gallery_url(cfg, cfg.gallery_paths[0]) == (
        cfg.base_url + "/gallery/breast/breast-augmentation/")


def test_listing_enumerates_case_slugs_in_numeric_order():
    listing = ('<a href="/gallery/breast/breast-augmentation/patient-10/">a</a>'
               '<a href="/gallery/breast/breast-augmentation/patient-2/">b</a>'
               '<a href="/gallery/breast/breast-augmentation/patient-2/">dup</a>')
    assert rm.rm_list_cases(listing, "/gallery/breast/breast-augmentation/") == [
        "patient-2", "patient-10"]


# ---------------------------------------------------------------------------
# collect_cases: two domains, one practice
# ---------------------------------------------------------------------------


SB_FIRST = "https://www.sbplasticsurgery.com"
SB_SECOND = "https://www.santabarbarabreast.com"


def _noise_png(seed):
    import numpy as np
    from PIL import Image
    pixels = np.random.default_rng(seed).integers(0, 256, (64, 64), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels).save(buf, format="PNG")
    return buf.getvalue()


def _asset(host, case_number, slot):
    return f"{host}/wp-content/uploads/rmgallery2/RMG9{case_number}-{case_number}-{slot}"


def _rm_case_page(host, case_number):
    return ('<div class="single-case-content"><div class="img-wrap">'
            f'<div class="before-img"><img src="{_asset(host, case_number, "b")}/medium.jpg"></div>'
            f'<div class="after-img"><img src="{_asset(host, case_number, "a")}/medium.jpg"></div>'
            '</div><p>Breast augmentation with 350cc smooth round silicone implants.</p></div>')


def test_collect_cases_reads_both_domains_and_drops_republished_patients(tmp_path):
    """sbschooler's two galleries both end in 'breast-augmentation/' on
    different hosts, so a listing key derived from the path replayed the first
    listing as the second. A patient republished under a new case number is
    caught on the pixels, a repeated case number on the number, and a case
    whose photograph cannot be fetched is kept rather than dropped."""
    cfg = sg.CLINICS["sbschooler"]
    first, second = (sg.gallery_url(cfg, path) for path in cfg.gallery_paths)

    def put(key, data):
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    put("sbschooler_listing_g0.html",
        f'<a href="{first}patient-1/">1</a><a href="{first}patient-2/">2</a>')
    put("sbschooler_listing_g1.html",
        "".join(f'<a href="/gallery/breast-augmentation/patient-{n}/">{n}</a>'
                for n in (1, 2, 3)))
    pages = {("g0", 1): (SB_FIRST, 100), ("g0", 2): (SB_FIRST, 200),
             ("g1", 1): (SB_SECOND, 300), ("g1", 2): (SB_SECOND, 200),
             ("g1", 3): (SB_SECOND, 400)}
    for (gallery, slug), (host, number) in pages.items():
        put(f"sbschooler_case_{gallery}_patient-{slug}.html", _rm_case_page(host, number))
    # 300 republishes 100's photograph; 400's is never cached.
    for host, number, seed in ((SB_FIRST, 100, 1), (SB_FIRST, 200, 2), (SB_SECOND, 300, 1)):
        url = f"{_asset(host, number, 'b')}/original.jpg"
        put(sg.image_cache_key("sbschooler", url), _noise_png(seed))

    cases = sg.collect_cases(cfg, sg.PoliteFetcher(tmp_path, delay=0, offline=True))

    assert [c.case_id for c in cases] == ["100", "200", "300", "200", "400"]
    assert [bool(c.pairs) for c in cases] == [True, True, False, False, True]
    assert cases[2].source_url.startswith(SB_SECOND)
    assert any("photographs of case 100" in w for w in cases[2].warnings)
    assert any("duplicate case number 200" in w for w in cases[3].warnings)
