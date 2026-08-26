"""Tests for scrape_gallery.py parsing against real (trimmed) gallery snapshots.

Fixtures under fixtures/gallery/ are text/HTML excerpts of real case pages
(gallery image tags + spec blocks). No patient images are stored.
"""

import json
import re
import sys
from pathlib import Path

import pytest
import requests

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
# sanantonio: BRAG book parsing (composite before|after images)
# ---------------------------------------------------------------------------


def test_sanantonio_parse_full_grid_case():
    case = sg.sanantonio_parse_case(
        load_fixture("sanantonio_case_12804.html"), "12804", "x")
    assert case.warnings == []
    assert [p.key for p in case.pairs] == ["angle1", "angle2", "angle3"]
    for pair in case.pairs:
        # Composite images: one URL carries both halves and must be split.
        assert pair.split_composite
        assert pair.before_url == pair.after_url
        assert pair.view_hint is None  # views are never labeled on the page
    specs = case.specs
    assert specs.age == 24
    assert specs.left_cc == 310 and specs.right_cc == 310
    assert specs.brand == "natrelle"  # documented 'Allergan' implant line
    assert specs.profile == "moderate"
    assert specs.months_post_op == 3.0
    # 'Silicone Gel' documents fill material, not shape; never invented.
    assert specs.shape is None
    # Height/Weight units are undocumented: kept verbatim, not interpreted.
    assert specs.fields["Height"] == "65"
    assert specs.fields["Weight"] == "115"
    assert specs.height == "" and specs.weight_lbs is None
    assert specs.fields["Gallery Case"] == "#12804"
    assert specs.fields["Procedures Performed"] == "Breast Augmentation"


def test_sanantonio_parse_sparse_case_notes_fallback():
    case = sg.sanantonio_parse_case(
        load_fixture("sanantonio_case_sparse.html"), "24004", "x")
    assert [p.key for p in case.pairs] == ["angle1", "angle2"]
    specs = case.specs
    assert specs.age is None
    assert specs.left_cc == 350 and specs.right_cc == 350  # from Case Notes
    assert specs.profile == "high"  # 'high profile' in the narrative
    assert specs.brand == "unknown"
    assert specs.months_post_op is None
    meta = sg.build_meta("sanantonio-24004-front", "front", specs, {}, {},
                         None, "sanantonio-agreement-2026-08")
    assert meta["volume_cc"] == 350
    assert meta["shape"] == "unknown"
    assert "months_post_op" not in meta


def test_sanantonio_full_profile_is_not_remapped():
    # Allergan's 'full profile' tier is not one of the schema's documented
    # profile words; it must stay unmapped (raw text survives in notes).
    specs = sg.CaseSpecs()
    sg.classify_brand_shape_profile(
        specs, "485 cc full profile silicone gel implants")
    assert specs.profile is None


def test_sanantonio_list_cases_dedupes_and_scopes():
    html = (
        '<a href="https://sanantonioplasticsurgery.com/before-after-photos/breast-augmentation/23829/">x</a>'
        '<a href="/before-after-photos/breast-augmentation/23818-2/">y</a>'
        '<a href="/before-after-photos/breast-augmentation/23829/">dup</a>'
        # sibling gallery and stale pagination must not match
        '<a href="/before-after-photos/breast-augmentation-with-lift/">no</a>'
        '<a href="/before-after-photos/breast-augmentation/page/2/">no</a>'
    )
    assert sg.sanantonio_list_cases(html) == ["23829", "23818-2"]


def test_sanantonio_missing_detail_view_warns():
    case = sg.sanantonio_parse_case("<html><body><p>none</p></body></html>",
                                    "24004", "x")
    assert case.pairs == []
    assert case.warnings == ["no brag-book case detail view found"]


def test_split_composite_image():
    import io

    from PIL import Image

    img = Image.new("RGB", (900, 450))
    left = Image.new("RGB", (450, 450), (200, 100, 100))
    right = Image.new("RGB", (450, 450), (100, 100, 200))
    img.paste(left, (0, 0))
    img.paste(right, (450, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    before_data, after_data = sg.split_composite_image(buf.getvalue())
    before, after = (Image.open(io.BytesIO(b)) for b in (before_data, after_data))
    assert before.size == (450, 450) and after.size == (450, 450)
    # Left half is the reddish 'before', right half the bluish 'after'.
    assert before.convert("RGB").getpixel((225, 225))[0] > 150
    assert after.convert("RGB").getpixel((225, 225))[2] > 150


def test_split_composite_image_rejects_portrait():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (450, 900)).save(buf, format="JPEG")
    with pytest.raises(ValueError, match="not landscape"):
        sg.split_composite_image(buf.getvalue())


def test_build_meta_months_post_op():
    specs = sg.sanantonio_parse_case(
        load_fixture("sanantonio_case_12804.html"), "12804", "x").specs
    meta = sg.build_meta("sanantonio-12804-front", "front", specs, {}, {},
                         None, "sanantonio-agreement-2026-08")
    assert meta["months_post_op"] == 3.0
    assert meta["brand"] == "natrelle"
    assert meta["profile"] == "moderate"
    assert meta["consent_ref"] == "sanantonio-agreement-2026-08"


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


class TestGramVolumesAreRecordedAsCc:
    """Captain ruling 2026-08-14 (`ba-viz-emit-backlog` report section 2).

    Anatomical implants are specified in grams; gel density is ~0.97 g/cc and
    clinics use the units interchangeably for one implant (drmiroshnik case64 is
    '255cc' in prose and '255g-...jpg' as a filename). A gram figure is recorded
    as volume_cc unconverted. Worth 118 drmiroshnik and 17 mitchellbrown cases.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Early 20s, no children, 290g anatomical high profile implants", (290.0, 290.0)),
            ("354gm Textured Round Gel Implant, Subpectoral Fold Incision", (354.0, 354.0)),
            ("425 grams anatomical P-URE implants", (425.0, 425.0)),
            ("L 240g, R 265g", (240.0, 265.0)),
            # Out of the 100-1000 schema range, so not an implant volume.
            ("she lost 12g", (None, None)),
        ],
    )
    def test_gram_volumes_parse(self, text, expected):
        assert sg.parse_fill_volumes(text) == expected

    def test_excised_tissue_in_grams_is_not_an_implant_volume(self):
        """sanantonio 24139: counting the 442 averages two unrelated numbers."""
        text = ("She selected midrange profile silicone breast implants, which for her "
                "base width came to the 457cc implant. ... 442 grams of tissue plus "
                "225 mL of lipoaspirate was removed.")
        assert sg.parse_fill_volumes(text) == (457.0, 457.0)

    @pytest.mark.parametrize("removed", ["fat", "tissue", "skin", "lipoaspirate"])
    def test_every_excision_noun_is_guarded(self, removed):
        assert sg.parse_fill_volumes(f"300cc implants; 480g of {removed} removed") == (300.0, 300.0)

    @pytest.mark.parametrize(
        "text,expected",
        [
            # The unit is routinely pluralised; a trailing \b alone would drop these.
            ("Mentor smooth saline 275 implants filled to 300ccs", (300.0, 300.0)),
            ("Submuscular Breast Augmentation. Silicone gels 270ccs.", (270.0, 270.0)),
        ],
    )
    def test_plural_units_still_parse(self, text, expected):
        assert sg.parse_fill_volumes(text) == expected


class TestAnatomicalIsTeardrop:
    """`\\banatomic\\b` never matched 'anatomical', the word clinics actually use.

    52 of drmiroshnik's 146 cases were recorded shape=unknown because of it, and
    that reached the corpus. Captain ruling 2026-08-14.
    """

    @pytest.mark.parametrize(
        "text", ["255g anatomical (teardrop) moderate profile implants",
                 "290g anatomical high profile implants",
                 "425cc RB shaped anatomic Cohesive Gel Implants",
                 "495cc medium height high profile anatomical Implants",
                 "335gm Shaped Gel Implant"],
    )
    def test_shaped_families_classify_as_teardrop(self, text):
        specs = sg.CaseSpecs()
        sg.classify_brand_shape_profile(specs, text)
        assert specs.shape == "teardrop"

    @pytest.mark.parametrize("text", ["350cc textured round breast implants",
                                      "500gm Round Gel Implants"])
    def test_round_is_unaffected(self, text):
        specs = sg.CaseSpecs()
        sg.classify_brand_shape_profile(specs, text)
        assert specs.shape == "round"

    def test_unrelated_anatomy_words_do_not_match(self):
        specs = sg.CaseSpecs()
        sg.classify_brand_shape_profile(specs, "respecting her chest anatomy and proportions")
        assert specs.shape is None


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
# 2026-08 batch: one test per new parser family
# ---------------------------------------------------------------------------


def test_harrington_parse_case():
    case = sg.harrington_parse_case(load_fixture("harrington_case_177.html"), "177", "x")
    assert [p.key for p in case.pairs] == ["view1", "view2", "view3"]
    assert case.pairs[0].before_url == "/wp-content/uploads/177/before-0.jpg"
    assert case.pairs[0].view_hint is None  # ordinal only; needs visual annotation
    specs = case.specs
    assert specs.age == 29
    assert specs.height == "5' 2"
    assert specs.weight_lbs == 110
    assert specs.left_cc == 385 and specs.right_cc == 385
    assert specs.brand == "sientra"
    assert specs.profile == "moderate"


def test_influx_swiper_lakeshore():
    case = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_01.html"), "01", "x",
        "/gallery/breast/breast-augmentation/")
    assert [p.key for p in case.pairs] == ["pair1"]
    assert case.pairs[0].before_url.endswith("01.jpg")
    assert case.pairs[0].after_url.endswith("02.jpg")
    specs = case.specs
    assert specs.left_cc == 445 and specs.right_cc == 445
    assert specs.shape == "round"
    assert specs.brand == "natrelle"
    assert specs.profile == "moderate"
    assert specs.age == 30
    assert specs.height == "5'3"
    assert specs.weight_lbs == 130


def test_influx_swiper_marina_narrative_specs():
    case = sg.influx_swiper_parse_case(
        load_fixture("marina_case_6155.html"), "6155", "x",
        "/before-after-gallery-los-angeles/breast-augmentation/")
    assert len(case.pairs) == 1
    specs = case.specs
    assert specs.age == 43
    assert specs.height == "5'7" and specs.weight_lbs == 123
    assert specs.left_cc == 255 and specs.right_cc == 255
    # A narrative is prose, not a spec chart: it must keep flowing to summary
    # (that is what parse_fill_volumes() reads), and it must not be mined for
    # placement/incision even when, as here, it names one.
    assert specs.summary.startswith("This 43 yr old")
    assert specs.placement is None and specs.incision is None


# ---------------------------------------------------------------------------
# influx_swiper: the three patient-details layouts the Influx template emits.
# Fixtures carry the verbatim spec block of the named lakeshore case.
# ---------------------------------------------------------------------------


def test_influx_swiper_bare_value_layout_recovers_specs():
    """Bug A: every spec is its own unlabelled <p>, so nothing had a label."""
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_62.html"), "62", "x",
        "/gallery/breast/breast-augmentation/").specs
    assert specs.left_cc == 400 and specs.right_cc == 400
    assert specs.placement == "submuscular"
    assert specs.incision == "inframammary"
    # The labelled placeholders the same block still prints carry no value and
    # must not be the only thing recovered.
    assert specs.fields == {}
    assert specs.summary == "Breast Augmentation with Silicone Implants"
    # 'Height#: n/a'/'Weight#: n/a': undocumented stays undocumented.
    assert specs.height == "" and specs.weight_lbs is None
    assert specs.height_cm is None and specs.weight_kg is None


def test_influx_swiper_bare_value_layout_sided_volume_and_frame():
    """Bug A + D: a bare volume line keeps the sides the clinic wrote."""
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_98.html"), "98", "x",
        "/gallery/breast/breast-augmentation/").specs
    # 'R-450cc, L-485cc'
    assert specs.left_cc == 485 and specs.right_cc == 450
    assert sg.volume_cc(specs) == 468
    assert specs.height == "5'8" and specs.weight_lbs == 131
    assert specs.height_cm == 172.7 and specs.weight_kg == 59.4
    assert specs.fields == {"Patient#": "207"}


def test_influx_swiper_single_paragraph_layout_recovers_volume():
    """Bug B: the whole block sits inside one wrapper <p>."""
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_124.html"), "124", "x",
        "/gallery/breast/breast-augmentation/").specs
    assert specs.left_cc == 345 and specs.right_cc == 345
    assert specs.fields["Implant volume"] == "345cc"
    assert specs.fields["Procedure"] == "Breast augmentation"
    assert specs.age == 32 and specs.height == "5'2\"" and specs.weight_lbs == 128
    assert specs.shape == "round" and specs.profile == "moderate"
    assert specs.placement == "submuscular" and specs.incision == "inframammary"


def test_influx_split_fields_reads_every_label_in_one_string():
    """Bug B at the level it actually bites: one string, many labels.

    partition(': ') splits once, so 'Procedure' used to absorb the rest of the
    block and the 'Implant volume' lookup missed a number that was right there.
    Verbatim flattened text of lakeshore case 124.
    """
    line = ("Procedure: Breast augmentation Implant Type: Silicone "
            "Implant volume: 345cc Implant Cohesivity: Natrelle Inspira "
            "Responsive Implant Implant Profile: Moderate Profile "
            "Implant Texture: Smooth Implant Shape: Round "
            "Implant Placement: Submuscular Incision: Inframammary "
            "Age: 32 Height: 5'2\" Weight: 128 "
            "Procedure Description: Breast augmentation with silicone breast implants")
    fields, leftover = sg._influx_split_fields(line)
    assert leftover == ""
    assert dict(fields)["Implant volume"] == "345cc"
    assert dict(fields)["Implant Placement"] == "Submuscular"
    assert dict(fields)["Procedure"] == "Breast augmentation"
    assert dict(fields)["Procedure Description"] == (
        "Breast augmentation with silicone breast implants")


def test_influx_split_fields_keeps_an_unknown_label():
    fields, leftover = sg._influx_split_fields("Implant Warranty: 10 years")
    assert fields == [("Implant Warranty", "10 years")]
    assert leftover == ""


def test_influx_swiper_bilateral_volume_averages_both_sides():
    """Bug D: 'Implant volume: 405 cc (left side), 445 cc (right side)'."""
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_04.html"), "04", "x",
        "/gallery/breast/breast-augmentation/").specs
    assert specs.left_cc == 405 and specs.right_cc == 445
    assert sg.volume_cc(specs) == 425  # the schema's average, not the left side
    assert "asymmetric volumes (left 405cc, right 445cc)" in sg.build_notes(specs, None)


def test_influx_swiper_unitless_bilateral_volume_stays_unread():
    """Bug C is out of scope and must not drift: '339 & 371' has no unit.

    Reading it as cc is an inference; only the captain may authorise it. The
    rest of the case's specs are still recovered.
    """
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_71.html"), "71", "x",
        "/gallery/breast/breast-augmentation/").specs
    assert specs.left_cc is None and specs.right_cc is None
    assert sg.volume_cc(specs) is None
    assert specs.profile == "moderate"
    assert specs.placement == "submuscular" and specs.incision == "inframammary"
    assert specs.height == "5'7" and specs.weight_lbs == 130


def test_influx_swiper_labelled_layout_gains_only_chart_fields():
    """The majority layout is untouched apart from the new chart metadata."""
    specs = sg.influx_swiper_parse_case(
        load_fixture("lakeshore_case_01.html"), "01", "x",
        "/gallery/breast/breast-augmentation/").specs
    assert specs.left_cc == 445 and specs.right_cc == 445
    assert specs.height_cm == 160.0 and specs.weight_kg == 59.0
    assert specs.placement is None and specs.incision is None


@pytest.mark.parametrize("height,expected", [
    ("5'3", 160.0),
    ("5'10", 177.8),
    ("5'2\"", 157.5),
    ("5’5", 165.1),
    ("", None),
    ("n/a", None),
    ("5.0” - 5.5”", None),  # sixsurgery publishes a bucket, not a height
    ("130", None),          # no unit documented
])
def test_height_to_cm(height, expected):
    assert sg.height_to_cm(height) == expected


def test_build_meta_carries_chart_fields():
    specs = sg.CaseSpecs(shape="round", left_cc=405.0, right_cc=445.0,
                         placement="submuscular", incision="inframammary",
                         height_cm=165.1, weight_kg=65.8)
    meta = sg.build_meta("lakeshore-04-front", "front", specs, {}, {}, None,
                         "lakeshore-agreement-2026-08")
    assert meta["volume_cc"] == 425
    assert meta["placement"] == "submuscular"
    assert meta["incision"] == "inframammary"
    assert meta["height_cm"] == 165.1 and meta["weight_kg"] == 65.8


def test_build_meta_omits_undocumented_chart_fields():
    meta = sg.build_meta("clinic-01-front", "front", sg.CaseSpecs(shape="round"),
                         {}, {}, None, "clinic-agreement-2026-08")
    assert not {"placement", "incision", "height_cm", "weight_kg"} & set(meta)


def test_austinweston_parse_case_excludes_thumbs_mobile():
    case = sg.austinweston_parse_case(load_fixture("austinweston_case_01.html"), "01", "x")
    assert len(case.pairs) == 1
    assert case.pairs[0].before_url.endswith("before.jpg")
    assert case.pairs[0].after_url.endswith("after.jpg")
    assert case.specs.left_cc == 375 and case.specs.right_cc == 375


def test_charlotte_parse_case_split_composites():
    case = sg.charlotte_parse_case(load_fixture("charlotte_case_01.html"), "01", "x")
    assert [p.key for p in case.pairs] == ["view1", "view2"]
    assert all(p.split_composite for p in case.pairs)
    assert case.specs.age == 34


def test_allure_parse_case_and_chain():
    case = sg.allure_parse_case(load_fixture("allure_case_01.html"), "01", "x")
    assert len(case.pairs) == 1
    assert case.specs.left_cc == 415 and case.specs.shape == "round"
    assert sg.allure_next_case_id(load_fixture("allure_case_01.html")) == "02"
    assert sg.allure_next_case_id(load_fixture("allure_case_last.html")) is None


def test_drtavakoli_parse_listing_filters_decoy_images():
    cases = sg.drtavakoli_parse_listing(load_fixture("drtavakoli_listing.html"), "x")
    assert [c.case_id for c in cases] == ["32286", "32288"]  # decoy (7154) excluded
    assert cases[0].pairs[0].split_composite
    assert cases[0].specs.left_cc == 400 and cases[0].specs.shape == "round"
    assert cases[0].specs.profile == "high"


def test_sixsurgery_parse_listing():
    (case,) = sg.sixsurgery_parse_listing(load_fixture("sixsurgery_listing.html"), "x")
    assert case.case_id == "43253"
    # A second gallery entry for the same case number is another angle of the
    # same patient, so it groups in as pair2 instead of becoming a second case
    # with a colliding id (which no annotation or pair_id could tell apart).
    assert [p.key for p in case.pairs] == ["pair1", "pair2"]
    assert case.pairs[1].before_url.endswith("angle2%20before.png")
    assert not case.pairs[0].split_composite
    # The 'sensitive content' eye icon sits inside the same container as each
    # photo; selecting by position instead of img.blurred-img made it the
    # 'after' image.
    assert case.pairs[0].before_url.endswith("43253%20before.png")
    assert case.pairs[0].after_url.endswith("43253%20after.png")
    specs = case.specs
    assert specs.left_cc == 275 and specs.right_cc == 275
    assert specs.profile == "moderate"
    assert specs.height == '5.5" - 6.0"'
    assert specs.weight_lbs == 100


def test_drmiroshnik_parse_listing_groups_by_caption():
    cases = sg.drmiroshnik_parse_listing(load_fixture("drmiroshnik_listing.html"), "x")
    assert len(cases) == 2
    case1 = cases[0]
    assert [p.key for p in case1.pairs] == ["front", "side"]
    assert case1.pairs[0].view_hint == "front"
    assert case1.pairs[1].view_hint == "side"
    assert all(p.split_composite for p in case1.pairs)
    assert case1.specs.left_cc == 350 and case1.specs.shape == "round"


def test_drrohrich_parse_listing_2x2_grid():
    (case,) = sg.drrohrich_parse_listing(load_fixture("drrohrich_listing.html"), "x")
    assert [p.key for p in case.pairs] == ["front", "side"]
    front = case.pairs[0]
    assert front.grid_shape == (2, 2)
    assert front.before_cell == (0, 0) and front.after_cell == (0, 1)
    assert front.view_hint == "front"
    assert case.pairs[1].view_hint == "side"
    specs = case.specs
    assert specs.age == 23
    assert specs.brand == "natrelle"  # 'Allergan' maps to its Natrelle implant line
    assert specs.profile == "moderate"
    assert specs.left_cc == 295 and specs.right_cc == 295


def test_drjeremyhunt_parse_listing():
    (case,) = sg.drjeremyhunt_parse_listing(load_fixture("drjeremyhunt_listing.html"), "x")
    assert case.case_id == "1197"
    assert {p.key for p in case.pairs} == {"oblique", "front", "side"}
    for p in case.pairs:
        assert p.split_composite
        assert p.before_url.endswith(".jpg") and "-768x512" not in p.before_url
    specs = case.specs
    assert specs.age == 41
    assert specs.shape == "teardrop"
    assert specs.left_cc == 420 and specs.right_cc == 420


def test_wny_parse_case_and_chain():
    case = sg.wny_parse_case(load_fixture("wny_case_8.html"), "8", "x")
    assert [p.key for p in case.pairs] == ["view1", "view2", "view3"]
    assert all(p.split_composite for p in case.pairs)
    # No L/R prefix in the narrative, so parse_fill_volumes falls back to its
    # documented unordered two-number case (not necessarily symmetric).
    assert case.specs.left_cc == 360 and case.specs.right_cc == 390
    assert sg.wny_next_case_ids(load_fixture("wny_case_8.html")) == ["1", "19"]


def test_privateclinic_parse_card():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(load_fixture("privateclinic_card.html"), "html.parser")
    card = soup.select_one("div.wp-block-group.is-style-thumbnail-format-2")
    case = sg.privateclinic_parse_card(card, "x")
    assert case.case_id == "089AR"
    assert case.pairs[0].split_composite
    assert case.specs.left_cc == 230 and case.specs.right_cc == 230


def test_mitchellbrown_parse_listing_excludes_lift_combo():
    cases = sg.mitchellbrown_parse_listing(load_fixture("mitchellbrown_listing.html"), "x")
    # 'ba-lift-case1' (combo augmentation+lift) is out of the implants-only scope.
    assert [c.case_id for c in cases] == ["round-gel7"]
    assert len(cases[0].pairs) == 2
    assert cases[0].specs.left_cc == 300 and cases[0].specs.right_cc == 270


def test_skplastic_parse_listing_2x3_grid():
    (case,) = sg.skplastic_parse_listing(load_fixture("skplastic_listing.html"), "x")
    assert [p.key for p in case.pairs] == ["front", "oblique", "side"]
    assert case.pairs[0].grid_shape == (2, 3)
    assert case.pairs[1].before_cell == (0, 1) and case.pairs[1].after_cell == (1, 1)
    assert case.specs.left_cc == 385 and case.specs.right_cc == 385


def test_heavenly_parse_listing_picks_gallery_column():
    cases = sg.heavenly_parse_listing(load_fixture("heavenly_listing.html"), "x")
    (case,) = cases
    assert [p.view_hint for p in case.pairs] == ["oblique-left", None]
    assert case.specs.left_cc == 425 and case.specs.brand == "mentor"


def test_mya_parse_listing():
    (case,) = sg.mya_parse_listing(load_fixture("mya_listing.html"), "x")
    # MYA_ID_RE captures the single hyphen-joined token immediately before
    # '-MYA<digits>-', not any multi-token initials prefix.
    assert case.case_id == "B-MYA706000"
    assert case.specs.left_cc == 375 and case.specs.right_cc == 350


def test_drgrover_parse_listing_relative_urls():
    (case,) = sg.drgrover_parse_listing(
        load_fixture("drgrover_listing.html"),
        "https://www.drgrover.com/gallery/breast-procedures/breast-augmentation/")
    assert case.case_id == "01"
    assert case.pairs[0].before_url == (
        "https://www.drgrover.com/gallery/breast-procedures/breast-augmentation/01/01.jpg")
    assert not case.specs.summary and not case.specs.fields  # no specs documented


def test_basu_parse_listing_page_bare_url_and_case_id():
    (case,) = sg.basu_parse_listing_page(load_fixture("basu_listing.html"), "x")
    assert case.case_id == "31722"
    assert "?" not in case.pairs[0].before_url  # query params stripped


def test_drteitelbaum_parse_listing_page_2x3_grid():
    (case,) = sg.drteitelbaum_parse_listing_page(load_fixture("drteitelbaum_listing.html"), "x")
    assert case.case_id == "161354"
    assert [p.key for p in case.pairs] == ["front", "oblique", "side"]
    assert case.pairs[0].grid_shape == (2, 3)


def test_crop_grid_cell():
    import io

    from PIL import Image

    img = Image.new("RGB", (900, 600))
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]
    cw, ch = 300, 300
    for i, color in enumerate(colors):
        row, col = divmod(i, 3)
        img.paste(Image.new("RGB", (cw, ch), color), (col * cw, row * ch))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    data = buf.getvalue()
    top_left = Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 3, (0, 0))))
    bottom_right = Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 3, (1, 2))))
    assert top_left.size == (300, 300)
    assert top_left.convert("RGB").getpixel((150, 150))[0] > 200  # red
    assert bottom_right.convert("RGB").getpixel((150, 150))[2] > 200  # blue/magenta


def test_resolve_view_prelabeled_schema_view():
    # heavenly's older filenames document laterality directly; a hint that is
    # already a full schema view resolves without any annotation.
    pair = sg.ImagePair("pair1", "b", "a", view_hint="oblique-right")
    assert sg.resolve_view(pair, {}) == ("oblique-right", None)


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


# ---------------------------------------------------------------------------
# etna parser family (Etna Interactive; 2026-08-15 consented batch)
#
# One parser serves twelve clinics, which is exactly the condition that invited
# the lakeshore single-layout mistake (218 pairs lost silently). Every
# description layout the batch publishes gets its own behavioural test below,
# pinned to a fixture holding the clinic's verbatim published page text.
# ---------------------------------------------------------------------------

BREAST_AUG_GALLERY = "/gallery/breast/breast-augmentation/"


def etna_case(fixture: str, case_id: str, gallery_path: str = BREAST_AUG_GALLERY):
    return sg.etna_parse_case(load_fixture(fixture), case_id, "x", gallery_path)


# -- layout A: labelled <strong>Label:</strong>value separated by <br> --------


def test_etna_layout_a_labelled_strong_fields():
    case = etna_case("etna_southeastern_case_191.html", "191")
    specs = case.specs
    assert specs.fields["Implant Size (Left)"] == "325cc"
    assert specs.fields["Implant Size (Right)"] == "325cc"
    assert specs.fields["Implant Type"] == "Saline"
    assert (specs.left_cc, specs.right_cc) == (325.0, 325.0)
    assert sg.volume_cc(specs) == 325
    assert specs.age == 46
    assert specs.height == "5'5"
    assert specs.height_cm == 165.1
    assert specs.weight_lbs == 130
    assert specs.weight_kg == 59.0


# -- layout B: labelled <br> list with a sided 'Filled to' sub-block ----------


def test_etna_layout_b_sided_filled_to_volumes():
    case = etna_case("etna_roth_case_219.html", "219",
                     "/before-after/breast/breast-augmentation/")
    specs = case.specs
    # The implanted volume is the FINAL fill (480), not the 420 shell size.
    assert (specs.left_cc, specs.right_cc) == (480.0, 480.0)
    assert sg.volume_cc(specs) == 480
    assert specs.placement == "submuscular"
    assert specs.incision == "periareolar"
    assert specs.shape == "round"
    assert specs.profile == "high"


# -- layout C: chart fields run together with NO delimiter --------------------


def test_etna_layout_c_undelimited_chart_splits_every_field():
    """northraleigh concatenates its chart with no separator at all.

    'Approach: InframammaryPlacement: SubfascialImplant size: 350 cc' has no
    word boundary before 'Placement', so a \\b-anchored label scan (or a split
    at the first ': ') yields ONE field whose value swallows the whole chart -
    the lakeshore failure mode. Every field must come back separately.
    """
    case = etna_case("etna_northraleigh_case_18.html", "18",
                     "/before-and-after/breast/breast-augmentation/")
    specs = case.specs
    assert specs.fields["Approach"] == "Inframammary"
    assert specs.fields["Placement"] == "Subfascial"
    assert specs.fields["Implant size"] == "350 cc"
    assert specs.fields["Implant type"].startswith("Smooth round")
    # The value must stop at the next label rather than run on into it.
    assert "Placement" not in specs.fields["Approach"]
    assert sg.volume_cc(specs) == 350
    assert specs.placement == "subfascial"
    assert specs.incision == "inframammary"


def test_etna_undelimited_labels_do_not_split_mid_word_generally():
    """The camel-case label boundary must not fire on ordinary capitalised prose."""
    fields, leftover = sg._etna_split_fields(
        "She discussed her BreastAugmentation options with Dr. Smith")
    assert fields == []
    assert leftover.startswith("She discussed")


# -- layout D: narrative prose carrying the volume ----------------------------


def test_etna_layout_d_narrative_prose_volume():
    case = etna_case("etna_coastal_case_122.html", "122")
    specs = case.specs
    assert sg.volume_cc(specs) == 470
    assert specs.shape == "round"
    assert specs.profile == "extra-high"
    assert "Breast Augmentation in Boston" in specs.summary
    # Placement/incision come from CHART text only. This case names both in
    # prose, and prose is not a chart (the marina precedent: a narrative may be
    # explaining the options rather than reporting this patient's).
    assert specs.placement is None
    assert specs.incision is None


# -- layout E: short unlabelled prose -----------------------------------------


def test_etna_layout_e_short_prose_bilateral_volume():
    case = etna_case("etna_wmips_case_9344.html", "9344")
    assert sg.volume_cc(case.specs) == 370
    assert case.specs.brand == "mentor"


def test_etna_layout_e_sided_prose_possessive_phrasing():
    """'385cc ... on her right, and a 325cc ... on her left' must keep its sides.

    Without the possessive form both sides fall through to the unsided
    fallback, which records the volumes in publication order and labels them
    backwards in notes. The average is right either way; the sides are not.
    """
    case = etna_case("etna_kochcarlisle_case_276.html", "276",
                     "/photo-gallery/breast-procedures/breast-augmentation/")
    specs = case.specs
    assert specs.right_cc == 385.0
    assert specs.left_cc == 325.0
    assert sg.volume_cc(specs) == 355


# -- layout F: 'No case details for this patient.' ----------------------------


def test_etna_layout_f_no_case_details_yields_no_volume():
    case = etna_case("etna_ablavsky_case_483.html", "483")
    assert case.specs.fields == {}
    assert case.specs.summary == ""
    assert sg.volume_cc(case.specs) is None


# -- layout G: no .case-description element at all ----------------------------


def test_etna_layout_g_missing_description_block_is_recorded():
    case = etna_case("etna_curtsinger_case_159.html", "159",
                     "/gallery/plastic-surgery/breast-augmentation/")
    assert sg.volume_cc(case.specs) is None
    assert any("no .case-description" in w for w in case.warnings)


# -- views: named filenames document laterality, positional ones do not -------


def test_etna_named_view_filenames_document_view_and_laterality():
    case = etna_case("etna_roth_case_219.html", "219",
                     "/before-after/breast/breast-augmentation/")
    assert [(p.key, p.view_hint) for p in case.pairs] == [
        ("front", "front"),
        ("left-oblique", "oblique-left"),
        ("left-side", "side-left"),
    ]
    # A named token resolves with no annotation and no visual call.
    for pair in case.pairs:
        assert sg.resolve_view(pair, {}) == (pair.view_hint, None)


def test_etna_positional_view_filenames_need_an_annotation():
    case = etna_case("etna_southeastern_case_191.html", "191")
    assert [p.key for p in case.pairs] == ["view-1", "view-2", "view-3"]
    for pair in case.pairs:
        assert pair.view_hint is None
        # No page-documented view: never guessed, skipped until annotated.
        assert sg.resolve_view(pair, {}) == (None, None)
    annotated = {"pairs": {"view-1": {"view": "front"}}}
    assert sg.resolve_view(case.pairs[0], annotated) == (
        "front", "visual inspection of downloaded images")


def test_etna_every_view_emitted_once_despite_webp_and_jpg_encodings():
    """Etna publishes each photograph as BOTH .jpg and .webp.

    Keying pairs on the URL instead of the view token emits every view twice,
    and the second copy would collide on pair_id at emit time.
    """
    html = load_fixture("etna_wmips_case_9344.html").replace(
        "-detail.jpg", "-detail.webp")
    both = load_fixture("etna_wmips_case_9344.html") + html
    case = sg.etna_parse_case(both, "9344", "x", BREAST_AUG_GALLERY)
    keys = [p.key for p in case.pairs]
    assert keys == sorted(set(keys))
    assert all(p.before_url.endswith(".jpg") for p in case.pairs)


def test_etna_back_view_has_no_schema_view_and_is_reported():
    case = etna_case("etna_ablavsky_case_483.html", "483")
    assert any("'back'" in w and "no schema view" in w for w in case.warnings)


# -- purity: the filename's procedure slug is the case's own procedure --------


def test_etna_mommy_makeover_case_excluded_by_procedure_slug():
    """A combined-procedure case in a breast-augmentation gallery.

    lukecurtsingermd lists case 159 under breast augmentation but publishes it
    as 'mommy-makeover-159-front-detail.jpg'. Its after photograph shows a
    change the implants did not cause, so it is excluded by captain ruling.
    """
    case = etna_case("etna_curtsinger_case_159.html", "159",
                     "/gallery/plastic-surgery/breast-augmentation/")
    assert case.pairs == []
    assert any("not pure breast augmentation" in w and "mommy-makeover" in w
               for w in case.warnings)


def test_etna_body_lift_case_excluded_by_procedure_slug():
    case = etna_case("etna_ablavsky_case_483.html", "483")
    assert case.pairs == []
    assert any("lower-circumferential-body-lift" in w for w in case.warnings)


def test_etna_pure_augmentation_case_is_kept():
    case = etna_case("etna_camp_case_124.html", "124")
    assert len(case.pairs) == 5
    assert not any("not pure" in w for w in case.warnings)


# -- volume units: the label supplies the unit, free prose does not -----------


def test_etna_labelled_bare_number_reads_as_cc():
    """Inside a field whose label names it as an implant size, a bare number
    or an ml figure reads as cc (2026-08-15 units ruling)."""
    assert sg._etna_labelled_volume("Implant Size", "350") == 350.0
    assert sg._etna_labelled_volume("Implant Size", "350 ml") == 350.0
    assert sg._etna_labelled_volume("Implant Size", "350cc") == 350.0
    # Grams are recorded unconverted, per the standing units ruling.
    assert sg._etna_labelled_volume("Implant Size", "330 grams") == 330.0


def test_etna_bare_number_outside_a_volume_label_is_not_a_volume():
    assert sg._etna_labelled_volume("Patient Weight", "130") is None
    assert sg._etna_labelled_volume("Patient Age", "350") is None
    # And a bare number in free prose never becomes a volume.
    assert sg.parse_fill_volumes("she was 350 in the study") == (None, None)


def test_etna_out_of_range_labelled_volume_is_dropped():
    assert sg._etna_labelled_volume("Implant Size", "12") is None
    assert sg._etna_labelled_volume("Implant Size", "5000") is None


# -- enumeration --------------------------------------------------------------


def test_etna_declared_total_read_from_gallery_js():
    listing = (
        'var EII_GALLERY_JS = {"CATEGORY_RESULTS":{"env":{"config":'
        '{"initial_num_cases":12},"state":{"showing":12,"cases_remaining":36,'
        '"total":48}}}};'
    )
    assert sg.etna_declared_total(listing) == 48


def test_etna_chain_links_yield_neighbour_case_paths():
    html = (
        '<a class="button secondary btn case-details-prev" '
        'href="https://www.se-plasticsurgery.com/gallery/breast/breast-augmentation/289/">Prev</a>'
        '<a class="button case-details-next btn secondary" '
        'href="https://www.se-plasticsurgery.com/gallery/breast/breast-augmentation/205/">Next</a>'
    )
    assert sg.etna_next_case_paths(html, "/gallery/") == [
        "/gallery/breast/breast-augmentation/289/",
        "/gallery/breast/breast-augmentation/205/",
    ]


def test_etna_chain_follows_links_into_other_categories():
    """The chain is scoped to the gallery ROOT, not to the procedure category.

    kochandcarlisle's augmentation cases link on into a liposuction category
    and camp's into mommy-makeover. Dropping those links ends the walk there,
    which cost 128 of 152 camp cases and 69 of 81 kochandcarlisle cases before
    the walk was widened. Off-category pages are traversed, never collected.
    """
    html = (
        '<a class="case-details-next" '
        'href="https://www.campplasticsurgery.com/gallery/body/mommy-makeover/77/">Next</a>'
    )
    assert sg.etna_next_case_paths(html, "/gallery/") == [
        "/gallery/body/mommy-makeover/77/"]


def test_etna_chain_ignores_links_outside_the_gallery_root():
    html = (
        '<a class="case-details-next" '
        'href="https://www.se-plasticsurgery.com/blog/12/">Next</a>'
    )
    assert sg.etna_next_case_paths(html, "/gallery/") == []


@pytest.mark.parametrize("gallery_path,root", [
    ("/gallery/breast/breast-augmentation/", "/gallery/"),
    ("/photo-gallery/breast-procedures/breast-augmentation/", "/photo-gallery/"),
    ("/before-and-after/breast/breast-augmentation/", "/before-and-after/"),
    ("/before-after/breast/breast-augmentation/", "/before-after/"),
])
def test_etna_gallery_root(gallery_path, root):
    assert sg.etna_gallery_root(gallery_path) == root


def test_etna_case_images_of_other_cases_are_ignored():
    html = load_fixture("etna_wmips_case_9344.html") + (
        '<img src="//images.wmips.com/content/images/'
        'breast-augmentation-9999-front-detail.jpg"/>')
    case = sg.etna_parse_case(html, "9344", "x", BREAST_AUG_GALLERY)
    assert all("-9344-" in p.before_url for p in case.pairs)


def test_etna_category_paths_from_gallery_index():
    """Category listings are the extra chain seeds.

    The chain is a set of disconnected components, so seeding only from the
    target category strands most of the gallery - 12 of kochandcarlisle's 81
    augmentation cases. Every category listing is a way into another component.
    """
    index = (
        '<a href="https://www.kochandcarlisle.com/photo-gallery/breast-procedures/breast-augmentation/">A</a>'
        '<a href="https://www.kochandcarlisle.com/photo-gallery/body-procedures/tummy-tuck/">B</a>'
        '<a href="/photo-gallery/facial-cosmetic-surgery/brow-lift/">C</a>'
        '<a href="/photo-gallery/breast-procedures/breast-augmentation/276/">a case, not a category</a>'
        '<a href="/about-us/">off gallery</a>'
        '<a href="/photo-gallery/">the index itself</a>'
    )
    assert sg.etna_category_paths(index, "/photo-gallery/") == [
        "/photo-gallery/breast-procedures/breast-augmentation/",
        "/photo-gallery/body-procedures/tummy-tuck/",
        "/photo-gallery/facial-cosmetic-surgery/brow-lift/",
    ]


# ---------------------------------------------------------------------------
# PoliteFetcher: transient-failure retry
# ---------------------------------------------------------------------------


class _FlakySession:
    """Fails `failures` times with a reset, then serves `payload`."""

    def __init__(self, failures: int, payload: bytes = b"ok", status: int = 200):
        self.failures = failures
        self.payload = payload
        self.status = status
        self.headers = {}
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise sg.requests.exceptions.ConnectionError("reset by peer")

        class R:
            status_code = self.status
            content = self.payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise sg.requests.exceptions.HTTPError(str(self.status_code))

        return R()


def _fetcher(tmp_path, monkeypatch, session):
    f = sg.PoliteFetcher(tmp_path, delay=0)
    f.session = session
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    return f


def test_fetcher_retries_transient_connection_error(tmp_path, monkeypatch):
    """A single reset must not end a multi-hundred-page enumeration.

    Three clinics' chain walks died mid-run on ConnectionResetError, and the
    resulting shortfall is indistinguishable from missing data unless the
    fetcher retries.
    """
    session = _FlakySession(failures=2, payload=b"page")
    f = _fetcher(tmp_path, monkeypatch, session)
    assert f.get("https://example.test/x", "x.html") == b"page"
    assert session.calls == 3
    assert f.retries_made == 2
    assert (tmp_path / "x.html").read_bytes() == b"page"


def test_fetcher_gives_up_after_max_attempts(tmp_path, monkeypatch):
    session = _FlakySession(failures=99)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.requests.exceptions.ConnectionError):
        f.get("https://example.test/x", "x.html")
    assert session.calls == sg.MAX_ATTEMPTS
    assert not (tmp_path / "x.html").exists()


def test_fetcher_retries_throttling_status(tmp_path, monkeypatch):
    session = _FlakySession(failures=0, payload=b"page", status=429)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.requests.exceptions.HTTPError):
        f.get("https://example.test/x", "x.html")
    # 429 is retried rather than accepted, then surfaced on the last attempt.
    assert session.calls == sg.MAX_ATTEMPTS


def test_fetcher_serves_cache_without_network(tmp_path, monkeypatch):
    (tmp_path / "x.html").write_bytes(b"cached")
    session = _FlakySession(failures=99)
    f = _fetcher(tmp_path, monkeypatch, session)
    assert f.get("https://example.test/x", "x.html") == b"cached"
    assert session.calls == 0


@pytest.mark.parametrize("token,view", [
    ("front", "front"),
    ("anterior", "front"),          # tccs case 10969 labels its front 'anterior'
    ("left-oblique", "oblique-left"),
    ("right-lateral", "side-right"),  # tccs spells a side view 'lateral'
])
def test_etna_named_view_token_maps_to_schema_view(token, view):
    assert sg.ETNA_VIEW_TOKENS[token] == view


@pytest.mark.parametrize("token", ["back", "front-arms-raised", "bent-forward"])
def test_etna_pose_tokens_are_not_folded_into_a_schema_view(token):
    """A different POSE is not a different view.

    'front-arms-raised' (drhasen case 362) and 'bent-forward' photograph the
    front, but mapping them onto 'front' would both mislabel the pose and
    collide with the case's real front view on pair_id. They are reported as
    having no schema view instead.
    """
    assert token not in sg.ETNA_VIEW_TOKENS
    assert token in sg.ETNA_NON_SCHEMA_VIEWS
    html = (f'<img src="//images.x.com/content/images/breast-augmentation-7-{token}'
            '-detail.jpg"/><div class="case-description"><p>350cc</p></div>')
    case = sg.etna_parse_case(html, "7", "x", BREAST_AUG_GALLERY)
    assert case.pairs == []
    assert any(token in w and "no schema view" in w for w in case.warnings)


# ---------------------------------------------------------------------------
# etna: the gallery case-list endpoint (2026-08-18 access grant)
# ---------------------------------------------------------------------------


TCCS_ENDPOINT = ("https://www.thecenterforcosmeticsurgery.net/wordpress/"
                 "wp-admin/admin-ajax.php")


def test_etna_ajax_config_reads_endpoint_and_action_off_the_listing():
    """The request target is the listing's own declaration, never a guess.

    Hardcoding '/wp-admin/admin-ajax.php' would be wrong on these sites: tccs
    serves WordPress from a /wordpress/ subdirectory, so its endpoint is
    /wordpress/wp-admin/admin-ajax.php. Reading it off EII_GALLERY_JS means a
    clinic whose listing declares no endpoint gets no request at all.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    assert sg.etna_ajax_config(listing) == (TCCS_ENDPOINT,
                                            "gallery_category_results")


def test_etna_ajax_config_absent_when_the_listing_declares_none():
    assert sg.etna_ajax_config("<html><body>no gallery here</body></html>") is None


def test_etna_filter_fields_carries_category_id_and_nothing_else():
    """Only the hidden category_id is submitted.

    The same form holds Gender / Age / provider controls. A browser submits them
    unset, and sending any of them with a value would FILTER the gallery - the
    opposite of enumerating it - so they are dropped rather than echoed back.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    assert sg.etna_filter_fields(listing) == {"category_id": "535"}


def test_etna_declared_total_still_reads_the_same_listing():
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    assert sg.etna_declared_total(listing) == 579


def test_etna_decode_ajax_returns_state_and_html():
    raw = (FIXTURES / "etna_tccs_ajax_page.json").read_bytes()
    state, html = sg.etna_decode_ajax(raw)
    assert state["total"] == 582
    assert state["showing"] == 3
    assert 'class="category-cases"' in html


def test_etna_ajax_case_paths_returns_paths_as_published():
    """Including the ones the practice files under another procedure.

    tccs's breast-augmentation category names /gallery/mommy-makeover/
    mommy-makeover/240/. That is the practice tagging a combined procedure with
    breast augmentation, and it is why the gallery's declared total is a count of
    TAGGED cases rather than of pure augmentations. The paths come back as
    published; scoping them is the caller's job.
    """
    _, html = sg.etna_decode_ajax((FIXTURES / "etna_tccs_ajax_page.json").read_bytes())
    assert sg.etna_ajax_page(html).paths == [
        "/gallery/breast-surgery/breast-augmentation/11531/",
        "/gallery/breast-surgery/breast-augmentation/400/",
        "/gallery/mommy-makeover/mommy-makeover/240/",
    ]


def test_etna_endpoint_refuses_a_clinic_with_no_access_grant():
    """The grant is what makes the request permitted, so it gates the code path.

    The executed AI-training consent covers USE of the material; access to the
    practice's own admin-ajax endpoint is a separate permission that only the
    practice can give. Seven of the twelve Etna clinics were fully enumerated
    without it and must not be re-fetched through it.
    """
    cfg = sg.CLINICS["roth"]
    assert cfg.endpoint_grant is None
    with pytest.raises(PermissionError):
        sg.etna_endpoint_case_paths(cfg, None, "", 68)


@pytest.mark.parametrize("slug", ["tccs", "camp", "kochcarlisle", "ablavsky",
                                  "colville"])
def test_etna_endpoint_grant_is_on_exactly_the_five_short_clinics(slug):
    assert sg.CLINICS[slug].endpoint_grant == (
        "clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md")


@pytest.mark.parametrize("slug", ["roth", "southeastern", "wmips", "hasen",
                                  "coastal", "northraleigh", "curtsinger"])
def test_etna_fully_enumerated_clinics_hold_no_endpoint_grant(slug):
    """The seven the chain walk already finished are out of scope by construction."""
    assert sg.CLINICS[slug].endpoint_grant is None


def _grant_root(tmp_path):
    """A corpus root with the endpoint access grant document actually filed in it.

    The gate resolves ClinicConfig.endpoint_grant against this root, so a test
    that reaches the endpoint has to put the grant on disk exactly as a real run
    does.
    """
    path = tmp_path / sg.CLINICS["tccs"].endpoint_grant
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Gallery case-list endpoint access granted 2026-08-18.\n")
    return tmp_path


def test_etna_endpoint_refuses_a_grant_path_that_is_not_on_disk(tmp_path):
    """A grant is a document, not a string.

    A stale or mistyped `endpoint_grant` would otherwise open the endpoint exactly
    as wide as a real grant: the permission was obtained from twelve practices
    individually and its boundary is the case list alone, so it is enforced
    against the tree the grant was filed in rather than against prose.
    """
    cfg = sg.CLINICS["tccs"]
    assert cfg.endpoint_grant
    with pytest.raises(PermissionError, match="not on disk"):
        sg.etna_endpoint_case_paths(cfg, None, "", 60, grant_root=tmp_path)


def test_etna_endpoint_accepts_the_grant_once_it_is_filed(tmp_path):
    """The same clinic passes the gate when the document really is there."""
    assert sg.endpoint_grant_document(
        sg.CLINICS["tccs"], _grant_root(tmp_path)).is_file()


class _RecordingSession:
    """Serves a scripted list of endpoint responses and records what was sent."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.posts = []
        self.headers = {}

    def post(self, url, files=None, timeout=None):
        self.posts.append((url, {k: v[1] for k, v in (files or {}).items()}))
        payload = self.pages.pop(0) if self.pages else _ajax_payload([], 0, 0)

        class R:
            status_code = 200
            content = payload

            def raise_for_status(self):
                return None

        return R()


def _ajax_payload(case_paths, showing, total, next_position=None):
    import base64 as _b64
    import json as _json
    cards = "".join(
        f'<div class="category-case-card"><div class="case-card-inner" '
        f'href="https://example.test{p}"></div></div>' for p in case_paths)
    html = f'<div class="category-cases">{cards}</div>'
    return _json.dumps({
        "_html": _b64.b64encode(html.encode("utf-8")).decode("ascii"),
        "env": {"state": {"showing": showing, "total": total,
                          "next_returned_position": next_position
                          or showing + 1}},
    }).encode("utf-8")


def test_etna_endpoint_sweep_pages_until_the_declared_total(tmp_path, monkeypatch):
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in range(1, 8)]
    session = _RecordingSession([
        _ajax_payload(paths[:3], 3, 7, next_position=4),
        _ajax_payload(paths[3:6], 3, 7, next_position=7),
        _ajax_payload(paths[6:], 1, 7, next_position=8),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act",
                                 {"category_id": "9"}, total=7, page_size=3,
                                 tag="a")
    assert got == paths
    assert [p[1]["first_returned_position"] for p in session.posts] == ["1", "4", "7"]
    assert all(p[1]["category_id"] == "9" for p in session.posts)
    assert all(p[1]["case_count"] == "3" for p in session.posts)


def test_etna_endpoint_sweep_stops_on_a_short_page(tmp_path, monkeypatch):
    """A page that returns fewer cards than asked for is the end of the set.

    Continuing past it asks the server for positions that do not exist, which is
    load spent on nothing - the thing the access grant explicitly asks us not to
    do.
    """
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in range(1, 5)]
    session = _RecordingSession([_ajax_payload(paths, 4, 900, next_position=5)])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=900, page_size=50, tag="a")
    assert got == paths
    assert len(session.posts) == 1


def test_etna_endpoint_sweep_deduplicates_across_pages(tmp_path, monkeypatch):
    """Positions are a way to sweep the set, never stable case identity.

    Measured on tccs: the result order is not the rendered listing's order and is
    not stable across different case_count values, so the same case can be handed
    back at more than one position.
    """
    a = "/gallery/breast/breast-augmentation/1/"
    b = "/gallery/breast/breast-augmentation/2/"
    session = _RecordingSession([
        _ajax_payload([a, b], 2, 4, next_position=3),
        _ajax_payload([b, a], 2, 4, next_position=5),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=4, page_size=2, tag="a")
    assert got == [a, b]


def test_etna_endpoint_second_sweep_runs_only_when_the_first_is_short(
        tmp_path, monkeypatch):
    """A full first sweep must not spend the site another whole pass."""
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    paths = [f"/gallery/breast-surgery/breast-augmentation/{i}/"
             for i in range(1, 4)]
    session = _RecordingSession([_ajax_payload(paths, 3, 3, next_position=4)])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=3,
                                     grant_root=_grant_root(tmp_path))
    assert got == paths
    assert len(session.posts) == 1


def test_etna_endpoint_second_sweep_uses_a_different_page_size(
        tmp_path, monkeypatch):
    """The order depends on the page size, so re-sweeping at the same size would
    just repeat the first pass rather than traverse the set differently."""
    assert sg.ETNA_AJAX_RETRY_PAGE_SIZE != sg.ETNA_AJAX_PAGE_SIZE
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    first = ["/gallery/breast-surgery/breast-augmentation/1/"]
    second = ["/gallery/breast-surgery/breast-augmentation/2/"]
    session = _RecordingSession([
        _ajax_payload(first, 1, 2, next_position=2),
        _ajax_payload(second, 1, 2, next_position=2),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=2,
                                     grant_root=_grant_root(tmp_path))
    assert got == first + second
    sizes = [p[1]["case_count"] for p in session.posts]
    assert sizes == [str(sg.ETNA_AJAX_PAGE_SIZE),
                     str(sg.ETNA_AJAX_RETRY_PAGE_SIZE)]


def test_etna_endpoint_posts_to_the_declared_url_with_the_action_query(
        tmp_path, monkeypatch):
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    session = _RecordingSession([_ajax_payload([], 0, 0)])
    f = _fetcher(tmp_path, monkeypatch, session)
    sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=1,
                               grant_root=_grant_root(tmp_path))
    url, body = session.posts[0]
    assert url == TCCS_ENDPOINT + "?action=gallery_category_results"
    assert body["action"] == "gallery_category_results"


def test_etna_endpoint_is_cached_so_a_rerun_costs_no_requests(tmp_path, monkeypatch):
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    paths = ["/gallery/breast-surgery/breast-augmentation/1/"]
    session = _RecordingSession([_ajax_payload(paths, 1, 1, next_position=2)])
    f = sg.PoliteFetcher(tmp_path, delay=0)
    f.session = session
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    grant = _grant_root(tmp_path)
    assert sg.etna_endpoint_case_paths(
        sg.CLINICS["tccs"], f, listing, 1, grant_root=grant) == paths
    offline = sg.PoliteFetcher(tmp_path, delay=0, offline=True)
    assert sg.etna_endpoint_case_paths(
        sg.CLINICS["tccs"], offline, listing, 1, grant_root=grant) == paths
    assert len(session.posts) == 1


def test_etna_endpoint_sweep_is_cache_bounded_offline(tmp_path, monkeypatch):
    """An offline re-parse must survive a cache taken before the sweep existed.

    Proving a shared parser's blast radius means re-parsing every cached case for
    every clinic offline. That has to keep working, so a missing endpoint
    response ends the sweep instead of the run.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    offline = sg.PoliteFetcher(tmp_path, delay=0, offline=True)
    assert sg.etna_endpoint_case_paths(
        sg.CLINICS["tccs"], offline, listing, 50,
        grant_root=_grant_root(tmp_path)) == []


def test_etna_endpoint_route_reads_the_listing_under_its_own_cache_key(
        tmp_path, monkeypatch):
    """A stale cached listing would reconcile against a stale declared total.

    tccs declared 579 cases when its listing was cached on 2026-08-15 and
    declares 582 now. Sweeping to the stale 579 would stop three cases short and
    report success, so the endpoint route fetches its own listing - under a key
    that leaves the shared cache entry every other clinic and every offline
    re-parse resolves against exactly as it was.
    """
    fresh = load_fixture("etna_tccs_listing_endpoint.html")
    stale = fresh.replace('"total":579', '"total":1')
    cfg = sg.CLINICS["tccs"]
    (tmp_path / "tccs_listing.html").write_text(stale)

    class _ListingSession:
        headers = {}

        def __init__(self):
            self.gets = []

        def get(self, url, timeout=None):
            self.gets.append(url)

            class R:
                status_code = 200
                content = fresh.encode()

                def raise_for_status(self):
                    return None

            return R()

        def post(self, url, files=None, timeout=None):
            class R:
                status_code = 200
                content = _ajax_payload([], 0, 0)

                def raise_for_status(self):
                    return None

            return R()

    session = _ListingSession()
    f = _fetcher(tmp_path, monkeypatch, session)
    sg.collect_cases(cfg, f, gallery_endpoint=True,
                     grant_root=_grant_root(tmp_path))
    assert (tmp_path / "tccs_listing_endpoint.html").exists()
    # The shared entry is untouched: same bytes, still the stale total.
    assert (tmp_path / "tccs_listing.html").read_text() == stale


class _EndpointRouteSession:
    """Serves a listing on GET, one scripted endpoint page on POST, case pages after."""

    headers = {}

    def __init__(self, listing: str, ajax_pages, case_html: bytes):
        self.listing = listing.encode()
        self.ajax = list(ajax_pages)
        self.case_html = case_html
        self.gets = []

    def get(self, url, timeout=None):
        self.gets.append(url)
        body = self.listing if url.endswith("/") and "gallery" in url and (
            url.rstrip("/").rsplit("/", 1)[-1] == "breast-augmentation") else self.case_html

        class R:
            status_code = 200
            content = body

            def raise_for_status(self):
                return None

        return R()

    def post(self, url, files=None, timeout=None):
        payload = self.ajax.pop(0) if self.ajax else _ajax_payload([], 0, 0)

        class R:
            status_code = 200
            content = payload

            def raise_for_status(self):
                return None

        return R()


def _reconciliation_line(capsys, slug):
    out = [l for l in capsys.readouterr().out.splitlines()
           if "case-list endpoint named" in l and slug in l]
    assert len(out) == 1, out
    return out[0].strip()


def test_endpoint_reconciliation_counts_a_dual_path_case_once(
        tmp_path, monkeypatch, capsys):
    """A case the practice files under two categories is ONE case, not two.

    `visited` dedupes on path, so counting in-category parses plus off-category
    paths reports one more case than the gallery declares whenever a case is
    reachable both ways - measured on this run's own cache for tccs 12145, camp
    507, kochcarlisle 600/614 and ablavsky 120/483. Four of the five clinics
    printed a spurious WARN over a sweep that really was complete.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html").replace(
        '"total":579', '"total":2')
    gallery = sg.CLINICS["tccs"].gallery_paths[0]
    both_ways = [f"{gallery}12145/",
                 "/gallery/motiva-implants/motiva-implants/12145/",
                 f"{gallery}400/"]
    case = ('<img src="//images.x.com/content/images/breast-augmentation-12145'
            '-front-detail.jpg"/><div class="case-description"><p>Implant Size: '
            '350cc</p></div>').encode()
    session = _EndpointRouteSession(listing, [_ajax_payload(both_ways, 3, 2)], case)
    f = _fetcher(tmp_path, monkeypatch, session)
    sg.collect_cases(sg.CLINICS["tccs"], f, gallery_endpoint=True,
                     grant_root=_grant_root(tmp_path))
    line = _reconciliation_line(capsys, "tccs")
    assert "WARN" not in line
    assert "all 2 declared case(s)" in line


def test_endpoint_reconciliation_still_warns_when_a_case_is_missed(
        tmp_path, monkeypatch, capsys):
    """The dangerous inverse: a double-count must not cancel a real shortfall.

    Two distinct cases, one of them also filed elsewhere, against a declared
    total of 3. The path-based count reached 3 and read as complete; the
    id-based count sees 2 and says so.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html").replace(
        '"total":579', '"total":3')
    gallery = sg.CLINICS["tccs"].gallery_paths[0]
    paths = [f"{gallery}12145/",
             "/gallery/motiva-implants/motiva-implants/12145/",
             f"{gallery}400/"]
    case = ('<img src="//images.x.com/content/images/breast-augmentation-12145'
            '-front-detail.jpg"/><div class="case-description"><p>Implant Size: '
            '350cc</p></div>').encode()
    session = _EndpointRouteSession(listing, [_ajax_payload(paths, 3, 3)], case)
    f = _fetcher(tmp_path, monkeypatch, session)
    sg.collect_cases(sg.CLINICS["tccs"], f, gallery_endpoint=True,
                     grant_root=_grant_root(tmp_path))
    line = _reconciliation_line(capsys, "tccs")
    assert "WARN" in line
    assert "named 2 distinct case(s)" in line
    assert "declares 3" in line


# ---------------------------------------------------------------------------
# etna endpoint: the backend-failure page must never read as end-of-set
# ---------------------------------------------------------------------------


# Verbatim body served by tccs on 2026-08-19, HTTP 200, in place of the cards.
ETNA_BACKEND_ERROR_HTML = (
    '<div  class="category-cases"><p class="error" style="margin: 0 1%;">We are '
    'currently experiencing technical difficulties. Administrators have been '
    'notified. Please try again later.</p></div>')


def _ajax_error_payload(showing, total):
    """The failure envelope, which is why the failure is invisible by default.

    The state block keeps counting up as though cases were served - `showing`
    rises, `cases_remaining` falls - so nothing but the fragment itself says the
    request failed.
    """
    import base64 as _b64
    import json as _json
    return _json.dumps({
        "_html": _b64.b64encode(ETNA_BACKEND_ERROR_HTML.encode()).decode("ascii"),
        "env": {"state": {"showing": showing, "total": total,
                          "cases_remaining": total - showing,
                          "next_returned_position": showing + 1}},
    }).encode("utf-8")


def test_etna_decode_ajax_rejects_the_backend_failure_page():
    with pytest.raises(sg.EtnaEndpointError, match="technical difficulties"):
        sg.etna_decode_ajax(_ajax_error_payload(500, 582))


def test_etna_backend_failure_is_not_read_as_the_end_of_the_set(
        tmp_path, monkeypatch):
    """The bug this guards against cost 132 of tccs's 582 declared cases.

    The endpoint answers 200 with an error paragraph and a state block that still
    counts up, so an empty card list is indistinguishable from a finished sweep.
    Treating it as the end reported 450 of 582 as a clean enumeration.
    """
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in range(1, 3)]
    session = _RecordingSession([
        _ajax_payload(paths, 2, 100, next_position=3),
        _ajax_error_payload(4, 100),
        _ajax_error_payload(4, 100),
        _ajax_error_payload(4, 100),
        _ajax_error_payload(4, 100),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.EtnaEndpointError):
        sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                               total=100, page_size=2, tag="a")


def test_etna_backend_failure_is_never_cached(tmp_path, monkeypatch):
    """A cached failure is replayed as data on every later run.

    The response is a well-formed 200, so it caches like any other page unless
    the body is validated BEFORE the write.
    """
    session = _RecordingSession([_ajax_error_payload(4, 100)] * 8)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.EtnaEndpointError):
        sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                               total=100, page_size=2, tag="a")
    assert list(tmp_path.glob("*.json")) == []


def test_etna_backend_failure_is_retried_before_giving_up(tmp_path, monkeypatch):
    """It is intermittent and it recovers, so it is a retry, not a ceiling."""
    paths = ["/gallery/breast/breast-augmentation/1/"]
    session = _RecordingSession([
        _ajax_error_payload(0, 1),
        _ajax_error_payload(0, 1),
        _ajax_payload(paths, 1, 1, next_position=2),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=1, page_size=1, tag="a")
    assert got == paths
    assert len(session.posts) == 3
    assert f.retries_made == 2


def test_etna_endpoint_error_backoff_waits_minutes_not_seconds():
    """Backing off is the courteous answer to a site saying its backend is down,
    and the access grant asks for exactly that in return for the permission."""
    assert min(sg.ETNA_AJAX_ERROR_BACKOFF) >= 60.0
    assert list(sg.ETNA_AJAX_ERROR_BACKOFF) == sorted(sg.ETNA_AJAX_ERROR_BACKOFF)


def test_etna_second_sweep_still_runs_after_the_first_is_abandoned(
        tmp_path, monkeypatch):
    """The failure lands at a different position under a different page size."""
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    b = "/gallery/breast-surgery/breast-augmentation/99/"
    first = [f"/gallery/breast-surgery/breast-augmentation/{i}/"
             for i in range(1, sg.ETNA_AJAX_PAGE_SIZE + 1)]
    session = _RecordingSession(
        [_ajax_payload(first, 50, 60, next_position=51)]
        + [_ajax_error_payload(50, 60)] * 4
        + [_ajax_payload([b], 1, 60, next_position=2)])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=60,
                                     grant_root=_grant_root(tmp_path))
    assert got == first + [b]


def test_etna_cumulative_showing_counter_cannot_signal_a_short_page(
        tmp_path, monkeypatch):
    """`state.showing` counts every case served so far, not this page's size.

    Measured on tccs: the nine 50-case pages reported showing 50, 100, ... 450.
    Comparing that against the page size would end the sweep after page one on
    any gallery whose second page is full.
    """
    p1 = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    p2 = [f"/gallery/breast/breast-augmentation/{i}/" for i in (3, 4)]
    session = _RecordingSession([
        _ajax_payload(p1, 2, 4, next_position=3),
        _ajax_payload(p2, 4, 4, next_position=5),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=4, page_size=2, tag="a")
    assert got == p1 + p2


def test_etna_a_repeat_inside_one_page_does_not_end_the_sweep(tmp_path, monkeypatch):
    """A full page that happens to repeat a case is still a full page.

    The card count is what says whether a page was short. Deduplicating inside a
    page before that check would read a full page as the end of the set.
    """
    a = "/gallery/breast/breast-augmentation/1/"
    b = "/gallery/breast/breast-augmentation/2/"
    session = _RecordingSession([
        _ajax_payload([a, a], 2, 4, next_position=3),
        _ajax_payload([b, b], 4, 4, next_position=5),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=4, page_size=2, tag="a")
    assert got == [a, b]
    assert len(session.posts) == 2


def test_etna_abandoned_sweep_keeps_the_pages_that_did_succeed(
        tmp_path, monkeypatch):
    """Pages served before the failure are real data, not collateral.

    Discarding them because a LATER page failed loses most of a gallery over its
    tail: tccs's sweep failed at position 451, so raising the 450 already
    collected away would have cost the whole clinic.
    """
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    session = _RecordingSession(
        [_ajax_payload(paths, 2, 100, next_position=3)]
        + [_ajax_error_payload(2, 100)] * 4)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.EtnaEndpointError) as excinfo:
        sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                               total=100, page_size=2, tag="a")
    assert excinfo.value.paths == paths


def test_etna_endpoint_sweep_never_repeats_a_position(tmp_path, monkeypatch):
    """A `next_returned_position` that does not advance must not loop forever.

    The state block is the flaky backend's own output, so it is untrusted input.
    A FULL page returned with a stuck position re-requests the same cache key,
    and after the first pass that is a pure cache read - no network call, no
    politeness sleep, no output. The run hangs silently instead of failing.
    """
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    session = _RecordingSession([_ajax_payload(paths, 2, 6, next_position=1)] * 8)
    f = _fetcher(tmp_path, monkeypatch, session)
    real = f.post_form
    calls = []

    def counted(*a, **kw):
        calls.append(a[0])
        if len(calls) > 6:
            raise AssertionError(
                "sweep kept re-requesting a position that never advanced")
        return real(*a, **kw)

    monkeypatch.setattr(f, "post_form", counted)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=6, page_size=2, tag="a")
    assert got == paths
    assert [p[1]["first_returned_position"] for p in session.posts] == ["1", "3", "5"]


def test_etna_endpoint_sweep_survives_a_non_numeric_position(tmp_path, monkeypatch):
    """A malformed state value falls back to one page forward, not to a crash."""
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    session = _RecordingSession([
        _ajax_payload(paths, 2, 4, next_position="soon"),
        _ajax_payload(["/gallery/breast/breast-augmentation/3/"], 3, 4,
                      next_position=None),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=4, page_size=2, tag="a")
    assert got == paths + ["/gallery/breast/breast-augmentation/3/"]
    assert [p[1]["first_returned_position"] for p in session.posts] == ["1", "3"]


def test_etna_truncated_body_still_keeps_the_pages_that_succeeded(
        tmp_path, monkeypatch):
    """The backend-failure page is not the only way this endpoint fails.

    A truncated body raises JSONDecodeError out of `validate`, which used to
    escape the sweep entirely and abort the clinic run - discarding exactly the
    pages EtnaEndpointError.paths exists to keep.
    """
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    session = _RecordingSession(
        [_ajax_payload(paths, 2, 100, next_position=3)]
        + [b'{"_html": "dGhpcyBpcyB0cnVuY2F0ZW'] * 8)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(sg.EtnaEndpointError) as excinfo:
        sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                               total=100, page_size=2, tag="a")
    assert excinfo.value.paths == paths


def test_etna_http_failure_still_keeps_the_pages_that_succeeded(
        tmp_path, monkeypatch):
    """A persistent 5xx is a requests.HTTPError, not an EtnaEndpointError."""
    paths = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    good = _ajax_payload(paths, 2, 100, next_position=3)

    class _FailsAfterOnePage:
        headers = {}

        def __init__(self):
            self.calls = 0

        def post(self, url, files=None, timeout=None):
            self.calls += 1
            served = self.calls == 1

            class R:
                status_code = 200 if served else 503
                content = good if served else b""

                def raise_for_status(self):
                    if not served:
                        raise requests.exceptions.HTTPError("503 Server Error")

            return R()

    f = _fetcher(tmp_path, monkeypatch, _FailsAfterOnePage())
    with pytest.raises(sg.EtnaEndpointError) as excinfo:
        sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                               total=100, page_size=2, tag="a")
    assert excinfo.value.paths == paths


class _ListingThenAjaxSession:
    """Serves one listing on GET and an empty endpoint page on every POST."""

    headers = {}

    def __init__(self, listing: str):
        self.listing = listing.encode()
        self.gets = []
        self.posts = []

    def get(self, url, timeout=None):
        self.gets.append(url)
        body = self.listing

        class R:
            status_code = 200
            content = body

            def raise_for_status(self):
                return None

        return R()

    def post(self, url, files=None, timeout=None):
        self.posts.append(url)

        class R:
            status_code = 200
            content = _ajax_payload([], 0, 0)

            def raise_for_status(self):
                return None

        return R()


def test_etna_endpoint_route_refuses_a_listing_with_no_declared_total(
        tmp_path, monkeypatch):
    """No declared total means no reconciliation, and no reconciliation is the
    whole failure this route exists to prevent.

    Sweeping to a total of 0 enumerates nothing, falls back to the 12 cases the
    listing renders, and exits zero - a ~98% under-collection reported as a clean
    run. That is how 582 declared cases became a confident 450.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html").replace(
        '"total":579', '"grand_total":579')
    assert sg.etna_declared_total(listing) is None
    session = _ListingThenAjaxSession(listing)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(RuntimeError, match="declares no case total"):
        sg.collect_cases(sg.CLINICS["tccs"], f, gallery_endpoint=True,
                         grant_root=_grant_root(tmp_path))
    assert session.posts == []


# ---------------------------------------------------------------------------
# etna: purity - the image slug is necessary but not sufficient
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", [
    # Verbatim from the five clinics' own case text.
    "She underwent a breast augmentation with a lift (mastopexy).",
    "Our patient received a breast augmentation with a breast lift.",
    "She underwent a Mommy Makeover consisting of a tummy tuck and a breast "
    "augmentation.",
    "Additionally, the patient underwent liposuction to the axillary area.",
    "Ablavsky performed a combined breast procedure - a breast augmentation and "
    "breast lift to provide volume and projection.",
    "This patient had a breast augmentation revision.",
    # A verb list cannot carry this screen: the clinic's own prose is typo'd.
    "Camp peformed a breast augmentation and tummy tuck.",
    # Nor this: no surgical verb at all.
    "Breast Augmentation with Nipple Reduction",
    "Our patient, 34, was able to achieve her desired result by using fat "
    "injections, which were taken during her Tummy Tuck procedure.",
    # A negation ANYWHERE in the sentence used to whitelist the whole sentence,
    # so each of these reported a combined procedure and read as pure. The cue
    # has to govern the mention it excuses, and every mention has to be excused.
    'She underwent a "mommy makeover" including an abdominoplasty and breast '
    'augmentation.',
    "Our patient received a breast augmentation with a breast lift, including a "
    "tummy tuck.",
    # The negation covers a DIFFERENT procedure from the one that happened.
    "She underwent a breast augmentation and a mastopexy, but did not want "
    "liposuction.",
    # "considered" sits after the lift it describes and does not unmake it.
    "Dr. Camp performed a breast augmentation with a breast lift, which she had "
    "considered for years.",
    # "instead of" governs the alternative, not the tummy tuck.
    "She had a breast augmentation and tummy tuck instead of a staged approach.",
    # Verbatim, and the two the sentence-level screen actually admitted to the
    # corpus. colville 432: a nipple reduction really was performed, excused by
    # an unrelated "After considering" 95 characters earlier.
    "After considering her needs and lifestyle, I suggested a course of action "
    "that consisted of performing a nipple reduction and using Allergan "
    "Natrelle Style SSM 275cc implants for augmentation.",
    # tccs 668: she avoided the ANCHOR mastopexy and had a periareolar one.
    '42 year-old woman with mild ptosis or drooping bilaterally and wished to '
    'avoid classic mastopexy "anchor" scars; had round saline implants '
    '(slightly larger on the left by 45cc) placed submuscularly, with a '
    '"Benelli" or "donut" periareolar (around the nipple) mastopexy '
    'bilaterally, to better position the nipples.',
    # colville 555, verbatim: a credential clause and a clinical fact in one
    # sentence. The bio cue must not launder what the same sentence says was
    # done - this case really is an implant removal, a capsulectomy and a
    # mastopexy, and it points at itself while saying so.
    "My technical proficiencies, honed at the distinguished Indiana University "
    "School of Medicine where I completed general surgery and plastic surgery "
    "residencies, guided me through this complex case involving implant removal "
    "with a full capsulectomy, secondary breast augmentation using 390cc saline "
    "implants and mastopexy with Galaform for added support.",
])
def test_etna_combined_procedure_is_excluded(sentence):
    assert sg.etna_combined_procedure_evidence(sentence) is not None


@pytest.mark.parametrize("sentence", [
    # Named but NOT done - the marina precedent: a narrative that names a
    # procedure may be explaining the options rather than reporting this one.
    "This 30 year-old woman wanted larger implants (DD+ bra size) but no lift.",
    "Although a lift was discussed, she was comfortable with the result.",
    "She did not have significant ptosis and therefore did not require a lift.",
    "45 year-old mother of 3 with early breast ptosis but wished to avoid a lift.",
    "While she did have a mild degree of ptosis she opted not to undergo a "
    "breast lift at this time.",
    "She came to my Toledo office to discuss her goals and explore options, "
    "including a breast lift, breast augmentation, or a combination.",
    "He recommended a dual-plane breast augmentation to achieve her goals "
    "without the need for breast lift scars.",
    # Surgeon boilerplate appended to every case page by these galleries.
    "I specialize in breast augmentation, breast lift, and breast reduction "
    "surgeries.",
    "In my practice located in Toledo, Ohio, I not only specialize in breast "
    "augmentation but also in breast lift, and breast reduction surgeries.",
    "My specialty in breast augmentation, lift, and reduction surgeries enables "
    "me to create personalized care plans.",
    "If you are interested in breast augmentation in Denver either as an "
    "individual procedure or as part of a Mommy Makeover, please call us.",
    # 'lift' in the gym sense.
    "As someone who maintains a very active lifestyle with CrossFit and weight "
    "lifting, she wanted an outcome that delivered fullness.",
    "I used a muscle-preserving technique to support her daily activities, such "
    "as frequent exercise and lifting weights.",
    # Verbatim, and the widest real gaps between a cue and the mention it
    # governs - these are what set the two window sizes. tccs 224 (77 before):
    # the next sentence reads "She chose to undergo augmentation alone".
    "She was offered the option of a breast augmentation alone, or augmentation "
    "in conjunction with a lift, given the relaxed shape of her breasts.",
    # tccs 696 (59 after): she declined the recommended lift.
    "It was recommended that she undergo a concomitant breast lift with a "
    "submuscular augmentation, but she was adamant about avoiding external "
    "breast scars, and was willing to accept an implant that sat a little "
    "lower.",
    # hasen 67: "lifting her nipples" is the effect of the implant, not a
    # mastopexy, and the cue that says so sits after it.
    "To improve appearance, I lowered the inframammary fold to allow room for "
    "the breast implant and give the illusion of lifting her nipples without "
    "the need for a breast lift (mastopexy).",
    # Credentials are surgeon bio, and these galleries append one to every case.
    # Both of these read as clinical fact unless the cue matches the word it was
    # written for.
    "Dr. Colville is a board certified plastic surgeon whose practice covers "
    "breast augmentation, breast lift, and breast reduction.",
    "I completed my residency at the Indiana University School of Medicine, "
    "where breast augmentation, breast lift and breast reduction surgeries "
    "became my focus.",
])
def test_etna_pure_augmentation_is_kept(sentence):
    assert sg.etna_combined_procedure_evidence(sentence) is None


def test_etna_purity_evidence_is_scoped_to_the_sentence():
    """A clean case plus appended boilerplate must survive.

    Every colville case ends with the surgeon's bio, and scanning the block as
    one string flagged all 27 of its otherwise-clean cases.
    """
    text = ("A thirty-one-year-old woman approached me seeking a fuller breast "
            "appearance. We decided on Breast Augmentation in Toledo using "
            "325cc moderate profile gel implants. Holding board certification "
            "from the American Board of Plastic Surgery since 1992, I "
            "specialize in breast augmentation, breast lift, and breast "
            "reduction surgeries.")
    assert sg.etna_combined_procedure_evidence(text) is None


def test_etna_case_with_combined_text_emits_no_pairs():
    """The screen has to reach the pairs, not just the warning."""
    html = ('<img src="//images.x.com/content/images/breast-augmentation-7-front'
            '-detail.jpg"/><div class="case-description"><p>She underwent a '
            'breast augmentation with a lift (mastopexy). Implant Size: 350cc'
            '</p></div>')
    case = sg.etna_parse_case(html, "7", "x", BREAST_AUG_GALLERY)
    assert case.pairs == []
    assert any("not pure breast augmentation" in w and "mastopexy" in w
               for w in case.warnings)


def test_etna_pure_case_with_a_negated_lift_still_emits_pairs():
    html = ('<img src="//images.x.com/content/images/breast-augmentation-8-front'
            '-detail.jpg"/><div class="case-description"><p>She wanted larger '
            'implants but no lift. Implant Size: 350cc</p></div>')
    case = sg.etna_parse_case(html, "8", "x", BREAST_AUG_GALLERY)
    assert [p.key for p in case.pairs] == ["front"]


# ---------------------------------------------------------------------------
# A watermark on one half only is correlated with the label
# ---------------------------------------------------------------------------


def _jpeg(w, h, colour=(180, 140, 120)):
    import io as _io
    from PIL import Image as _Image
    buf = _io.BytesIO()
    _Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


def test_crop_bottom_trims_the_requested_rows():
    import io as _io
    from PIL import Image as _Image
    out = sg.crop_bottom(_jpeg(850, 637), 130)
    with _Image.open(_io.BytesIO(out)) as im:
        assert im.size == (850, 507)


def test_crop_bottom_is_a_no_op_at_zero():
    data = _jpeg(100, 100)
    assert sg.crop_bottom(data, 0) is data


def test_crop_bottom_refuses_to_crop_away_the_whole_image():
    with pytest.raises(ValueError):
        sg.crop_bottom(_jpeg(100, 100), 100)


@pytest.mark.parametrize("url", [
    "https://x.test/img/case-1-front.webp",
    "https://x.test/img/case-1-front.PNG",
    "https://x.test/img/case-1-front.jpeg",
])
def test_uncropped_halves_keep_the_source_extension(url):
    """The corpus legitimately mixes .jpg/.jpeg/.png/.webp and a `before.*` scan
    depends on the name being true to the bytes."""
    assert sg.emitted_image_name("before", url, 0) == (
        "before" + Path(url).suffix.lower())


@pytest.mark.parametrize("url", [
    "https://x.test/img/case-1-front.webp",
    "https://x.test/img/case-1-front.png",
    "https://x.test/img/case-1-front.jpg",
])
def test_cropped_halves_are_named_for_the_encoding_not_the_source(url):
    """crop_bottom re-encodes to JPEG, so a cropped half can only be a .jpg.

    Writing JPEG bytes into a `before.webp` is how a corpus starts lying about
    its own files - latent today only because no clinic yet combines a bottom
    crop with the non-composite emit branch.
    """
    assert sg.emitted_image_name("before", url, 130) == "before.jpg"


@pytest.mark.parametrize("slug,crop", [
    # Each measured by averaging every frame and high-passing the average, then
    # confirmed constant in PIXELS across the clinic's height groups.
    ("tccs", 130),   # colour-wheel logo + wordmark, BEFORE half
    ("roth", 175),   # "Jeffrey J. Roth, M.D., F.A.C.S." script, AFTER half
    ("camp", 110),   # "STEVEN CAMP MD PLASTIC SURGERY", AFTER half
    ("wny", 60),     # after-image caption, AFTER half
])
def test_one_sided_watermark_clinics_carry_a_measured_bottom_crop(slug, crop):
    """A mark on one half and not the other is a label leak.

    An edit model can satisfy "make the breasts larger" by learning to add or
    remove the mark, which teaches nothing about augmentation and scores as
    success in evaluation. The crop is what breaks that correlation. tccs carries
    its mark on the BEFORE half; roth, camp and wny on the AFTER half - so the
    crop is measured per clinic rather than transferred.
    """
    assert sg.CLINICS[slug].bottom_crop_px == crop


@pytest.mark.parametrize("slug", [
    "kochcarlisle", "ablavsky", "colville", "southeastern", "northraleigh",
    "hasen", "curtsinger", "coastal", "wmips",
])
def test_clinics_without_a_one_sided_mark_are_not_cropped(slug):
    """Cropping costs pairs at the 400px floor, so it is not applied on spec."""
    assert sg.CLINICS[slug].bottom_crop_px == 0


# ---------------------------------------------------------------------------
# etna endpoint: an unreadable card is not the end of the gallery
# ---------------------------------------------------------------------------


def test_etna_endpoint_sweep_reads_page_size_from_every_card_returned(
        tmp_path, monkeypatch):
    """One card this parser cannot read is not a short page.

    The sweep stops when a page comes back smaller than the one it asked for.
    Measuring that on the FILTERED paths makes a single unrecognised href end
    the sweep at that position - on a 50-card page that abandons the rest of the
    gallery, and the case-list route exists precisely to stop a truncated sweep
    from reading as a finished one.
    """
    good = [f"/gallery/breast/breast-augmentation/{i}/" for i in (1, 2)]
    unreadable = "/gallery/breast/breast-augmentation/featured/"
    session = _RecordingSession([
        _ajax_payload([good[0], unreadable, good[1]], 3, 6, next_position=4),
        _ajax_payload(["/gallery/breast/breast-augmentation/3/"], 1, 6,
                      next_position=7),
    ])
    f = _fetcher(tmp_path, monkeypatch, session)
    got = sg.etna_endpoint_sweep(f, "x", "https://example.test/aj", "act", {},
                                 total=6, page_size=3, tag="a")
    assert got == good + ["/gallery/breast/breast-augmentation/3/"]
    assert [p[1]["first_returned_position"] for p in session.posts] == ["1", "4"]


def test_etna_ajax_page_counts_cards_it_could_not_parse(tmp_path):
    """The card count is the page's size; the paths are what was readable."""
    _, html = sg.etna_decode_ajax(_ajax_payload(
        ["/gallery/breast/breast-augmentation/1/",
         "/gallery/breast/breast-augmentation/featured/"], 2, 2))
    page = sg.etna_ajax_page(html)
    assert page.paths == ["/gallery/breast/breast-augmentation/1/"]
    assert page.cards == 2


# ---------------------------------------------------------------------------
# etna endpoint: a declared total read from cache is a snapshot, and says so
# ---------------------------------------------------------------------------


def _endpoint_run(tmp_path, monkeypatch, cache_dir=None):
    """One --gallery-endpoint collection of a one-case tccs gallery."""
    listing = load_fixture("etna_tccs_listing_endpoint.html").replace(
        '"total":579', '"total":1')
    gallery = sg.CLINICS["tccs"].gallery_paths[0]
    case = ('<img src="//images.x.com/content/images/breast-augmentation-400'
            '-front-detail.jpg"/><div class="case-description"><p>Implant '
            'Size: 350cc</p></div>').encode()
    session = _EndpointRouteSession(
        listing, [_ajax_payload([f"{gallery}400/"], 1, 1)], case)
    f = sg.PoliteFetcher(cache_dir or tmp_path, delay=0)
    f.session = session
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    sg.collect_cases(sg.CLINICS["tccs"], f, gallery_endpoint=True,
                     grant_root=_grant_root(tmp_path))


def test_endpoint_reconciliation_says_the_total_was_freshly_fetched(
        tmp_path, monkeypatch, capsys):
    _endpoint_run(tmp_path, monkeypatch)
    line = _reconciliation_line(capsys, "tccs")
    assert "all 1 declared case(s)" in line
    assert "the total the gallery declares now" in line
    assert "REPLAYED" not in line


def test_endpoint_reconciliation_says_a_replayed_total_is_a_snapshot(
        tmp_path, monkeypatch, capsys):
    """A rerun reconciles against a cached denominator, and must not hide it.

    The endpoint route reads the listing under its own cache key so the declared
    total is current - but that key is written to the same persistent cache, so
    the freshness holds only the first time. On every later run the total is as
    old as the cache, and cases published since are invisible to it. The sweep
    still runs; what it must not do is report "named all N declared case(s)" as
    though N had just been checked against the site.
    """
    cache = tmp_path / "cache"
    _endpoint_run(tmp_path, monkeypatch, cache_dir=cache)
    capsys.readouterr()
    _endpoint_run(tmp_path, monkeypatch, cache_dir=cache)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "case-list endpoint named" in l]
    assert len(line) == 1, out
    assert "a REPLAYED cached total, not the gallery's current one" in line[0]
    assert "REPLAYED cache entry" in out


# ---------------------------------------------------------------------------
# Image emit: a missing photograph is tolerated, a refused site is not
# ---------------------------------------------------------------------------


class _EmitSession:
    """A one-case tccs gallery whose only photograph answers `image_status`."""

    headers = {}

    def __init__(self, image_status: int):
        self.image_status = image_status
        self.image_calls = 0

    def get(self, url, timeout=None):
        gallery = sg.CLINICS["tccs"].gallery_paths[0]
        if "images.x.com" in url:
            self.image_calls += 1
            status, body = self.image_status, b""
        elif url.endswith(f"{gallery}77/"):
            status, body = 200, (
                '<img src="//images.x.com/content/images/breast-augmentation-77'
                '-front-detail.jpg"/><div class="case-description"><p>Implant '
                'Size: 350cc</p></div>').encode()
        elif url.endswith(gallery):
            status, body = 200, (
                '<script>var EII_GALLERY_JS = {"env":{"state":{"total":1}}};'
                f'</script><a href="{gallery}77/">case</a>').encode()
        else:
            status, body = 404, b""

        class R:
            status_code = status
            content = body

            def raise_for_status(self):
                if status >= 400:
                    raise requests.exceptions.HTTPError(
                        f"{status} for {url}", response=self)

        return R()


def _run_emit(tmp_path, monkeypatch, image_status):
    session = _EmitSession(image_status)
    real = sg.PoliteFetcher

    def build(*a, **kw):
        f = real(*a, **kw)
        f.session = session
        return f

    monkeypatch.setattr(sg, "PoliteFetcher", build)
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    monkeypatch.setattr(sg.sys, "argv",
                        ["scrape_gallery.py", "--clinic", "tccs",
                         "--out", str(tmp_path / "out"), "--delay", "0"])
    return session, sg.main()


class _DuplicatePatientSession:
    """Two Page 1 Solutions categories publishing ONE patient's photographs."""

    headers: dict = {}
    IMAGE = b"\xff\xd8\xff\xe0 one patient's photograph \xff\xd9"

    def __init__(self, base: str):
        self.base = base

    def _listing(self, folder: str) -> bytes:
        root = f"./{folder}/"
        return (
            '<html><body><div class="patient"><div class="patient-info">'
            '<a>Breast Augmentation</a><div class="patient-meta-info">'
            '<strong>Implant Size:</strong> 350 High Profile</div></div>'
            f'<div class="slides"><div class="item">'
            f'<img src="{root}01.jpg"/><img src="{root}02.jpg"/>'
            "</div></div></div></body></html>").encode()

    def get(self, url, timeout=None):
        if url.endswith(".jpg"):
            body = self.IMAGE
        elif "ultra-high-profile" in url:
            body = self._listing("55")
        else:
            body = self._listing("44")

        class R:
            status_code = 200
            content = body

            def raise_for_status(self):
                return None

        return R()


class _RmgDuplicateSession:
    """Two gryskiewicz categories publishing ONE patient's case twice."""

    headers: dict = {}
    IMAGE = b"\xff\xd8\xff\xe0 one patient's photograph \xff\xd9"
    SHARED = "/wp-content/uploads/rmgallery2/RMG2862851087-8212"
    DUAL = "/gallery/breast/dual-plane-breast-augmentation/"
    SALINE = "/gallery/breast/saline-breast-augmentation/"

    def _case(self) -> bytes:
        return (
            '<html><body><section class="case-wrap"><div class="img-wrap">'
            f'<div class="before-img img-frame">'
            f'<img src="{self.SHARED}-b/original.jpeg"></div>'
            f'<div class="after-img img-frame">'
            f'<img src="{self.SHARED}-a/original.jpeg"></div>'
            '</div></section><div class="patient-details">'
            "<p>Implant Size: 350cc</p></div></body></html>").encode()

    def _listing(self, path: str, number: int) -> bytes:
        return (
            '<html><body><h1>Breast Augmentation</h1>'
            f'<div class="bna-group"><a href="{path}patient-{number}">'
            f'<img class="before-img" data-src="{self.SHARED}-b/small.jpeg">'
            "</a></div></body></html>").encode()

    def get(self, url, timeout=None):
        if url.endswith(".jpeg"):
            body = self.IMAGE
        elif url.rstrip("/").endswith(("patient-88", "patient-293")):
            body = self._case()
        elif self.DUAL in url:
            body = self._listing(self.DUAL, 88)
        else:
            body = self._listing(self.SALINE, 293)

        class R:
            status_code = 200
            content = body

            def raise_for_status(self):
                return None

        return R()


def test_one_patient_is_reported_even_when_only_one_copy_emits(
        tmp_path, monkeypatch, capsys):
    """The duplicate must not depend on both copies reaching the corpus.

    gryskiewicz publishes one patient in both its dual-plane and its saline
    category, citing the same five image URLs; under a fronts-only pass only
    one copy emits, so a check that compares emitted bytes sees one digest and
    reports nothing. The source URLs the collection already holds say it
    outright, whatever anybody annotates.
    """
    cfg = sg.ClinicConfig(
        slug="rmgdup", consent_ref="rmgdup-agreement",
        base_url="https://rmg.example.com",
        gallery_paths=[_RmgDuplicateSession.DUAL, _RmgDuplicateSession.SALINE],
        kind="rmgallery2")
    monkeypatch.setitem(sg.CLINICS, "rmgdup", cfg)
    annotations = tmp_path / "ann.json"
    annotations.write_text(json.dumps({
        "rmgdup:dual-plane-breast-augmentation-patient-88": {
            "pairs": {"pair1": {"view": "front"}}},
    }))

    session = _RmgDuplicateSession()
    real = sg.PoliteFetcher

    def build(*a, **kw):
        f = real(*a, **kw)
        f.session = session
        return f

    monkeypatch.setattr(sg, "PoliteFetcher", build)
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    monkeypatch.setattr(sg.sys, "argv",
                        ["scrape_gallery.py", "--clinic", "rmgdup",
                         "--out", str(tmp_path / "out"), "--delay", "0",
                         "--annotations", str(annotations)])
    assert sg.main() == 0

    out = capsys.readouterr().out
    emitted = sorted(p.parent.name
                     for p in (tmp_path / "out" / "rmgdup").glob("*/meta.json"))
    assert emitted == ["rmgdup-dual-plane-breast-augmentation-patient-88-front"]
    assert "patient(s) collected under more than one case key" in out
    assert ("dual-plane-breast-augmentation-patient-88 == "
            "saline-breast-augmentation-patient-293") in out
    assert _RmgDuplicateSession.SHARED + "-b/original.jpeg" in out


def test_one_patient_published_in_two_categories_is_reported(
        tmp_path, monkeypatch, capsys):
    """A clinic's categories are not always disjoint.

    ciaravino's ultra-high-profile category is a name-subset of its silicone
    one, so a case in both becomes two pair ids for one person - and
    build_dataset.py splits train/val BY PATIENT precisely so that one person
    cannot sit on both sides. Identical image bytes is the only signal that
    survives per-gallery case keys, so the collision is recorded (and only
    recorded - nothing is merged or renamed).
    """
    cfg = sg.ClinicConfig(
        slug="p1sdup", consent_ref="p1sdup-agreement",
        base_url="https://p1s.example.com",
        gallery_paths=["/gallery/breast-augmentation-silicone-implants/",
                       "/gallery/ultra-high-profile-silicone-implants/"],
        kind="page1solutions_paged")
    monkeypatch.setitem(sg.CLINICS, "p1sdup", cfg)
    annotations = tmp_path / "ann.json"
    annotations.write_text(json.dumps({
        "p1sdup:silicone-44": {"pairs": {"pair1": {"view": "front"}}},
        "p1sdup:ultra-high-profile-silicone-55": {
            "pairs": {"pair1": {"view": "front"}}},
    }))

    session = _DuplicatePatientSession(cfg.base_url)
    real = sg.PoliteFetcher

    def build(*a, **kw):
        f = real(*a, **kw)
        f.session = session
        return f

    monkeypatch.setattr(sg, "PoliteFetcher", build)
    monkeypatch.setattr(sg.time, "sleep", lambda s: None)
    monkeypatch.setattr(sg.sys, "argv",
                        ["scrape_gallery.py", "--clinic", "p1sdup",
                         "--out", str(tmp_path / "out"), "--delay", "0",
                         "--annotations", str(annotations)])
    assert sg.main() == 0

    out = capsys.readouterr().out
    emitted = sorted(p.parent.name
                     for p in (tmp_path / "out" / "p1sdup").glob("*/meta.json"))
    # Both pairs are still emitted: this detects and records, it does not merge.
    assert emitted == ["p1sdup-silicone-44-front",
                       "p1sdup-ultra-high-profile-silicone-55-front"]
    assert "byte-identical to p1sdup-silicone-44-front" in out
    assert "patient(s) collected under more than one case key" in out
    assert "silicone-44 == ultra-high-profile-silicone-55" in out


def test_a_missing_photograph_skips_the_pair_and_the_clinic_run_continues(
        tmp_path, monkeypatch, capsys):
    """tccs case 11336 publishes a front photograph that 404s.

    An unguarded raise there abandoned the remaining 300+ cases mid-run, and the
    shortfall looks exactly like a finished collection.
    """
    session, rc = _run_emit(tmp_path, monkeypatch, 404)
    assert rc == 0
    out = capsys.readouterr().out
    assert "image unavailable" in out
    assert "skipped 1 pair(s)" in out
    assert not list((tmp_path / "out" / "tccs").glob("*/meta.json"))


@pytest.mark.parametrize("status", [403, 503])
def test_a_refused_site_fails_the_run_instead_of_tallying_skips(
        tmp_path, monkeypatch, status):
    """A WAF block or a sustained outage is not a gap in the gallery.

    Swallowing it per pair turns every remaining pair into 'image unavailable',
    exits 0, and hands back a partial collection that reads as a complete one -
    the same failure the missing-image guard was written to prevent, reached
    from the other side.
    """
    with pytest.raises(requests.exceptions.HTTPError):
        _run_emit(tmp_path, monkeypatch, status)


# ---------------------------------------------------------------------------
# PoliteFetcher: the transport and validate retry budgets are separate
# ---------------------------------------------------------------------------


class _BlipThenRejectedSession:
    """One connection reset, then bodies the caller's `validate` will reject."""

    headers = {}

    def __init__(self, rejects: int):
        self.rejects = rejects
        self.calls = 0

    def post(self, url, files=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise requests.exceptions.ConnectionError("reset by peer")
        body = b"bad" if self.calls - 1 <= self.rejects else b"good"

        class R:
            status_code = 200
            content = body

            def raise_for_status(self):
                return None

        return R()


def _reject_bad(data):
    if data == b"bad":
        raise ValueError("backend failure page")


def test_a_transport_blip_does_not_spend_a_validate_retry(tmp_path, monkeypatch):
    """The two allowances answer to different failures, so they count apart.

    Sharing one counter let a single reset consume a rejected-body retry, and the
    endpoint sweep's whole point is that a rejected body is retried on the
    schedule the caller asked for - all of it.
    """
    session = _BlipThenRejectedSession(rejects=3)
    f = _fetcher(tmp_path, monkeypatch, session)
    assert f.post_form("https://example.test/aj", {}, "aj.json",
                       validate=_reject_bad,
                       retry_waits=(60.0, 180.0, 420.0)) == b"good"
    # 1 reset + 3 rejected bodies + the accepted one.
    assert session.calls == 5
    assert (tmp_path / "aj.json").read_bytes() == b"good"


def test_the_first_rejected_body_waits_the_first_retry_wait(
        tmp_path, monkeypatch):
    """Even when a transport blip came first.

    A shared counter started the validate schedule at retry_waits[1], so the
    first rejected body backed off three minutes instead of one - a schedule the
    caller never asked for.
    """
    waits = []
    session = _BlipThenRejectedSession(rejects=1)
    f = sg.PoliteFetcher(tmp_path, delay=0)
    f.session = session
    monkeypatch.setattr(sg.time, "sleep", lambda s: waits.append(s))
    assert f.post_form("https://example.test/aj", {}, "aj.json",
                       validate=_reject_bad,
                       retry_waits=(60.0, 180.0, 420.0)) == b"good"
    # The politeness floor is deducted from each wait, so this is the 60s step
    # of the schedule and nothing near the 180s one.
    assert any(59.0 <= w <= 60.0 for w in waits)
    assert max(waits) < 100.0


def test_every_rejected_body_is_retried_on_the_full_schedule(
        tmp_path, monkeypatch):
    """And the exception is raised, not swallowed, when all of them are."""
    session = _BlipThenRejectedSession(rejects=99)
    f = _fetcher(tmp_path, monkeypatch, session)
    with pytest.raises(ValueError):
        f.post_form("https://example.test/aj", {}, "aj.json",
                    validate=_reject_bad, retry_waits=(60.0, 180.0, 420.0))
    # 1 reset + the first body + one per retry_wait.
    assert session.calls == 5
    assert not (tmp_path / "aj.json").exists()


# ---------------------------------------------------------------------------
# sculpted: bespoke WordPress, paginated inline listing of framed composites
# ---------------------------------------------------------------------------


def test_sculpted_case_key_is_the_asset_stem_not_the_published_index():
    """The published 'Patient N' title is a display position, not an identity.

    Page 1's 'Patient 1' and 'Patient 2' are served from Patient_14_* and
    Patient_16_* respectively, and the two numberings run in opposite
    directions. Keying on the title would re-point every pair id the moment the
    practice publishes a new case at the top of the list, so the case key comes
    from the (immutable) wp-content upload stem and the display index is kept in
    the notes instead.
    """
    cases = sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p1.html"), "x")
    assert [c.case_id for c in cases] == ["14", "16"]
    assert [c.specs.fields["published as"] for c in cases] == [
        "Patient 1", "Patient 2"]


@pytest.mark.parametrize("filename,case_id,view", [
    ("Breast-Implants-Patient_7_Front.jpg", "7", "front"),    # the common spelling
    ("Breast-Implants-patient-19-front.jpg", "19", "front"),  # lowercase, hyphens
    ("Breast-Implants-patient_N7_Side.jpg", "n7", "side"),    # non-numeric stem
])
def test_sculpted_reads_all_three_filename_spellings(filename, case_id, view):
    """One gallery, three spellings of the same asset name.

    'Patient_7_Front', 'patient-19-front' and 'patient_N7_Front' all appear in
    this 13-case gallery; a parser that pinned one separator or assumed a purely
    numeric case number would silently drop the other two cases.
    """
    m = sg.SCULPTED_ASSET_RE.search(filename)
    assert m is not None
    assert (m.group(1).lower(), m.group(2).lower()) == (case_id, view)


def test_sculpted_page_two_yields_both_odd_spellings_as_cases():
    ids = [c.case_id for c in sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p2.html"), "x")]
    assert ids == ["19", "n7"]


def test_sculpted_pairs_are_framed_composites_with_bare_view_hints():
    cases = sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p1.html"), "x")
    pairs = cases[1].pairs
    assert [p.key for p in pairs] == ["front", "angle", "side"]
    # 'Angle' is this gallery's word for oblique. Neither the filename nor the
    # alt text documents laterality, so oblique/side stay bare hints and reach
    # the corpus only through an annotation.
    assert [p.view_hint for p in pairs] == ["front", "oblique", "side"]
    for pair in pairs:
        assert pair.split_composite and pair.before_url == pair.after_url
        assert pair.composite_border == sg.SCULPTED_BORDER_PX
        assert pair.composite_gutter == sg.SCULPTED_GUTTER_PX


def test_sculpted_strips_the_wordpress_size_suffix():
    """The fancybox href is already the bare original; keep it that way.

    Every image is also published as a '-768x432' derivative. Linking one would
    halve the resolution of a composite whose halves are only 578px wide after
    the frame trim, so the suffix is stripped rather than trusted.
    """
    assert sg.SCULPTED_SIZE_SUFFIX_RE.sub(
        "", "/x/Breast-Implants-Patient_7_Front-768x432.jpg"
    ) == "/x/Breast-Implants-Patient_7_Front.jpg"


def test_sculpted_reads_volume_age_and_post_op_from_the_caption():
    case = sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p3.html"), "x")[0]
    assert sg.volume_cc(case.specs) == 440
    assert case.specs.age == 32
    assert case.specs.months_post_op == 2.0
    assert case.specs.profile is None   # the gallery publishes no profile at all
    # The caption's own 'Patient 13 :' prefix is not clinical description.
    assert case.specs.summary.startswith("32F patient underwent")


def test_sculpted_keeps_asymmetric_volumes_on_their_documented_sides():
    """'a 420cc implant in right breast and a 315cc implant in left breast'."""
    case = sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p5_asym.html"), "x")[0]
    assert (case.specs.left_cc, case.specs.right_cc) == (315.0, 420.0)
    assert sg.volume_cc(case.specs) == 368
    assert "asymmetric volumes" in sg.build_notes(case.specs, None)


@pytest.mark.parametrize("weeks_phrase,months", [
    ("Post operative photos taken at 6 weeks.", 1.4),
    ("Post operative photos taken at 1 year.", 12.0),
    ("Post operative photos taken at 2.5 months.", 2.5),
])
def test_sculpted_converts_documented_post_op_units_to_months(weeks_phrase, months):
    m = sg.SCULPTED_POSTOP_RE.search(weeks_phrase)
    value, unit = float(m.group(1)), m.group(2).lower()
    assert round(value * sg.SCULPTED_POSTOP_UNIT_MONTHS[unit], 1) == months


@pytest.mark.parametrize("phrase", [
    "Bilateral Breast Augmentation with a lift",
    "Bilateral Breast Augmentation and Mastopexy",
    "Mommy Makeover",
])
def test_sculpted_drops_a_case_whose_text_names_a_second_procedure(phrase):
    """Purity is screened on the case TEXT; the slug carries no procedure here.

    Every image in this gallery is published under the same 'Breast-Implants-'
    prefix whatever the case was, so a filename screen would pass everything.
    """
    html = load_fixture("sculpted_listing_p3.html").replace(
        "Bilateral Breast Augmentation", phrase)
    case = sg.sculpted_parse_listing_page(html, "x")[0]
    assert case.pairs == []
    assert any("not pure breast augmentation" in w for w in case.warnings)


@pytest.mark.parametrize("phrase", [
    "Bilateral Breast Augmentation, which gives a natural lift",
    "Bilateral Breast Augmentation for a lifted appearance",
])
def test_sculpted_keeps_a_case_whose_prose_merely_says_lift(phrase):
    """'a natural lift' is what implants alone do, not a second procedure.

    A bare-'lift' screen would drop cases the captain ruling never meant to
    exclude, so only a lift named AS a procedure counts.
    """
    html = load_fixture("sculpted_listing_p3.html").replace(
        "Bilateral Breast Augmentation", phrase)
    case = sg.sculpted_parse_listing_page(html, "x")[0]
    assert len(case.pairs) == 3
    assert case.warnings == []


def test_sculpted_visual_exclusions_drop_their_cases_with_a_reason():
    """Two findings the case text cannot express, so they are enumerated.

    Case 14 carries a mosaic over an identifying mark in all three views (which
    censorship.py does not detect), and case n7's BEFORE photo shows a
    pre-existing mastopexy scar set behind a caption that names only an
    augmentation.
    """
    assert set(sg.SCULPTED_VISUAL_EXCLUSIONS) == {"14", "n7"}
    case = sg.sculpted_parse_listing_page(
        load_fixture("sculpted_listing_p1.html"), "x")[0]
    assert case.case_id == "14" and case.pairs == []
    assert any("mosaic censoring" in w for w in case.warnings)


def test_sculpted_composite_trim_is_symmetric_and_refuses_to_over_crop():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 675), "white").save(buf, format="JPEG")
    before, after = sg.split_composite_image(
        buf.getvalue(), border=sg.SCULPTED_BORDER_PX, gutter=sg.SCULPTED_GUTTER_PX)
    sizes = {Image.open(io.BytesIO(half)).size for half in (before, after)}
    # Both halves lose exactly the same amount, so the pair stays matched, and
    # 578x655 clears ingest.py's 400px floor.
    assert sizes == {(578, 655)}
    with pytest.raises(ValueError, match="leaves no image"):
        sg.split_composite_image(buf.getvalue(), border=10, gutter=600)


def test_split_composite_image_default_is_still_the_raw_midpoint_split():
    """The trim is opt-in: every composite clinic before sculpted is untouched."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 675), "white").save(buf, format="JPEG")
    before, after = sg.split_composite_image(buf.getvalue())
    assert {Image.open(io.BytesIO(h)).size for h in (before, after)} == {(600, 675)}


# ---------------------------------------------------------------------------
# aips (Breakdance page builder; CSS-background pairs on one inline listing)
# ---------------------------------------------------------------------------


AIPS_UPLOADS = "https://www.aiplasticsurgery.com/wp-content/uploads/"


def aips_cases():
    cases = sg.aips_parse_listing(load_fixture("aips_listing.html"), "src")
    return {c.case_id: c for c in cases}


def test_aips_reads_photos_from_css_backgrounds_not_the_label_imgs():
    """The <img> in each slot is a transparent BEFORE/AFTER label overlay; the
    patient photo is the slot div's background-image, declared in the page's
    inline <style>. A parser that trusted the <img> would collect the same two
    label PNGs for every case in the gallery."""
    case = aips_cases()["01"]
    assert [p.key for p in case.pairs] == [
        "PC236751-P2107653", "PC236763-P2107667", "PC236760-P2107664"]
    for pair in case.pairs:
        for url in (pair.before_url, pair.after_url):
            assert url.startswith(AIPS_UPLOADS + "bna_braug_01_")
            assert "bna_label_" not in url


def test_aips_direct_img_slot_wins_over_a_stale_background():
    """Case 14's third AFTER slot publishes its photo as a real <img> and also
    carries a background left over from case 01. The <img> is what a viewer
    sees, so it must win - reading the background there would emit another
    patient's photo as this patient's result."""
    pair = aips_cases()["14"].pairs[-1]
    assert pair.after_url == AIPS_UPLOADS + "bna_braug_14_P7093367.jpg"
    assert "bna_braug_01" not in pair.after_url


def test_aips_flags_a_republished_image_without_guessing_which_row_is_real():
    """Case 16 publishes one 'before' against two different 'afters' (the
    clinic never published a front before for it), so at most one of those two
    rows is a real pair. Both are kept and flagged: the markup does not say
    which, and only looking at the images does."""
    case = aips_cases()["16"]
    assert [p.key for p in case.pairs] == [
        "P3191094-P7163622", "P3191094-P7163633", "P3191090-P7163631"]
    assert case.pairs[0].before_url == case.pairs[1].before_url
    assert any("republishes an image" in w for w in case.warnings)


def test_aips_drops_a_row_whose_halves_belong_to_different_patients():
    html = (
        f'<style>.breakdance .bde-div-1-1{{background-image:url("{AIPS_UPLOADS}'
        f'bna_braug_07_P9114754.jpg");}}'
        f'.breakdance .bde-div-1-2{{background-image:url("{AIPS_UPLOADS}'
        f'bna_braug_08_P6022570.jpg");}}</style>'
        '<div class="bde-div-1-0 bde-div">'
        f'<div class="bde-div-1-1 bde-div"><img alt="Before" src="{AIPS_UPLOADS}'
        'bna_label_before.png"></div>'
        f'<div class="bde-div-1-2 bde-div"><img alt="After" src="{AIPS_UPLOADS}'
        'bna_label_after.png"></div>'
        '<div class="bde-div-1-3 bde-div"><div class="bde-rich-text"></div></div>'
        '</div>')
    cases = sg.aips_parse_listing(html, "src")
    assert [c.case_id for c in cases] == ["07"]
    assert cases[0].pairs == []
    assert any("belongs to case 08" in w for w in cases[0].warnings)


def test_aips_case_id_does_not_match_the_augmentation_with_lift_assets():
    """The practice publishes augmentation-with-lift under 'bna_braug_masto_NN'
    on a SEPARATE gallery. The id pattern requires digits directly after
    'braug_', so a looser one cannot read those in as augmentation cases."""
    assert sg.AIPS_ASSET_RE.search("bna_braug_masto_01_IMG_0697.jpg") is None
    m = sg.AIPS_ASSET_RE.search("bna_braug_07_P9114754.jpg")
    assert (m.group(1), m.group(2)) == ("07", "P9114754")
    # WordPress re-upload suffix is not part of the shoot token
    m2 = sg.AIPS_ASSET_RE.search("bna_braug_04_PA135387_2.jpg")
    assert (m2.group(1), m2.group(2)) == ("04", "PA135387")


def test_aips_parses_the_nine_field_spec_chart():
    specs = aips_cases()["01"].specs
    assert specs.age == 39
    assert specs.height == "5'8\""
    assert specs.height_cm == 172.7
    assert sg.volume_cc(specs) == 400
    assert specs.profile == "high"
    assert specs.shape == "round"          # SHELL: 'Smooth, Round'
    assert specs.incision == "inframammary"
    assert specs.fields["Type"] == "Silicone"
    assert specs.fields["Children"] == "2"


def test_aips_asymmetric_size_field_reads_both_sides():
    specs = aips_cases()["14"].specs
    assert (specs.left_cc, specs.right_cc) == (475.0, 500.0)
    assert sg.volume_cc(specs) == 488     # schema records the average


def test_aips_plane_is_not_decoded_into_the_placement_enum():
    """'Under Muscle' covers both submuscular and dual-plane and 'Above Muscle'
    covers both subglandular and subfascial, so neither is the documented
    schema value. Recorded verbatim, never guessed."""
    under, above = aips_cases()["01"].specs, aips_cases()["16"].specs
    assert under.fields["Plane"] == "Under Muscle"
    assert above.fields["Plane"] == "Above Muscle"
    assert under.placement is None and above.placement is None


def test_aips_notes_carry_the_undecodable_chart_fields():
    notes = sg.build_notes(aips_cases()["16"].specs, None)
    for expected in ("Plane: Above Muscle", "Type: Silicone", "Children: 3"):
        assert expected in notes


def test_aips_reports_a_case_whose_spec_chart_is_missing():
    html = (
        f'<style>.breakdance .bde-div-2-1{{background-image:url("{AIPS_UPLOADS}'
        f'bna_braug_09_P3201183.jpg");}}'
        f'.breakdance .bde-div-2-2{{background-image:url("{AIPS_UPLOADS}'
        f'bna_braug_09_P5122023.jpg");}}</style>'
        '<div class="bde-div-2-0 bde-div">'
        f'<div class="bde-div-2-1 bde-div"><img alt="Before" src="{AIPS_UPLOADS}'
        'bna_label_before.png"></div>'
        f'<div class="bde-div-2-2 bde-div"><img alt="After" src="{AIPS_UPLOADS}'
        'bna_label_after.png"></div>'
        '<div class="bde-div-2-3 bde-div"><div class="bde-rich-text"></div></div>'
        '</div>')
    case = sg.aips_parse_listing(html, "src")[0]
    assert len(case.pairs) == 1
    assert any("no spec chart" in w for w in case.warnings)


def test_aips_views_are_never_inferred_from_the_page():
    """Nothing in the markup, the alt text or the camera's DCIM filenames says
    which view a row is, so every pair must reach resolve_view with no hint and
    be skipped until a visual annotation supplies one."""
    for case in aips_cases().values():
        for pair in case.pairs:
            assert pair.view_hint is None
            assert sg.resolve_view(pair, {}) == (None, None)
    pair = aips_cases()["01"].pairs[0]
    assert sg.resolve_view(pair, {"pairs": {pair.key: {"view": "front"}}})[0] == "front"


# ---------------------------------------------------------------------------
# blaine: bespoke WordPress [gallery] shortcode
#
# The fixture is a trimmed real snapshot of
# blaineplasticsurgery.com/before-and-after/breast-procedures/breast-augmentation/
# holding one figure per behaviour pinned below (plus both patients published
# under case #115).
#
# One deliberate edit to that snapshot: the practice names several assets after
# what look like patient surnames ('EArnold_115_viewA'). Those stems are
# replaced with neutral placeholders ('CaseM_115_viewA') here and nowhere else -
# no other fixture in this directory carries a name-like stem, and a committed
# test file is not the place to introduce one. The markup, the case numbers and
# every caption are otherwise verbatim, so what the parser is exercised against
# is unchanged.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def blaine_cases():
    cases = sg.blaine_parse_listing(load_fixture("blaine_listing.html"), "x")
    return {c.case_id: c for c in cases}


def test_blaine_case_number_is_not_a_patient_key(blaine_cases):
    """#115 is TWO patients: a 36-yo with 'Sientra 415 HP' and the older
    'CaseM_115' patient with '325 CC'. Collapsing them would put one person
    in both halves of build_dataset.py's by-patient train/val split."""
    assert set(blaine_cases) >= {"115", "115b"}
    assert blaine_cases["115"].specs.age == 36
    assert blaine_cases["115b"].specs.age == 51
    assert sg.volume_cc(blaine_cases["115"].specs) is None   # '415 HP', no unit
    assert sg.volume_cc(blaine_cases["115b"].specs) == 325
    assert [p.key for p in blaine_cases["115b"].pairs] == [
        "CaseM_115_viewA", "CaseM_115_viewB"]


def test_blaine_combined_procedure_named_in_the_spec_field_is_excluded(blaine_cases):
    case = blaine_cases["169"]
    assert case.pairs == []
    assert any("liposuction" in w for w in case.warnings)


def test_blaine_combined_procedure_disclosed_only_in_the_image_alt(blaine_cases):
    """Case #111's Procedure reads 'Breast Augmentation with moderate plus
    profile 450cc silicone implants' - clean - while every one of its images
    carries alt='Case #111 Mommy Makeover'. Screening the spec field alone
    admits it, which is why the screen reads the whole figure."""
    case = blaine_cases["111"]
    assert "Makeover" not in case.specs.summary
    assert case.pairs == []
    assert any("Makeover" in w for w in case.warnings)


def test_blaine_multi_timepoint_strip_is_not_split(blaine_cases):
    """'Patient at pre op, 1 month post op, 3 months post op, and 6 months post
    op' is four panels in one file; a midpoint split would glue two timepoints
    into each half."""
    case = blaine_cases["23"]
    assert case.pairs == []
    assert any("multi-panel strip" in w for w in case.warnings)


def test_blaine_front_alt_is_a_view_hint_and_side_alt_is_not(blaine_cases):
    """The alt names 'front' or 'side'. Only 'front' is a schema view: this
    gallery uses 'side' for every non-front view, so a 5-view case reads
    front + 4x'side' spanning both obliques and both sides, and it never
    states laterality."""
    front = blaine_cases["91"].pairs[0]
    assert front.view_hint == "front"
    assert sg.resolve_view(front, {}) == ("front", None)
    side = blaine_cases["115"].pairs[0]
    assert side.view_hint is None
    assert sg.resolve_view(side, {}) == (None, None)


@pytest.mark.parametrize("case_id,left,right,average", [
    # '((L) 415cc HP, (Rt) 440cc HP ...)' - 'Rt' is not in the shared side
    # alternation, and the marker precedes its volume.
    ("33", 415, 440, 428),
    # '400 CC silicone implant (right) and 450 CC silicone implant (left)' -
    # the side follows the volume, several words later.
    ("117", 450, 400, 425),
    # 'left breast ... filled to 400 CC, and right breast ... filled to 500 CC'
    ("24", 400, 500, 450),
])
def test_blaine_reads_side_scoped_volumes(blaine_cases, case_id, left, right, average):
    specs = blaine_cases[case_id].specs
    assert (specs.left_cc, specs.right_cc) == (left, right)
    assert sg.volume_cc(specs) == average


def test_blaine_unitless_size_is_not_a_volume(blaine_cases):
    """'6 months post Breast Augmentation 455 MP+ Sientra subglandular' states
    a size with no unit, in a field labelled Procedure rather than implant
    size. A bare number in prose is not a documented volume."""
    assert sg.volume_cc(blaine_cases["38"].specs) is None
    assert blaine_cases["38"].specs.summary.count("455") == 1


@pytest.mark.parametrize("text,profile", [
    ("6 months post Breast Augmentation 455 MP+ Sientra subglandular", "moderate-plus"),
    ("1 month post Breast Augmentation 450cc MP Sientra subglandular", "moderate"),
    ("1 month post Breast Augmentation with Sientra 330HP submuscular", "high"),
    ("6 months post Breast Augmentation 450 HP mentor submuscular", "high"),
    # Spelled-out words win over any abbreviation in the same line.
    ("Breast Augmentation with moderate plus profile 350 CC silicone gel implants",
     "moderate-plus"),
    ("Breast Augmentation. Mentor 350 cc Moderate Profile implants", "moderate"),
    # Mentor's 'Xtra' is a product line, not a projection: case #158 takes
    # moderate-plus from the words beside it and never extra-high from 'Xtra'.
    ("Breast Augmentation with 440 CC silicone gel implants, Moderate Plus Profile Xtra",
     "moderate-plus"),
    # Nothing published: never defaulted.
    ("before and one month post-op, Motiva 245, Subglandular", None),
    ("Breast Augmentation with 450 CC saline implants", None),
])
def test_blaine_profile_decoding(text, profile):
    assert sg.blaine_parse_procedure(text).profile == profile


def test_blaine_xtra_never_reads_as_extra_high():
    specs = sg.blaine_parse_procedure(
        "Breast Augmentation with 440 CC silicone gel implants, Moderate Plus Profile Xtra")
    assert specs.profile != "extra-high"


def test_blaine_reads_the_clinics_own_misspellings_of_documented_values():
    """'inframmary' (cases #91/#92/#93) and 'High Profle' (#15) are the
    practice's spellings of values it did document; the raw line is kept."""
    specs = sg.blaine_parse_procedure(
        "submuscular inframmary Breast Augmentation with 400 CC silicone implants")
    assert specs.incision == "inframammary"
    assert "inframmary" in specs.summary
    specs = sg.blaine_parse_procedure(
        "Breast Augmentation. Mentor 450cc High Profle implants. Time post op 1 month.")
    assert specs.profile == "high"
    assert specs.months_post_op == 1


@pytest.mark.parametrize("text,months", [
    ("12 months post Breast Augmentation (350 cc HP Sientra submuscular)", 12),
    ("1 year post Breast Augmentation; 350cc HP Sientra subglandular", 12),
    ("before and one month post-op, 335 Sientra Moderate Plus, Subglandular", 1),
    ("Breast Augmentation. Ideal 350cc (L), 360cc (R) implants. Time post op 1 month.", 1),
    # A week figure is left unconverted rather than turned into a fraction of a
    # month the clinic never stated.
    ("Breast Augmentation. Sientra (gummy bear) 415cc implants. Time post op 1 week.", None),
    ("Breast Augmentation with 450 CC implants; High Profile", None),
])
def test_blaine_months_post_op(text, months):
    assert sg.blaine_parse_procedure(text).months_post_op == months


def test_blaine_placement_and_incision_come_from_the_chart_line(blaine_cases):
    specs = blaine_cases["91"].specs
    assert (specs.placement, specs.incision) == ("submuscular", "inframammary")
    # Nothing stated: omitted rather than guessed.
    assert blaine_cases["117"].specs.placement is None


def test_blaine_pairs_are_split_composites_of_the_full_size_original(blaine_cases):
    for case in blaine_cases.values():
        for pair in case.pairs:
            assert pair.split_composite is True
            assert pair.before_url == pair.after_url
            # The <a href> original, never the 540px <img> thumbnail.
            assert "-540x" not in pair.before_url
            assert pair.before_url.startswith("https://blaineplasticsurgery.com/")


# ---------------------------------------------------------------------------
# Page 1 Solutions (scripts/page1_solutions.py; first clinic on it is ncps)
# ---------------------------------------------------------------------------


def _page1_case(number):
    html = load_fixture(f"page1_ncps_case_{number}.html")
    return sg.page1_parse_case(
        html, number,
        "https://www.drgregpark.com/before-after-gallery-san-diego/"
        f"breast-augmentation/{number}/")


def test_page1_dispatch_routes_ncps_through_its_own_paginated_walk(tmp_path):
    """Two clinics on two Page 1 Solutions parsers must not share a `kind`.

    ncps and psiw were both registered `kind="page1"` after their collections
    merged, so the first matching branch in collect_cases claimed both and ncps
    was enumerated with psiw's inline-listing parser - which finds no
    `div.patient-holder` here and returns zero cases, a silent-zero run that
    looks exactly like a finished collection.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ncps_listing.html").write_text(
        load_fixture("page1_ncps_listing.html"))
    for number in ("9325", "11549"):
        (cache / f"ncps_case_{number}.html").write_text(
            load_fixture("page1_ncps_case_9325.html"))
    fetcher = sg.PoliteFetcher(cache, delay=0, offline=True)

    cases = sg.collect_cases(sg.CLINICS["ncps"], fetcher)

    assert [c.case_id for c in cases] == ["9325", "11549"]
    assert all(c.pairs for c in cases)


def test_page1_dispatch_routes_psiw_through_its_own_inline_listing(tmp_path):
    """psiw keeps the inline-listing parser it was collected with."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "psiw_listing.html").write_text(
        load_fixture("page1_psiw_listing.html"))
    fetcher = sg.PoliteFetcher(cache, delay=0, offline=True)

    cases = sg.collect_cases(sg.CLINICS["psiw"], fetcher)

    expected = sg.page1_parse_listing(
        load_fixture("page1_psiw_listing.html"), PSIW_GALLERY)
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
    listing = load_fixture("page1_ncps_listing.html")
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

    monkeypatch.setattr(sg, "PAGE1_MAX_PAGES", 3)
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
    cases = sg.p1.page1_list_cases(load_fixture("page1_ncps_listing.html"))
    assert [number for number, _ in cases] == ["9325", "11549"]
    assert cases[1][1].endswith("/ideal-breast-implant-2/")


def test_page1_numeric_slug_is_not_the_case_number():
    """Even a numeric slug disagrees with its own case number (slug 8901 is #12688)."""
    cases = sg.p1.page1_list_cases(load_fixture("page1_ncps_listing_p20.html"))
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


def test_page1_full_res_strips_only_the_size_suffix():
    assert sg.p1.page1_full_res("/files/2017/08/56674855-1of10-300x300.jpg") == (
        "/files/2017/08/56674855-1of10.jpg")
    assert sg.p1.page1_full_res("/files/a-2of10.jpg") == "/files/a-2of10.jpg"


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
    assert "Left" in sg.p1.page1_profile_field(case.specs.fields)
    assert case.specs.profile is None


@pytest.mark.parametrize("value,expected", [
    ("High", "high"),
    ("moderate plus", "moderate-plus"),
    ("Moderate High", None),   # not a term in the captain's profile mapping
    ("Classic Profile", None),
    ("See Below", None),
])
def test_page1_bare_profile_decodes_only_documented_terms(value, expected):
    assert sg.p1.page1_bare_profile(value) == expected


# ---------------------------------------------------------------------------
# choice (Webflow lightbox gallery, consented 2026-08-25)
# ---------------------------------------------------------------------------


CHOICE_FIXTURE = "choice_listing.html"


def _choice_cases():
    return sg.choice_parse_listing(load_fixture(CHOICE_FIXTURE), "x")


def test_choice_reads_every_view_from_the_lightbox_manifest():
    """The thumbnail <img> shows one view; the manifest lists them all.

    Case 1 publishes five composites and the page renders a single thumbnail,
    so a parser that walked the <img> tags would collect one pair in five.
    """
    case = _choice_cases()[0]
    assert case.case_id == "case1"
    assert [p.key for p in case.pairs] == ["pair1", "pair2", "pair3", "pair4", "pair5"]


def test_choice_takes_the_bare_original_not_a_srcset_downscale():
    """`-p-500`/`-p-800` derivatives would fall under the 400px floor once the
    composite is split (500/2 = 250 wide)."""
    for case in _choice_cases():
        for pair in case.pairs:
            assert "-p-500" not in pair.before_url
            assert "-p-800" not in pair.before_url


def test_choice_pairs_are_split_composites_sharing_one_url():
    case = _choice_cases()[0]
    for pair in case.pairs:
        assert pair.split_composite is True
        assert pair.before_url == pair.after_url


def test_choice_view_is_never_invented():
    """Neither the page nor the filenames document a view, so every pair must
    arrive without a hint and wait for an annotation."""
    for case in _choice_cases():
        for pair in case.pairs:
            assert pair.view_hint is None
            assert sg.resolve_view(pair, {}) == (None, None)


def test_choice_reads_the_volume_out_of_the_narrative():
    volumes = [sg.volume_cc(c.specs) for c in _choice_cases() if c.pairs]
    assert volumes == [375, 270, 435, 330, 300]


def test_choice_sided_volumes_land_on_the_right_sides():
    """'a 480cc in the smaller right breast and 390cc implant in the larger
    left breast' - the volume precedes its side, which the determiner-form
    side marker ('in the right') cannot reach."""
    case = [c for c in _choice_cases() if c.case_id == "case3"][0]
    assert (case.specs.left_cc, case.specs.right_cc) == (390.0, 480.0)
    assert sg.volume_cc(case.specs) == 435


@pytest.mark.parametrize("text,expected", [
    ("A 360cc implant was inserted in the larger left breast and a 420cc "
     "implant in the smaller right breast.", (360.0, 420.0)),
    ("She underwent augmentation with a 390cc implant in her smaller left "
     "breast and a 360cc implant in her larger right breast.", (390.0, 360.0)),
    # A side word with no volume in its own clause must not steal the next
    # one, and must not stop the clause after it from being read.
    ("This lady had asymmetric breasts with her left breast being slightly "
     "smaller. She had a 390cc implant in her left breast and a 360cc "
     "implant in her right breast.", (390.0, 360.0)),
])
def test_sided_breast_phrasing_assigns_volumes_per_side(text, expected):
    assert sg.parse_fill_volumes(text) == expected


@pytest.mark.parametrize("text,expected,why", [
    # camp 749 - the noun form reads the first, the determiner form the second.
    ("Motiva RSF round, smooth-wall implants were selected - 335cc for the left "
     "breast and 355cc for the right - to help achieve balance.",
     (335.0, 355.0), "camp 749"),
    # charlotte 33 - base read the sides the wrong way round.
    ("implanted with Mentor smooth round saline implants (350cc for the right "
     "breast, 360cc for the left)", (360.0, 350.0), "charlotte 33"),
    # colville 382 / tccs 12124 - 'for the <side>' with no noun at all.
    ("I chose to use two different Allergan Natrelle implants: a 365cc Style SSF "
     "for the right side and a 240cc Style SSM for the left.",
     (240.0, 365.0), "colville 382"),
    ("a 470cc extra full profile implant for the right and a 365cc full profile "
     "implant for the left.", (365.0, 470.0), "tccs 12124"),
])
def test_for_the_side_phrasing_assigns_volumes_per_side(text, expected, why):
    """Measured against every cached case of all 32 offline-reachable clinics:
    7 of 2426 change, all of them corrections. These four are the volume ones."""
    assert sg.parse_fill_volumes(text) == expected, why


def test_sided_breast_marker_does_not_reach_across_a_sentence_break():
    """The photo-position reading of a side word is why the segment is cut at
    the previous sentence: '275 cc implants ... on the right' must stay
    bilateral rather than becoming a one-sided volume."""
    text = ("She had 275 cc saline filled implants. She is shown here 3 years "
            "post operatively on the right.")
    assert sg.parse_fill_volumes(text) == (275.0, 275.0)


def test_choice_profile_is_recorded_only_where_the_clinic_prints_one():
    cases = {c.case_id: c for c in _choice_cases()}
    assert cases["case2"].specs.profile == "moderate"   # '270cc round moderate profile'
    # 'medium profile' (case 7 on the live page) is NOT in the captain's
    # 2026-08-19 profile vocabulary, so it stays undecoded rather than being
    # read as 'moderate'.
    assert cases["case4"].specs.profile is None
    assert "medium profile" in cases["case4"].specs.summary


def test_choice_placement_prose_is_not_read_as_a_documented_placement():
    """'placed under the muscle' is a description, not this clinic's charted
    placement value (see PLACEMENT_PATTERNS)."""
    case = [c for c in _choice_cases() if c.case_id == "case4"][0]
    assert "under the muscle" in case.specs.summary
    assert case.specs.placement is None
    assert case.specs.incision is None


def test_choice_drops_the_republished_duplicate_case():
    """Cases 16 and 17 carry identical narratives and identical image
    basenames; the page's own tell is that both reuse the lightbox id
    'lighttest16'. One patient under two case ids would put the same person
    in both halves of build_dataset.py's by-patient split."""
    cases = _choice_cases()
    dupes = [c for c in cases if any("duplicate case" in w for w in c.warnings)]
    assert len(dupes) == 1
    assert dupes[0].pairs == []
    assert "case5" in dupes[0].warnings[0]


@pytest.mark.parametrize("text,term", [
    ("Breast augmentation with a mastopexy to lift the breasts.", "mastopexy"),
    ("She had a mummy makeover with 350cc implants.", "mummy makeover"),
    ("Augmentation combined with a breast lift and 300cc implants.",
     "breast lift"),
    ("300cc implants with fat transfer to the upper pole.", "fat transfer"),
])
def test_choice_purity_screen_rejects_a_combined_narrative(text, term):
    assert sg.choice_screen_purity(text).lower() == term


@pytest.mark.parametrize("text", [
    "She wanted more volume without a breast lift, so 300cc implants were used.",
    "Augmentation with 375cc implants rather than a breast reduction.",
    "She did not want a mastopexy; 400cc implants were placed.",
])
def test_choice_purity_screen_allows_a_ruled_out_procedure(text):
    assert sg.choice_screen_purity(text) is None


def test_choice_purity_negation_is_scoped_to_its_own_clause():
    """A later 'without a lift' must not clear an earlier reported lift."""
    text = ("Breast augmentation with a mastopexy was performed. "
            "The scar was placed without a vertical component.")
    assert sg.choice_screen_purity(text) == "mastopexy"


def test_choice_every_published_case_is_pure_breast_augmentation():
    """Measured on the live listing: the clinic segregates its other
    procedures into sibling galleries, and no narrative in this one reports a
    combined procedure."""
    assert [c for c in _choice_cases() if any("not pure" in w for w in c.warnings)] == []


def test_choice_config_crops_the_before_after_caption_band():
    cfg = sg.CLINICS["choice"]
    assert cfg.kind == "choice"
    assert cfg.consent_ref == "choice-agreement-2026-08-25"
    # Band measured at 50-53px with gold text topping out at 53px over all 52
    # published composites; the crop must clear it with margin and still leave
    # a half above ingest.py's 400px floor.
    assert cfg.bottom_crop_px == 60
    assert 502 - cfg.bottom_crop_px >= 400
    assert 910 // 2 >= 400


def test_crop_bottom_trims_both_halves_by_the_same_rows():
    from PIL import Image
    import io

    src = Image.new("RGB", (910, 502), "white")
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    before, after = sg.split_composite_image(buf.getvalue())
    cropped = [sg.crop_bottom(half, 60) for half in (before, after)]
    sizes = {Image.open(io.BytesIO(c)).size for c in cropped}
    assert sizes == {(455, 442)}


def test_crop_bottom_error_names_the_image_height_it_exceeds():
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (100, 50), "white").save(buf, format="PNG")
    with pytest.raises(ValueError, match="exceeds image height"):
        sg.crop_bottom(buf.getvalue(), 50)


def test_the_four_burnt_in_mark_crops_stay_four_independent_mechanisms():
    """One clinic, one measured mark, one mechanism - and none shadowing another.

    Four collections invented a burnt-in-mark crop in parallel and two of them
    took a name the default branch had just used, so a naive union leaves a
    second definition that silently disables the pixel crop for the clinics
    carrying a one-sided watermark. Nothing fails when that happens: the halves
    simply keep the mark, and the model learns to read it instead of the
    anatomy. So the guard is behavioural - each mechanism must still trim the
    frame in its own way, and no clinic may ask for two of them.
    """
    from PIL import Image
    import io

    def png(w, h):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), "white").save(buf, format="PNG")
        return buf.getvalue()

    def size(data):
        with Image.open(io.BytesIO(data)) as im:
            return im.size

    composite = png(1000, 500)
    half, _ = sg.split_composite_image(composite)
    assert size(half) == (500, 500)

    # a) absolute pixel rows, after the split (ClinicConfig.bottom_crop_px).
    pixel = sg.crop_bottom(half, 60)
    # b) a fraction of the half's WIDTH, after the split (bottom_crop_frac).
    width_frac = sg.crop_bottom_frac(half, 0.10)
    # c) a fraction of the composite's HEIGHT, before the split
    #    (ImagePair.composite_bottom_frac).
    height_frac, _ = sg.split_composite_image(composite, bottom_frac=0.22)
    # d) a measured caption band plus the divider either side of the midpoint
    #    (ImagePair.crop_caption_band / seam_trim).
    band, _ = sg.split_composite_image(composite, bottom_crop=117, seam_trim=8)

    assert size(pixel) == (500, 440)
    assert size(width_frac) == (500, 450)
    assert size(height_frac) == (500, 390)
    assert size(band) == (492, 383)
    # Four mechanisms, four geometries: any one silently replaced by another
    # collapses this set.
    assert len({size(pixel), size(width_frac), size(height_frac), size(band)}) == 4

    assert [c.slug for c in sg.CLINICS.values()
            if c.bottom_crop_px and c.bottom_crop_frac] == []


def test_crop_bottom_is_a_no_op_for_every_other_clinic():
    """The crop is opt-in per clinic: only a measured mark earns one.

    The list is the union of every clinic with a measured burnt-in mark, so it
    grows only when someone measures one - never by default.
    """
    payload = b"not-an-image"
    assert sg.crop_bottom(payload, 0) is payload
    assert sorted(c.slug for c in sg.CLINICS.values() if c.bottom_crop_px) == [
        "camp", "choice", "roth", "tccs", "wny"]


# ---------------------------------------------------------------------------
# mwps (Mountain West Plastic Surgery): Influx Growthstack, stitched composites
# ---------------------------------------------------------------------------


def test_mwps_list_cases_enumerates_the_whole_listing():
    ids = sg.mwps_list_cases(load_fixture("mwps_listing.html"),
                             "/gallery/breast/breast-augmentation/")
    # The listing is unpaginated and inlines every case; the gallery publishes
    # no declared total of its own to reconcile against.
    assert len(ids) == 67
    assert ids[:3] == ["6", "10", "11"] and ids[-1] == "193"
    assert ids == sorted(ids, key=int)


def test_mwps_every_slide_is_its_own_before_after_composite():
    case = sg.mwps_parse_case(load_fixture("mwps_case_6.html"), "6", "x")
    assert [p.key for p in case.pairs] == ["6-01", "6-02"]
    for pair in case.pairs:
        assert pair.split_composite
        assert pair.before_url == pair.after_url
        assert pair.composite_bottom_frac == sg.MWPS_WATERMARK_CROP_BOTTOM_FRAC


def test_mwps_ignores_the_templates_before_after_slide_classes():
    """Case 162's three composites are classed before/after/before.

    They are leftovers of the un-stitched Influx layout and mean nothing in a
    'stitched' subcategory; reading them as a before/after tagging would pair
    two unrelated views and drop the third.
    """
    html = load_fixture("mwps_case_162.html")
    assert 'gallery-image-before' in html and 'gallery-image-after' in html
    case = sg.mwps_parse_case(html, "162", "x")
    assert [p.key for p in case.pairs] == ["162-01", "162-02", "162-03"]


def test_mwps_reads_the_older_terse_description():
    specs = sg.mwps_parse_case(load_fixture("mwps_case_6.html"), "6", "x").specs
    assert sg.volume_cc(specs) == 350
    assert specs.profile == "high"
    assert specs.shape == "round"
    assert specs.age == 25 and specs.gender == "Female"


def test_mwps_averages_a_sided_volume_from_prose():
    # '345cc (Right) and 385cc (Left)' - both sides published, so volume_cc is
    # the average the schema asks for, not whichever side was printed first.
    specs = sg.mwps_parse_case(load_fixture("mwps_case_18.html"), "18", "x").specs
    assert (specs.right_cc, specs.left_cc) == (345.0, 385.0)
    assert sg.volume_cc(specs) == 365


def test_mwps_reads_the_newer_labelled_chart():
    specs = sg.mwps_parse_case(load_fixture("mwps_case_172.html"), "172", "x").specs
    # A BARE number under a labelled implant-size field counts as cc.
    assert sg.volume_cc(specs) == 415
    # Height is published as plain inches, which height_to_cm cannot read.
    assert specs.height_cm == pytest.approx(170.2)
    assert specs.weight_kg == pytest.approx(63.5)
    assert specs.months_post_op == 12


def test_mwps_never_reads_a_bare_number_in_prose_as_a_volume():
    # Case 92: 'Smooth round 385 Implants placed submuscular' - no unit, and
    # no labelled implant-size field, so the volume stays unrecorded.
    specs = sg.mwps_parse_case(load_fixture("mwps_case_92.html"), "92", "x").specs
    assert sg.volume_cc(specs) is None
    assert specs.shape == "round"


def test_mwps_never_reads_placement_off_the_narrative():
    # Case 92's prose says 'placed submuscular' and case 172's says nothing;
    # neither Description is a chart, so placement/incision stay undocumented.
    for fixture, case_id in (("mwps_case_92.html", "92"),
                             ("mwps_case_172.html", "172")):
        specs = sg.mwps_parse_case(load_fixture(fixture), case_id, "x").specs
        assert specs.placement is None and specs.incision is None


@pytest.mark.parametrize("fixture,case_id,reason", [
    ("mwps_case_113.html", "113", "implant removal"),
])
def test_mwps_excludes_an_implant_exchange(fixture, case_id, reason):
    """A revision reads as an augmentation unless removed/replaced is caught:
    its 'before' is a patient who already has implants."""
    case = sg.mwps_parse_case(load_fixture(fixture), case_id, "x")
    assert case.pairs == []
    assert case.warnings == [
        f"excluded: not a pure breast augmentation ({reason})"]


@pytest.mark.parametrize("fixture,case_id", [
    ("mwps_case_168.html", "168"),   # '...cosmetic and RECONSTRUCTIVE surgery'
    ("mwps_case_188.html", "188"),   # '...an UPLIFTed confidence'
])
def test_mwps_marketing_boilerplate_does_not_exclude_a_pure_case(fixture, case_id):
    """The newer cases wrap the case in practice-marketing prose. Screening it
    with loose substrings would reject cases that are pure augmentations."""
    case = sg.mwps_parse_case(load_fixture(fixture), case_id, "x")
    assert case.warnings == []
    assert len(case.pairs) == 3


def test_mwps_marketing_prose_never_invents_a_brand_or_profile():
    """'her decision motivated by personal preference' + 'a full, balanced
    figure' used to parse as a Motiva implant at high profile - a fabricated
    training label out of a sentence naming no implant at all."""
    for fixture, case_id in (("mwps_case_188.html", "188"),
                             ("mwps_case_172.html", "172")):
        specs = sg.mwps_parse_case(load_fixture(fixture), case_id, "x").specs
        assert specs.brand == "unknown"
        assert specs.profile is None


def test_mwps_does_not_decode_an_unbranded_full_profile():
    """'shaped silicone implants' documents a shape and no profile. Bare
    'Full'/'Demi' only decode for a documented Motiva implant (the clinic
    names no manufacturer anywhere), so profile stays unrecorded."""
    specs = sg.mwps_parse_case(load_fixture("mwps_case_11.html"), "11", "x").specs
    assert specs.shape == "teardrop"
    assert specs.profile is None
    assert sg.volume_cc(specs) == 295


def test_brand_keyword_motiva_needs_a_word_boundary():
    specs = sg.CaseSpecs()
    sg.classify_brand_shape_profile(specs, "Her motivation was a fuller figure")
    assert specs.brand == "unknown"
    assert specs.profile is None
    specs = sg.CaseSpecs()
    sg.classify_brand_shape_profile(specs, "Motiva Ergonomix Full implants")
    assert specs.brand == "motiva" and specs.profile == "high"


def test_brand_keywords_other_than_motiva_stay_substring_matches():
    """harrington strips its inline <a> without a separator, publishing
    '...mammoplasty withSientrasmooth round silicone implants'. A word
    boundary there would silently drop a documented brand."""
    specs = sg.CaseSpecs()
    sg.classify_brand_shape_profile(specs, "mammoplasty withSientrasmooth round")
    assert specs.brand == "sientra"


def test_split_composite_crops_the_bottom_off_both_halves_equally():
    """mwps's watermark is centred ON the split seam, so an equal piece lands
    on each half; cropping one side only would leave a mark on one half of the
    pair, which is a label leak. The crop is applied to the whole composite
    before the split, so the halves stay dimension- and framing-matched."""
    import io

    from PIL import Image

    # The mark is scaled to the frame, so the crop is a fraction of height:
    # a fixed pixel count measured on one height leaves the logo's top behind
    # on every taller composite.
    for height, kept in ((499, 389), (568, 443), (644, 502)):
        buf = io.BytesIO()
        Image.new("RGB", (1500, height), (10, 20, 90)).save(buf, format="JPEG")
        before, after = sg.split_composite_image(buf.getvalue(),
                                                 bottom_frac=0.22)
        assert [Image.open(io.BytesIO(d)).size for d in (before, after)] == [
            (750, kept), (750, kept)]


def test_split_composite_without_a_crop_is_unchanged():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1000, 500), (10, 20, 90)).save(buf, format="JPEG")
    before, after = sg.split_composite_image(buf.getvalue())
    assert [Image.open(io.BytesIO(d)).size for d in (before, after)] == [
        (500, 500), (500, 500)]


def test_split_composite_refuses_a_crop_that_consumes_the_image():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1000, 80), (10, 20, 90)).save(buf, format="JPEG")
    with pytest.raises(ValueError, match="leaves nothing"):
        sg.split_composite_image(buf.getvalue(), bottom_frac=1.0)


@pytest.mark.parametrize("value,expected", [
    ("65", 165.1), ("62", 157.5), ("67 in", 170.2), ("", None),
    ("5'6\"", None),   # feet/inches is height_to_cm's job, not this one
    ("12", None),      # out of the schema's height_cm range
])
def test_inches_to_cm(value, expected):
    result = sg.inches_to_cm(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# dsm: Kadence gallery, one inline listing, per-photo before/after captions
# ---------------------------------------------------------------------------

DSM_URL = "https://www.dsmplasticsurgery.com/gallery/breast-augmentation/"


def dsm_cases():
    return {c.case_id: c
            for c in sg.dsm_parse_listing(load_fixture("dsm_listing_excerpt.html"),
                                          DSM_URL)}


def test_dsm_groups_photos_into_cases_by_patient_filter_class():
    cases = dsm_cases()
    assert sorted(cases) == ["01", "05", "09", "11", "12", "13", "58"]
    # Patient 1 publishes three views (six photos), the rest two.
    assert len(cases["01"].pairs) == 3
    assert len(cases["09"].pairs) == 2


def test_dsm_declared_patient_count_comes_from_the_gallery_filter_list():
    """The filter list is the gallery's own declaration of what it published,
    and is what a run reconciles its case count against."""
    assert sg.dsm_declared_patients(load_fixture("dsm_listing_excerpt.html")) == 7


def test_dsm_before_after_comes_from_the_caption_not_the_filename():
    """Every one of patient 58's six files is named '...-After-Photo-...';
    only the captions alternate BEFORE/AFTER. Reading the slug would label
    all six as afters and lose the whole case."""
    case = dsm_cases()["58"]
    assert len(case.pairs) == 3
    for pair in case.pairs:
        assert "After-Photo" in pair.before_url and "After-Photo" in pair.after_url
        assert pair.before_url != pair.after_url


def test_dsm_reads_the_full_size_href_not_a_thumbnail():
    pair = dsm_cases()["01"].pairs[0]
    assert pair.before_url.endswith("01-35958-1-Breast-Augmentation.jpg")
    assert "-300x300" not in pair.before_url


def test_dsm_view_is_unlabelled_except_where_the_filename_spells_it_out():
    """Patient 5 is the gallery's only page-documented view labelling; every
    other patient needs a visual-annotation pass and must not be guessed."""
    assert [p.view_hint for p in dsm_cases()["05"].pairs] == ["front", "side-left"]
    assert [p.view_hint for p in dsm_cases()["01"].pairs] == [None, None, None]
    assert [p.key for p in dsm_cases()["01"].pairs] == ["pair1", "pair2", "pair3"]


def test_dsm_resolves_only_the_page_documented_views():
    pairs = dsm_cases()["05"].pairs
    assert sg.resolve_view(pairs[0], {}) == ("front", None)
    assert sg.resolve_view(pairs[1], {}) == ("side-left", None)
    assert sg.resolve_view(dsm_cases()["01"].pairs[0], {}) == (None, None)


def test_dsm_parses_the_labelled_chart():
    specs = dsm_cases()["01"].specs
    assert specs.age == 37
    assert specs.height == "5’9"
    assert specs.height_cm == 175.3
    assert specs.profile == "moderate"
    assert sg.volume_cc(specs) == 405
    assert specs.summary == "Silicone moderate profile 405CC"


def test_dsm_cup_size_after_label_carries_the_implant_spec():
    """The clinic prints the implant description under two different labels;
    'Cup Size After:' never carries a cup size."""
    specs = dsm_cases()["13"].specs
    assert specs.summary == "Silicone High Profile-350cc"
    assert specs.profile == "high"
    assert sg.volume_cc(specs) == 350


def test_dsm_weight_without_a_documented_unit_stays_verbatim():
    """Patient 1's weight is the bare number '140'; patient 5's says '145 lbs'.
    Only the second is a measurement this pipeline can convert."""
    assert dsm_cases()["01"].specs.weight_kg is None
    assert dsm_cases()["01"].specs.fields["Patient Weight"] == "140"
    assert dsm_cases()["05"].specs.weight_kg == 65.8


def test_dsm_unparseable_implant_code_yields_a_volume_but_no_profile():
    """Patient 11 reads 'Silicone 410-375cc'. 410 sits where every sibling
    caption puts the profile, but it is not one - a manufacturer model code
    stays unparsed rather than being read as a profile or a second volume."""
    specs = dsm_cases()["11"].specs
    assert specs.profile is None
    assert specs.shape == "unknown" or specs.shape is None
    assert sg.volume_cc(specs) == 375


def test_dsm_case_without_a_published_chart_still_yields_pairs():
    case = dsm_cases()["12"]
    assert len(case.pairs) == 2
    assert case.specs.summary == ""
    assert sg.volume_cc(case.specs) is None


def test_dsm_non_alternating_couple_is_dropped_with_a_warning():
    """Photos come in BEFORE, AFTER couples; anything else is reported rather
    than paired up by position."""
    html = (
        '<div class="kt-gallery-item patient-3">'
        '<a href="https://x.test/a.jpg" data-size="900x600"></a>'
        '<div class="kt-gallery-caption-text"><b>BEFORE PHOTO</b></div></div>'
        '<div class="kt-gallery-item patient-3">'
        '<a href="https://x.test/b.jpg" data-size="900x600"></a>'
        '<div class="kt-gallery-caption-text"><b>BEFORE PHOTO</b></div></div>'
    )
    cases = sg.dsm_parse_listing(html, DSM_URL)
    assert cases == []


def test_dsm_odd_photo_count_warns_and_keeps_the_complete_couples():
    html = "".join(
        f'<div class="kt-gallery-item patient-4">'
        f'<a href="https://x.test/{i}.jpg" data-size="900x600"></a>'
        f'<div class="kt-gallery-caption-text"><b>{m} PHOTO</b></div></div>'
        for i, m in enumerate(["BEFORE", "AFTER", "BEFORE"]))
    case = sg.dsm_parse_listing(html, DSM_URL)[0]
    assert len(case.pairs) == 1
    assert any("odd photo count" in w for w in case.warnings)


# ---------------------------------------------------------------------------
# page1 (Page 1 Solutions): markup contract, spec layouts, purity screen
# ---------------------------------------------------------------------------

PSIW_GALLERY = "https://www.plasticsurgerynow.com/gallery/breast-procedures/augmentation/"


@pytest.fixture(scope="module")
def psiw_cases():
    html = load_fixture("page1_psiw_listing.html")
    return {c.case_id: c for c in sg.page1_parse_listing(html, PSIW_GALLERY)}


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
    assert sg.page1_parse_volumes(
        "Implant size: Left: 375cc Right: 350cc "
        "Cup Size: Left: 340 Right: 300") == (375, 350)
    # With no implant-size label at all, a bare sided number is not a volume.
    assert sg.page1_parse_volumes("Cup Size: Left: 340 Right: 300") == (None, None)


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
    sg.page1_parse_details(text, specs)
    assert specs.profile == profile


def test_page1_natrelle_model_code_does_not_decode_to_a_profile():
    """'SSM'/'SRM' encode cc and profile but stay unparseable, per the
    drkolker 'Mini Motiva' precedent."""
    specs = sg.CaseSpecs()
    sg.page1_parse_details("67 year-old 445cc Natrelle SRM", specs)
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
    assert sg.page1_combined_procedure("breast-augmentation") is None
    assert sg.page1_combined_procedure(
        "breast augmentation with 255cc implants and a tummy tuck"
    ) == "abdominoplasty"


def test_page1_augmentation_scar_is_not_a_combined_procedure():
    """'breast augmentation scar' (case 32) describes the photo, not a second
    procedure."""
    assert sg.page1_combined_procedure(
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
    sg.page1_parse_details(
        "35 year old 7 weeks status post subpectoral breast augmentation "
        "Allerghan 255cc moderate profile plus implants.", specs)
    assert specs.placement is None
    assert specs.incision is None
    assert specs.profile == "moderate-plus"


# ---------------------------------------------------------------------------
# swan parser (The Swan Center; 2026-08-25 prospected batch)
#
# Etna asset naming on a self-hosted WordPress plugin. The images are the etna
# family; the PAGE is not - a structured '.attributes-list' chart, a published
# procedures list, and a public REST route for enumeration. The tests below pin
# each of those three differences, because each is a place where reusing the
# etna parser wholesale would have failed silently.
# ---------------------------------------------------------------------------

SWAN_GALLERY = "/gallery/breast/breast-augmentation/"


def swan_case(case_id: str):
    return sg.swan_parse_case(
        load_fixture(f"swan_case_{case_id}.html"), case_id, "x")


# -- enumeration: the gallery states its own total --------------------------


def test_swan_listing_declares_its_own_total_and_load_more_term():
    """'Showing 12 of 90 Cases' is the reconciliation target for the walk."""
    listing = load_fixture("swan_listing.html")
    assert sg.swan_declared_total(listing) == 90
    assert sg.swan_rest_term(listing) == "573"
    assert len(sg.swan_list_case_ids(listing, SWAN_GALLERY)) == 12


def test_swan_rest_page_yields_ids_and_restates_the_gallery_total():
    page = sg.swan_rest_page(load_fixture("swan_cases_page2.json"), SWAN_GALLERY)
    assert len(page["ids"]) == 12
    assert page["ids"][0] == "17760"
    assert (page["loaded"], page["total"], page["more"]) == (24, 90, True)


def test_swan_rest_page_reports_the_end_of_the_walk():
    """'more': false is what stops the pagination; a short page does not."""
    payload = json.dumps({"html": "", "loaded": 90, "total": 90, "more": False})
    page = sg.swan_rest_page(payload, SWAN_GALLERY)
    assert page["ids"] == []
    assert page["more"] is False


def test_swan_missing_load_more_control_yields_no_term():
    assert sg.swan_rest_term('<div id="eii-gallery-footer"></div>') is None


def test_swan_declared_total_absent_is_reported_as_none():
    assert sg.swan_declared_total("<html><body>no counter</body></html>") is None


# -- purity: screened from the case text, never the filename slug ------------


def test_swan_pure_augmentation_case_is_kept():
    case = swan_case("17094")
    assert case.warnings == []
    assert len(case.pairs) == 2


def test_swan_combined_case_excluded_by_its_own_procedures_list():
    """The structured screen the etna clinics do not publish.

    There is Etna asset naming here, so the etna parser's filename-slug screen
    would read 'breast-augmentation-...' and keep this case. The page's own
    procedures list says otherwise, and that is the case text.
    """
    html = load_fixture("swan_case_17094.html").replace(
        "</ul>",
        '<li><a class="case-category-link" href="#">Breast Lift</a></li></ul>')
    case = sg.swan_parse_case(html, "17094", "x")
    assert case.pairs == []
    assert any("not pure breast augmentation" in w and "breast lift" in w
               for w in case.warnings)


def test_swan_case_with_no_procedures_list_is_excluded_not_assumed_pure():
    html = re.sub(r'<ul class="eii-gallery-details-procedures-list".*?</ul>', "",
                  load_fixture("swan_case_17094.html"), flags=re.S)
    case = sg.swan_parse_case(html, "17094", "x")
    assert case.pairs == []
    assert any("no procedures list" in w for w in case.warnings)


def test_swan_narrative_backstop_catches_a_combined_procedure():
    """A case can be filed under one procedure and described as another."""
    html = load_fixture("swan_case_17094.html").replace(
        "39-year old shown with",
        "39-year old shown after augmentation-mastopexy with")
    case = sg.swan_parse_case(html, "17094", "x")
    assert case.pairs == []
    assert any("combined procedure" in w for w in case.warnings)


@pytest.mark.parametrize("phrase", [
    "breast lift", "mastopexy", "mommy makeover", "breast reduction",
    "implant exchange", "explant",
])
def test_swan_combined_vocabulary_matches_the_phrases_that_report_surgery(phrase):
    assert sg.SWAN_COMBINED_RE.search(f"patient shown after {phrase} results")


@pytest.mark.parametrize("phrase", [
    "the implants lift the breast tissue",
    "a fuller, lifted appearance",
])
def test_swan_combined_vocabulary_does_not_fire_on_prose_about_shape(phrase):
    """'lift' alone describes what an implant does as often as a mastopexy."""
    assert sg.SWAN_COMBINED_RE.search(phrase) is None


# -- the structured attributes chart -----------------------------------------


def test_swan_full_chart_populates_every_documented_field():
    case = swan_case("17094")
    specs = case.specs
    assert specs.age == 39
    assert specs.gender == "Female"
    assert sg.volume_cc(specs) == 325
    assert specs.profile == "moderate-plus"
    assert specs.shape == "round"
    assert specs.placement == "dual-plane"
    assert specs.fields["Cup Size Before"] == "AA"
    assert specs.fields["Implant Contents"] == "Silicone"


def test_swan_height_and_weight_have_no_documented_unit_and_do_not_convert():
    """The chart publishes bare '62' and '135'.

    Reading them as inches and pounds is an inference, and a wrong frame metric
    is worse than a missing one (the sanantonio precedent). The verbatim values
    still survive into the notes.
    """
    specs = swan_case("17094").specs
    assert specs.height == "62"
    assert specs.height_cm is None
    assert specs.weight_lbs is None
    assert specs.weight_kg is None
    assert "Weight Before: 135" in sg.build_notes(specs, None)


def test_swan_bare_number_in_a_labelled_implant_size_field_reads_as_cc():
    """The 2026-08-15 units ruling: labelled field yes, free prose no."""
    specs = swan_case("17094").specs
    assert (specs.left_cc, specs.right_cc) == (325.0, 325.0)


def test_swan_asymmetric_volumes_average_and_say_so_in_the_notes():
    case = swan_case("25826")
    assert (case.specs.left_cc, case.specs.right_cc) == (375.0, 400.0)
    assert sg.volume_cc(case.specs) == 388
    assert "asymmetric volumes" in sg.build_notes(case.specs, None)


def test_swan_incision_comes_from_the_charts_own_field():
    """Only 16 of the 90 cases publish 'Breast Incision Type'."""
    case = swan_case("25810")
    assert case.specs.fields["Breast Incision Type"] == "Inframammary"
    assert case.specs.incision == "inframammary"


def test_swan_incision_is_unset_when_the_chart_omits_the_field():
    assert swan_case("17094").specs.incision is None


def test_swan_sparse_chart_leaves_undocumented_fields_unset():
    """Absent is absent: no profile, no placement, no shape - never defaulted."""
    case = swan_case("26713")
    specs = case.specs
    assert specs.profile is None
    assert specs.placement is None
    assert specs.shape is None
    assert sg.volume_cc(specs) == 325
    assert case.warnings == []


# -- profile: a LABELLED field decodes a bare projection word ----------------


def test_swan_labelled_profile_field_decodes_a_bare_word():
    """'High' alone is not a profile in prose; in 'Implant Profile' it is."""
    assert sg.PROFILE_PATTERNS[2][0].search("High") is None
    assert swan_case("25826").specs.profile == "high"


@pytest.mark.parametrize("published,expected", [
    ("Moderate", "moderate"),
    ("Moderate Plus", "moderate-plus"),
    ("High", "high"),
    # Captain's 2026-08-19 ruling: every one of these means extra-high.
    ("Ultra High Profile", "extra-high"),
    ("UHP", "extra-high"),
    ("VHP", "extra-high"),
    ("Extra-Full", "extra-high"),
    ("Extra High Range", "extra-high"),
    ("Corse", "extra-high"),
])
def test_swan_profile_vocabulary(published, expected):
    html = load_fixture("swan_case_17094.html").replace(
        ">Moderate Plus<", f">{published}<")
    assert sg.swan_parse_case(html, "17094", "x").specs.profile == expected


def test_swan_mentor_xtra_is_a_product_line_and_does_not_decode():
    """Manufacturer model codes stay unparseable (captain's 2026-08-19 ruling).

    Read against case 26713, whose narrative is the site's boilerplate, so the
    labelled field is the only profile evidence on the page and an unrecognised
    value leaves the case with no profile at all.
    """
    html = load_fixture("swan_case_26713.html").replace(
        "</div>\n</div>",
        '</div>\n<div class="attribute"><div class="attribute-name">Implant '
        'Profile</div><div class="attribute-value">Xtra</div></div>\n</div>')
    case = sg.swan_parse_case(html, "26713", "x")
    assert case.specs.fields["Implant Profile"] == "Xtra"
    assert case.specs.profile is None
    assert any("not in the profile vocabulary" in w for w in case.warnings)


def test_swan_a_narrative_profile_survives_an_unreadable_chart_value():
    """Case 17094's prose says 'Moderate Plus Profile' in its own words.

    That is a documented statement, so an unparseable chart value withholds the
    chart's evidence without discarding the narrative's.
    """
    html = load_fixture("swan_case_17094.html").replace(
        ">Moderate Plus<", ">Xtra<")
    case = sg.swan_parse_case(html, "17094", "x")
    assert case.specs.profile == "moderate-plus"
    assert any("not in the profile vocabulary" in w for w in case.warnings)


# -- description block --------------------------------------------------------


def test_swan_contact_us_boilerplate_is_not_a_clinic_description():
    """21 of the 90 cases publish this in '.case-description'.

    Letting it through writes a marketing sentence into those pairs' notes as
    though the surgeon had described the case.
    """
    case = swan_case("26713")
    assert case.specs.summary == ""
    assert "Contact us for more details" not in sg.build_notes(case.specs, None)


def test_swan_real_narrative_is_kept():
    assert swan_case("17094").specs.summary.startswith("39-year old shown with")


@pytest.mark.parametrize("phrase,months", [
    ("shown 6 months post-op with", 6.0),
    ("shown 1-year post-op with", 12.0),
    ("shown 6 weeks post-op with", 1.4),
    ("shown 6 months post-operative, with", 6.0),
])
def test_swan_post_op_interval_reads_in_the_unit_the_clinic_published(phrase, months):
    html = load_fixture("swan_case_17094.html").replace("shown with", phrase)
    assert sg.swan_parse_case(html, "17094", "x").specs.months_post_op == months


def test_swan_no_post_op_phrase_leaves_months_unset():
    assert swan_case("17094").specs.months_post_op is None


# -- images -------------------------------------------------------------------


def test_swan_images_are_split_composites_with_positional_view_names():
    """Every case in this gallery publishes 'view-N', which documents nothing.

    So every pair needs the annotation pass; resolve_view must refuse to guess.
    """
    case = swan_case("17094")
    assert [p.key for p in case.pairs] == ["view-1", "view-2"]
    for pair in case.pairs:
        assert pair.split_composite
        assert pair.before_url == pair.after_url
        assert pair.view_hint is None
        assert sg.resolve_view(pair, {}) == (None, None)


def test_swan_third_view_is_collected_when_the_case_publishes_one():
    assert [p.key for p in swan_case("26713").pairs] == [
        "view-1", "view-2", "view-3"]


def test_swan_annotation_supplies_the_view():
    case = swan_case("17094")
    annotations = {"pairs": {"view-1": {"view": "front"},
                             "view-2": {"view": "side-left"}}}
    assert [sg.resolve_view(p, annotations)[0] for p in case.pairs] == [
        "front", "side-left"]


def test_swan_populated_thumbnail_caption_outranks_a_visual_call():
    """No case measured publishes one, but the slot exists in the markup."""
    html = load_fixture("swan_case_17094.html").replace(
        '<span class="case-view-lower"></span>',
        '<span class="case-view-lower">Left Oblique</span>', 1)
    case = sg.swan_parse_case(html, "17094", "x")
    assert case.pairs[0].view_hint == "oblique-left"
    assert sg.resolve_view(case.pairs[0], {}) == ("oblique-left", None)


def test_swan_each_view_emitted_once_despite_repeated_markup():
    """The focus pane and the thumbnail strip publish the same photograph."""
    html = load_fixture("swan_case_17094.html")
    doubled = html + html
    assert [p.key for p in sg.swan_parse_case(doubled, "17094", "x").pairs] == [
        "view-1", "view-2"]


def test_swan_another_cases_images_on_the_page_are_not_collected():
    html = load_fixture("swan_case_17094.html").replace(
        "</body>",
        '<img src="https://www.swancenteratlanta.com/wp-content/uploads/2026/06/'
        'breast-augmentation-99999-view-1-detail.jpg"/></body>')
    case = sg.swan_parse_case(html, "17094", "x")
    assert all("17094" in p.before_url for p in case.pairs)


# -- emitted metadata ---------------------------------------------------------


def test_swan_meta_carries_view_volume_profile_and_a_clinic_consent_ref():
    case = swan_case("17094")
    meta = sg.build_meta(
        "swan-17094-front", "front", case.specs, {}, {},
        "visual inspection of downloaded images",
        sg.CLINICS["swan"].consent_ref)
    assert meta["view"] == "front"
    assert meta["volume_cc"] == 325
    assert meta["profile"] == "moderate-plus"
    assert meta["consent_ref"] == "swan-agreement-2026-08-25"
    assert "swan" in meta["consent_ref"]


def test_swan_meta_omits_a_profile_the_clinic_did_not_publish():
    case = swan_case("26713")
    meta = sg.build_meta("swan-26713-front", "front", case.specs, {}, {}, None,
                         sg.CLINICS["swan"].consent_ref)
    assert "profile" not in meta
    assert meta["shape"] == "unknown"
    assert meta["volume_cc"] == 325


# ---------------------------------------------------------------------------
# tcclinic (Toronto Cosmetic Clinic; bespoke WordPress / Divi)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tcclinic_cases():
    return sg.tcclinic_parse_listing(
        load_fixture("tcclinic_listing.html"),
        "https://www.tcclinic.com/surgical/breast-augmentation/before-after-photos/")


def test_tcclinic_reads_both_photo_module_shapes(tcclinic_cases):
    """The gallery publishes its photos two ways and a parser must read both.

    4 of the 26 cases use a Divi `et_pb_gallery` whose composites are ordinary
    `<a href>`s; the other 22 use an `et_pb_slider` whose images exist ONLY as
    `background-image` rules in the page's inline CSS. Reading markup alone
    finds 12 of the 65 published composites.
    """
    by_id = {c.case_id: c for c in tcclinic_cases}
    assert [p.key for p in by_id["67436"].pairs] == ["01", "02", "03"]      # gallery
    assert [p.key for p in by_id["patient-11"].pairs] == ["1101", "1102", "1103"]  # slider
    assert all(p.before_url.startswith("https://www.tcclinic.com/")
               for c in tcclinic_cases for p in c.pairs)


def test_tcclinic_case_id_is_the_asset_folder_not_the_displayed_number(tcclinic_cases):
    """'Patient 25' publishes out of `patient-03`.

    The displayed number is a running position on the page, so keying cases by
    it would renumber every pair the moment the clinic reorders the gallery.
    """
    by_id = {c.case_id: c for c in tcclinic_cases}
    assert by_id["patient-03"].specs.fields["Case"] == "Patient 25"
    assert by_id["patient-01"].specs.fields["Case"] == "Patient 4"


def test_tcclinic_every_case_carries_volume_and_profile(tcclinic_cases):
    for case in tcclinic_cases:
        assert sg.volume_cc(case.specs) is not None
        assert case.specs.profile is not None


def test_tcclinic_reads_asymmetric_left_right_volumes(tcclinic_cases):
    """'Left: 400cc / Right: 425cc' - both sides, then the schema's average."""
    case = next(c for c in tcclinic_cases if c.case_id == "patient-01")
    assert (case.specs.left_cc, case.specs.right_cc) == (400.0, 425.0)
    assert sg.volume_cc(case.specs) == 412


@pytest.mark.parametrize("value,expected", [
    ("Left: 450 cc / Right: 450cc", (450.0, 450.0)),
    ("Left: 400cc / Right: 425cc", (400.0, 425.0)),
    ("350cc", (350.0, 350.0)),
])
def test_tcclinic_implant_volumes(value, expected):
    assert sg.tcclinic_implant_volumes(value) == expected


def test_tcclinic_moderate_plus_shorthand_decodes(tcclinic_cases):
    """This clinic writes moderate-plus as 'Moderate +' on its chart.

    Decoded locally rather than in the shared PROFILE_PATTERNS: curtsinger
    publishes 'moderate + xtra', Mentor's product line, which the captain's
    2026-08-19 ruling says does NOT decode.
    """
    case = next(c for c in tcclinic_cases if c.case_id == "patient-11")
    assert case.specs.fields["Implant Profile"] == "Moderate +"
    assert case.specs.profile == "moderate-plus"
    assert not sg.PROFILE_PATTERNS[1][0].search("moderate + xtra")


def test_tcclinic_chart_placement_and_incision(tcclinic_cases):
    by_id = {c.case_id: c for c in tcclinic_cases}
    assert by_id["67436"].specs.placement == "submuscular"
    assert by_id["67436"].specs.incision == "periareolar"      # 'Peri Areola'
    assert by_id["66963"].specs.placement == "subglandular"
    assert by_id["66963"].specs.incision == "inframammary"     # 'Inframmary'


def test_tcclinic_photo_taken_converts_weeks_to_months(tcclinic_cases):
    """'*Photo Taken 6 Weeks after Surgery' - a unit conversion, not a guess;
    the clinic's own wording survives in the chart fields."""
    case = next(c for c in tcclinic_cases if c.case_id == "66963")
    assert case.specs.months_post_op == 1.38
    assert case.specs.fields["Photo Taken"] == "Photo Taken 6 Weeks after Surgery"
    assert next(c for c in tcclinic_cases
                if c.case_id == "67436").specs.months_post_op == 6.0


def test_tcclinic_pairs_are_watermark_cropped_composites(tcclinic_cases):
    for case in tcclinic_cases:
        for pair in case.pairs:
            assert pair.split_composite and pair.crop_caption_band
            assert pair.seam_trim == sg.TCCLINIC_SEAM_TRIM
            assert pair.before_url == pair.after_url
            assert pair.view_hint is None      # the '-01' index is positional


def test_tcclinic_chart_without_photos_is_reported_not_dropped():
    html = ('<div class="et_pb_toggle"><h5 class="et_pb_toggle_title">Patient 9</h5>'
            '<div class="et_pb_toggle_content"><table><tr><td>Implant Size:</td>'
            '<td>350cc</td></tr></table></div></div>')
    case, = sg.tcclinic_parse_listing(html, "x")
    assert case.pairs == []
    assert case.warnings == ["'Patient 9': chart published with no photos"]


# ---------------------------------------------------------------------------
# Caption-band watermark cropping
# ---------------------------------------------------------------------------


def _banded_composite(width=1200, height=571, band_top=454, badge_top=410,
                      badge_radius=55):
    """A composite shaped like tcclinic's: two photo halves, a white caption
    band across the bottom, and a dark logo badge centred on the seam that
    rises out of the band into the photo."""
    import io

    import numpy as np
    from PIL import Image, ImageDraw

    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[:, :width // 2] = (200, 120, 110)     # before half
    arr[:, width // 2:] = (110, 120, 200)     # after half
    arr[band_top:, :] = 255
    img = Image.fromarray(arr)
    mid = width // 2
    ImageDraw.Draw(img).ellipse(
        [mid - badge_radius, badge_top, mid + badge_radius,
         badge_top + 2 * badge_radius], outline=(35, 31, 32), width=4)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_caption_band_crop_removes_the_band_and_the_badge_above_it():
    """The band height alone is the wrong answer.

    tcclinic's badge straddles the seam and rises 44px out of the band into the
    frame, so cropping only the band leaves the watermark on BOTH halves.
    """
    import io

    import numpy as np
    from PIL import Image

    data = _banded_composite()
    crop = sg.caption_band_crop(data)
    assert crop > 571 - 454          # more than the band alone
    with Image.open(io.BytesIO(data)) as im:
        kept = np.asarray(im.convert("RGB")).astype(int)[: 571 - crop]
    window = kept[:, 1200 // 2 - sg.LOGO_HALF_WIDTH: 1200 // 2 + sg.LOGO_HALF_WIDTH]
    assert not ((window.max(axis=2) < sg.LOGO_VALUE)
                & (window.max(axis=2) - window.min(axis=2) < sg.LOGO_NEUTRAL)).any()


def test_caption_band_crop_is_zero_without_a_band():
    """Same gallery, other image family: 835x455 with no band at all. A
    detector that always trims would silently shrink 53 of 65 composites."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (835, 455), (180, 140, 130)).save(buf, format="JPEG")
    assert sg.caption_band_crop(buf.getvalue()) == 0


def test_split_composite_image_crops_and_trims_symmetrically():
    import io

    from PIL import Image

    data = _banded_composite()
    before_data, after_data = sg.split_composite_image(
        data, bottom_crop=sg.caption_band_crop(data), seam_trim=8)
    before, after = (Image.open(io.BytesIO(b)) for b in (before_data, after_data))
    # Both halves lose exactly the same rows and columns: an unequal crop on a
    # before/after pair is a label leak.
    assert before.size == after.size
    assert before.size[0] == 1200 // 2 - 8
    assert before.size[1] == 571 - sg.caption_band_crop(data)
    assert before.convert("RGB").getpixel((296, 205))[0] > 150
    assert after.convert("RGB").getpixel((296, 205))[2] > 150


def test_split_composite_image_rejects_an_impossible_crop():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (900, 450)).save(buf, format="JPEG")
    with pytest.raises(ValueError, match="exceeds image height"):
        sg.split_composite_image(buf.getvalue(), bottom_crop=450)
    with pytest.raises(ValueError, match="seam trim"):
        sg.split_composite_image(buf.getvalue(), seam_trim=450)


def test_split_composite_image_default_path_is_unchanged_for_odd_widths():
    """Untrimmed, an odd-width composite still gives an after one pixel wider.

    Every clinic already in the corpus was emitted through this path, and
    emit_corpus.py reads a byte difference as a clash rather than a merge, so
    adding the crop options must not shift it.
    """
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (901, 450), (170, 130, 120)).save(buf, format="JPEG")
    before, after = (Image.open(io.BytesIO(b))
                     for b in sg.split_composite_image(buf.getvalue()))
    assert (before.size, after.size) == ((450, 450), (451, 450))


# ---------------------------------------------------------------------------
# wyten: procedure purity read off the slide TITLE, and the 2x2 grid gutter
# ---------------------------------------------------------------------------


def _wyten_cases():
    return sg.wyten_parse_listing(load_fixture("wyten_listing.html"), "x")


def test_wyten_carries_only_pure_implant_augmentation():
    """Every published slide is accounted for: 15 = 6 carried + 9 excluded."""
    cases = _wyten_cases()
    carried = [c for c in cases if c.pairs]
    excluded = [c for c in cases if not c.pairs]
    assert len(cases) == 15
    assert len(carried) == 6
    assert len(excluded) == 9
    # An excluded slide is still returned, with a reason, so a run cannot
    # quietly shrink the gallery to the cases it liked.
    assert all(len(c.warnings) == 1 for c in excluded)
    assert [c.case_id for c in carried] == [
        "ba-imp-250cc-6mo-2",
        "ba-imp-335cc-2mo",
        "ba-imp-350cc-6mo",
        "ba-imp-375cc-12mo",
        "bai-350cc-smooth-moderate-plus-profile-9mo",
        "bai-375cc-smooth-round-high-profile-24mo",
    ]


def test_wyten_screens_the_title_not_the_filename():
    """The filename prefix does not separate pure from combined at this clinic.

    'bai_R275cc_L300cc_5mo_1.jpg' and 'bai_375cc_Smooth_Round_High_Profile...'
    share the `bai_` prefix; the first is an abdominoplasty case and the second
    is pure. Reading the slug is what let 86 combined cases through at the Etna
    clinics, and it would take one straight into the corpus here.
    """
    by_id = {c.case_id: c for c in _wyten_cases()}
    assert by_id["bai-375cc-smooth-round-high-profile-24mo"].pairs
    combined = by_id["bai-r275cc-l300cc-5mo-1"]
    assert not combined.pairs
    assert "combined-procedure (abdominoplasty)" in combined.warnings[0]
    # ...and the same prefix collision the other way: `mp_bai_` is a mastopexy.
    assert "combined-procedure (mastopexy)" in by_id["mp-bai-300cc-9mo"].warnings[0]


@pytest.mark.parametrize("title,expected", [
    ("Breast Augmentation with Breast Implants", None),
    ("Breast Augmentation with Fat Grafting", "fat-grafting-not-implants"),
    ("Fat Transfer to Breasts", "fat-grafting-not-implants"),
    ("Breast Implants & Abdominoplasty", "combined-procedure"),
    ("Mastopexy & Breast Implants", "combined-procedure"),
    ("Breast Augmentation -Remove and Replace implants",
     "revision-not-primary-augmentation"),
    ("Breast Augmentation - Remove implants - Mastopexy",
     "revision-not-primary-augmentation"),
    # Fails CLOSED: a title the allow-list does not recognise is excluded even
    # though it names no disqualifying procedure.
    ("Breast Surgery", "not-implant-augmentation"),
    # And a disqualifier after the pure phrase does not ride in on it.
    ("Breast Augmentation with Breast Implants & Abdominoplasty",
     "combined-procedure"),
])
def test_wyten_rejection_reason(title, expected):
    reason = sg.wyten_rejection_reason(title)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and reason.startswith(expected)


def test_wyten_specs_come_from_the_caption():
    by_id = {c.case_id: c for c in _wyten_cases()}
    high = by_id["bai-375cc-smooth-round-high-profile-24mo"].specs
    assert sg.volume_cc(high) == 375
    assert high.profile == "high" and high.shape == "round"
    assert high.months_post_op == 24.0
    modplus = by_id["bai-350cc-smooth-moderate-plus-profile-9mo"].specs
    assert modplus.profile == "moderate-plus"
    assert sg.volume_cc(modplus) == 350
    # 'Moderate Plus' must not degrade to plain 'moderate'.
    assert modplus.profile != "moderate"
    # An undocumented profile stays undocumented rather than defaulting.
    assert by_id["ba-imp-250cc-6mo-2"].specs.profile is None
    # Motiva's product names are not a profile: 'SilkSurface Egronomix Round'
    # documents a shape and nothing about projection, and Ergonomix is not in
    # BRAND_KEYWORDS, so the Motiva Mini/Demi/Full/Corse decode never fires.
    ergonomix = by_id["ba-imp-335cc-2mo"].specs
    assert ergonomix.shape == "round"
    assert ergonomix.profile is None
    assert ergonomix.brand == "unknown"


def test_wyten_pairs_are_2x2_grid_cells_needing_a_laterality_annotation():
    case = {c.case_id: c for c in _wyten_cases()}["ba-imp-350cc-6mo"]
    front, side = case.pairs
    assert [p.key for p in case.pairs] == ["front", "side"]
    for pair in (front, side):
        assert pair.grid_shape == (2, 2)
        assert pair.before_url == pair.after_url  # one fetch yields both halves
    assert front.before_cell == (0, 0) and front.after_cell == (0, 1)
    assert side.before_cell == (1, 0) and side.after_cell == (1, 1)
    # Front needs nothing; the lateral is not labelled by the clinic anywhere,
    # so it reaches a view only through an annotation - never a guess.
    assert sg.resolve_view(front, {}) == ("front", None)
    assert sg.resolve_view(side, {}) == (None, None)
    view, source = sg.resolve_view(side, {"laterality": "left"})
    assert view == "side-left" and source is not None


def test_crop_grid_cell_gutter_only_trims_interior_seams():
    """The divider strip sits between the panels, never at the outer edge."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    import io

    img = Image.new("RGB", (100, 60), "black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    data = buf.getvalue()

    plain = Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 2, (0, 0))))
    assert plain.size == (50, 30)
    # Top-left cell touches the vertical seam on its right and the horizontal
    # seam on its bottom, so it loses 8px on each of those two sides only.
    tl = Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 2, (0, 0), 8)))
    br = Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 2, (1, 1), 8)))
    assert tl.size == (42, 22) == br.size
    # Every cell in the grid keeps the same dimensions, which is what keeps the
    # two halves of a pair matched.
    sizes = {Image.open(io.BytesIO(sg.crop_grid_cell(data, 2, 2, (r, c), 8))).size
             for r in (0, 1) for c in (0, 1)}
    assert sizes == {(42, 22)}
    # A 1x2 grid has no horizontal seam, so height is untouched.
    wide = Image.open(io.BytesIO(sg.crop_grid_cell(data, 1, 2, (0, 0), 8)))
    assert wide.size == (42, 60)


def test_wyten_config_is_registered_and_traceable_to_its_consent():
    cfg = sg.CLINICS["wyten"]
    assert cfg.kind == "wyten"
    assert cfg.base_url == "https://drrebeccawyten.com.au"
    assert cfg.grid_gutter_px == 8
    # Section 6 of the consent allows written revocation with 30 business days
    # to remove, which only works if a pair names the form that covers it.
    assert cfg.consent_ref == "wyten-consent-2026-08-25"


# ---------------------------------------------------------------------------
# bayside: narrative specs in ml, MP/HP profiles, text-only purity screen
# ---------------------------------------------------------------------------


def bayside_case(fixture: str, case_id: str = "case-x"):
    return sg.bayside_parse_case(load_fixture(fixture), case_id, "x")


def test_bayside_listing_lists_every_case_once():
    ids = sg.bayside_list_cases(load_fixture("bayside_listing_forties.html"))
    assert len(ids) == 16
    assert len(set(ids)) == 16
    assert "breast-augmentation-case-50-age-40" in ids


def test_bayside_parses_three_labelled_before_after_pairs():
    case = bayside_case("bayside_case_three_views.html")
    assert [p.key for p in case.pairs] == ["img1-2", "img3-4", "img5-6"]
    # before/after come from div.before / div.after, never from filename order.
    assert all(p.before_url != p.after_url for p in case.pairs)
    assert case.pairs[0].before_url.endswith("Photos-2012-11-November-061.jpg")
    assert case.pairs[0].after_url.endswith("Photos-2015-3-March-026.jpg")
    # The page documents no view; that comes from the annotation file.
    assert all(p.view_hint is None for p in case.pairs)


def test_bayside_case_with_only_two_published_views():
    case = bayside_case("bayside_case_two_views.html")
    assert [p.key for p in case.pairs] == ["img1-2", "img3-4"]
    assert not case.warnings


def test_bayside_strips_wordpress_size_suffix_to_reach_the_original():
    case = bayside_case("bayside_case_two_views.html")
    # The page serves -300x225 derivatives, all below ingest.py's 400px floor.
    assert all("300x225" not in p.before_url for p in case.pairs)
    assert case.pairs[0].before_url.endswith("/2014/06/737.jpg")


@pytest.mark.parametrize("url,expected", [
    ("https://x/a/737-300x225.jpg", "https://x/a/737.jpg"),
    ("https://x/a/Photos-2012-11-November-061.jpg",
     "https://x/a/Photos-2012-11-November-061.jpg"),
    # A dimension-looking token that is not the WordPress size suffix stays.
    ("https://x/a/1024x768-portrait.jpg", "https://x/a/1024x768-portrait.jpg"),
])
def test_bayside_full_res(url, expected):
    assert sg.bayside_full_res(url) == expected


def test_bayside_volume_in_ml_and_two_letter_profile():
    case = bayside_case("bayside_case_three_views.html")
    assert sg.volume_cc(case.specs) == 275      # '275ml' reads as cc
    assert case.specs.profile == "moderate"     # 'MP'
    assert case.specs.age == 30
    assert case.specs.gender == "female"
    assert case.specs.months_post_op == 4.0
    # 'saline' is a fill, not a manufacturer, and no shell shape is published.
    assert case.specs.brand == "unknown"
    assert case.specs.shape is None


def test_bayside_moderate_plus_beats_bare_mp():
    case = bayside_case("bayside_case_moderate_plus.html")
    assert case.specs.profile == "moderate-plus"
    assert sg.volume_cc(case.specs) == 300


def test_bayside_case_publishing_no_profile_records_none():
    case = bayside_case("bayside_case_no_profile.html")
    assert case.specs.profile is None
    assert sg.volume_cc(case.specs) == 300      # volume is still published


@pytest.mark.parametrize("text,expected", [
    ("300ml MP saline implants", None),
    ("380ml HP saline filled breast implants", None),
    ("350ml MP saline filled implants and suction lipectomy of axillary folds",
     "lipectomy"),
    ("300ml MP saline implants with left periareolar mastopexy to improve "
     "breast symmetry", "mastopexy"),
    # Negated mentions are not combined procedures.
    ("breast enlargement surgery (without breast lift) with 460ml HP implants",
     None),
    ("breast augmentation only with 330 ml HP saline implants to avoid "
     "scarring from mastopexy", None),
    # A tattoo and an asymmetric fill are not procedures either.
    ("460ml HP saline filled breast implants (and new tattoo)", None),
    ("300 ml MP saline implants differentially filled to correct asymmetry",
     None),
])
def test_bayside_purity_is_screened_on_case_text(text, expected):
    assert sg.bayside_screen_purity(text) == expected


@pytest.mark.parametrize("fixture,named", [
    ("bayside_case_combined_lipectomy.html", "lipectomy"),
    ("bayside_case_combined_mastopexy.html", "mastopexy"),
])
def test_bayside_combined_case_emits_no_pairs(fixture, named):
    case = bayside_case(fixture)
    assert case.pairs == []
    assert any(named in w for w in case.warnings)


@pytest.mark.parametrize("fixture", [
    "bayside_case_without_lift.html",
    "bayside_case_augmentation_only.html",
])
def test_bayside_negated_lift_mention_still_yields_pairs(fixture):
    """37 of 78 cases sit under a 'bam-' slug and are plain augmentations.

    Screening the slug instead of the text would drop every one of them.
    """
    case = bayside_case(fixture)
    assert len(case.pairs) == 3
    assert not case.warnings


def test_bayside_two_timepoint_caption_records_no_follow_up_interval():
    """'both 3 months and then 6 years following' names neither timepoint."""
    case = bayside_case("bayside_case_two_timepoints.html")
    assert case.specs.months_post_op is None
    assert sg.volume_cc(case.specs) == 420
    assert case.specs.profile == "high"


def test_bayside_template_view_comments_are_never_read():
    """The carousel's profile_view/frontal_view/oblique_view comments are

    static boilerplate: the same four appear on the two-view cases too, and
    they disagree with the photographs (slot 1 is a front view, not a profile).
    """
    two = load_fixture("bayside_case_two_views.html")
    assert two.count("profile_view") == 1 and two.count("frontal_view") == 1
    case = bayside_case("bayside_case_two_views.html")
    assert all(p.view_hint is None for p in case.pairs)


# ---------------------------------------------------------------------------
# 2026-08-25 batch: shared screens (purity, the captain's profile vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,term", [
    ("Mommy Makeover", "mommy makeover"),
    ("Breast Augmentation with Lift", "augmentation with lift"),
    ("Breast Augmentation and Breast Lift", "augmentation and breast lift"),
    ("Augmentation Mastopexy", "mastopexy"),
    ("breast reduction", "breast reduction"),
    ("Implant Removal", "implant removal"),
    ("Breast Augmentation with fat grafting", "fat grafting"),
    ("Abdominoplasty", "abdominoplasty"),
])
def test_combined_procedure_screen_names_the_term_it_matched(text, term):
    """Every rejection carries its evidence, per the per-clinic accounting."""
    assert sg.combined_procedure_term(text) == term


@pytest.mark.parametrize("text", [
    "Breast Augmentation (Silicone Implants)",
    "bilateral breast augmentation in partial submuscular pocket",
    "6 months post-op with 410 cc high profile silicone gel implants",
    # 'lift' as prose about what an implant does is not a lift PROCEDURE.
    "the implant lifts the upper pole",
])
def test_combined_procedure_screen_passes_pure_augmentation(text):
    assert sg.combined_procedure_term(text) is None


@pytest.mark.parametrize("text,profile", [
    ("430UHP", "extra-high"),          # thebodydoc UHP gallery, case 09
    ("350HP", "high"),                 # thebodydoc UHP gallery, case 08
    ("375HP", "high"),                 # tcplasticsurgery patient-114
    ("300 MP", "moderate"),
    ("Extra-Full projection", "extra-high"),
    ("Corsé", "extra-high"),
    ("VHP", "extra-high"),
])
def test_captain_profile_ruling_decodes_its_vocabulary(text, profile):
    """The 2026-08-19 ruling, applied to labelled implant fields only."""
    assert sg.captain_profile_term(text) == profile


@pytest.mark.parametrize("text", [
    # Mentor's product line is a product NAME, not a projection (the Natrelle
    # model-code precedent); 'Moderate High' is simply not in the ruling.
    "450 High Profile Xtra Filled",
    "310 Moderate High Xtra Filled",
    # Lower-case 'hp'/'mp' inside ordinary words are not the chart abbreviation.
    "champion",
    "sharp",
])
def test_captain_profile_ruling_does_not_invent_a_decode(text):
    assert sg.captain_profile_term(text) is None


@pytest.mark.parametrize("value,cc", [
    ("339cc", 339.0),
    ("450 High Profile Xtra Filled", 450.0),   # label supplies the unit
    ("430UHP", 430.0),                         # number hard against the abbrev
    ("375HP", 375.0),
    ("270 filled to 285cc", 285.0),            # the FINAL volume is implanted
    ("34A", None),                             # a bra size is not a volume
    ("", None),
    ("1200cc", None),                          # outside the schema's bounds
])
def test_labelled_volume_reads_only_a_size_field(value, cc):
    assert sg.labelled_volume(value) == cc


# ---------------------------------------------------------------------------
# rmgallery2 family (Rosemont Media "RM Gallery 2"; gryskiewicz)
# ---------------------------------------------------------------------------

RMG_GALLERY = "/gallery/breast/silicone-breast-augmentation/"


def _rmg_cases() -> dict:
    """The fixture's four verbatim case-wrap blocks, keyed by patient slug."""
    parts = re.split(r"<!-- (silicone-breast-augmentation_patient-\d+) -->",
                     load_fixture("rmgallery2_gryskiewicz_cases.html"))
    return dict(zip(parts[1::2], parts[2::2]))


def _rmg_case(slug: str):
    return sg.rmgallery2_parse_case(_rmg_cases()[slug], slug, "x",
                                    "Silicone Breast Augmentation")


def test_rmgallery2_listing_enumerates_and_counts_its_own_cases():
    """The listing renders every case inline, and its block count is the check.

    RM Gallery 2 publishes no case total, so a case walk that comes back short
    can only be caught against what the listing itself rendered.
    """
    html = load_fixture("rmgallery2_gryskiewicz_listing.html")
    assert sg.rmgallery2_list_cases(html, RMG_GALLERY) == [
        "patient-1", "patient-2", "patient-150"]
    assert sg.rmgallery2_listing_case_count(html) == 3


def test_rmgallery2_listing_ignores_links_outside_its_gallery():
    html = load_fixture("rmgallery2_gryskiewicz_listing.html").replace(
        "</section>",
        '<div class="bna-group case-9"><a href="https://www.tcplasticsurgery.com'
        '/gallery/breast/breast-lift/patient-9"><img class="before-img" '
        'data-src="/x/small.jpeg"></a></div></section>')
    assert "patient-9" not in sg.rmgallery2_list_cases(html, RMG_GALLERY)


def _rmg_frames(*halves: str) -> str:
    """A case whose img-wrap publishes the given before/after frame run."""
    frames = "".join(
        f'<div class="{half}-img img-frame"><img data-src="/x/RMG{n}-9-'
        f'{half[0]}/small.jpeg"></div>'
        for n, half in enumerate(halves, 1))
    return ('<section class="case-wrap">'
            f'<div class="img-wrap">{frames}</div></section>')


def test_rmgallery2_holds_a_case_whose_frame_run_stops_alternating():
    """Pairing across the gap crosses two VIEWS of one patient.

    The 'after' half would then show a pose change on top of the size change,
    and the pair id, the 400px floor, the censorship gate and the schema all
    pass it. A parser that says it cannot trust the run must not emit from it.
    """
    case = sg.rmgallery2_parse_case(
        _rmg_frames("before", "before", "after", "after"), "patient-9", "x")
    assert case.pairs == []
    assert any("two consecutive before frames" in w for w in case.warnings)
    assert any("held rather than paired across the gap" in w
               for w in case.warnings)


def test_rmgallery2_holds_a_case_with_a_trailing_unmatched_before_frame():
    case = sg.rmgallery2_parse_case(
        _rmg_frames("before", "after", "before"), "patient-9", "x")
    assert case.pairs == []
    assert any("trailing before frame" in w for w in case.warnings)


def test_rmgallery2_keeps_an_alternating_run_untouched():
    """The hold is for a desync only - a clean run still pairs every frame."""
    case = sg.rmgallery2_parse_case(
        _rmg_frames("before", "after", "before", "after"), "patient-9", "x")
    assert [p.key for p in case.pairs] == ["pair1", "pair2"]
    assert not any("held rather than paired" in w for w in case.warnings)


def test_rmgallery2_pairs_each_before_frame_with_the_after_that_follows():
    """The page's own before/after divs pair the files, not a filename rule."""
    case = _rmg_case("silicone-breast-augmentation_patient-1")
    assert [p.key for p in case.pairs] == [f"pair{i}" for i in range(1, 6)]
    for pair in case.pairs:
        assert pair.before_url.endswith("-b/original.jpeg")
        assert pair.after_url.endswith("-a/original.jpeg")
        # Separate files, so the file IS the half - never a composite split.
        assert not pair.split_composite
        assert pair.before_url != pair.after_url
        # Views are documented nowhere on this platform.
        assert pair.view_hint is None


def test_rmgallery2_reads_both_fields_when_a_chart_line_holds_two():
    """'L implant: 339cc   R implant: 339cc' is one line carrying two fields.

    Splitting a line at its first ': ' is the lakeshore failure mode: the first
    field's value swallows the rest of the chart and the case loses its volume.
    """
    specs = _rmg_case("silicone-breast-augmentation_patient-1").specs
    assert specs.fields["L implant"] == "339cc"
    assert specs.fields["R implant"] == "339cc"
    assert sg.volume_cc(specs) == 339
    assert specs.age == 44
    assert specs.fields["Size preop"] == "34A"


def test_rmgallery2_averages_asymmetric_volumes():
    specs = _rmg_case("silicone-breast-augmentation_patient-103").specs
    assert (specs.left_cc, specs.right_cc) == (275.0, 325.0)
    assert sg.volume_cc(specs) == 300


def test_rmgallery2_decodes_a_profile_abbreviated_onto_the_volume():
    specs = _rmg_case("silicone-breast-augmentation_patient-114").specs
    assert specs.fields["L implant"] == "375HP"
    assert sg.volume_cc(specs) == 375
    assert specs.profile == "high"


def test_rmgallery2_reads_placement_off_the_chart():
    specs = _rmg_case("silicone-breast-augmentation_patient-131").specs
    assert specs.placement == "submuscular"
    assert specs.profile == "moderate-plus"


def test_rmgallery2_full_res_reaches_the_original_the_listing_hides():
    small = ("https://www.tcplasticsurgery.com/wp-content/uploads/rmgallery2/"
             "RMG2515968080-520-b/small.jpeg")
    assert sg.rmgallery2_full_res(small).endswith("-520-b/original.jpeg")
    # Already-original URLs and anything else are left alone.
    original = small.replace("small", "original")
    assert sg.rmgallery2_full_res(original) == original


def test_rmgallery2_missing_chart_is_reported_not_invented():
    html = ('<section class="case-wrap"><div class="img-wrap">'
            '<div class="before-img img-frame"><img src="/a-b/original.jpeg"></div>'
            '<div class="after-img img-frame"><img src="/a-a/original.jpeg"></div>'
            '</div></section>')
    case = sg.rmgallery2_parse_case(html, "patient-9", "x", "Saline")
    assert len(case.pairs) == 1
    assert sg.volume_cc(case.specs) is None
    assert any("no div.patient-details" in w for w in case.warnings)


def test_rmgallery2_excludes_a_combined_case_and_says_which_term():
    html = _rmg_cases()["silicone-breast-augmentation_patient-1"]
    case = sg.rmgallery2_parse_case(html, "patient-1", "x",
                                    "Breast Augmentation with Lift")
    assert case.pairs == []
    assert any("augmentation with lift" in w for w in case.warnings)


# ---------------------------------------------------------------------------
# page1solutions_paged family (paginated inline Page 1 Solutions gallery;
# ciaravino). A distinct kind from page1solutions/page1/page1_inline on purpose.
# ---------------------------------------------------------------------------

P1S_SILICONE = ("https://www.thebodydoc.com/before-after-gallery-houston/breast/"
                "breast-augmentation-silicone-implants/")
P1S_UHP = ("https://www.thebodydoc.com/before-after-gallery-houston/breast/"
           "ultra-high-profile-silicone-implants/")


def _p1s(fixture: str, gallery_url: str, tag: str) -> dict:
    return {c.case_id: c for c in sg.page1solutions_parse_listing_page(
        load_fixture(fixture), gallery_url, tag)}


def test_page1solutions_pager_is_the_enumeration_check():
    """The pager enumerates every page, so the gallery states its own extent."""
    html = load_fixture("page1solutions_ciaravino_silicone.html")
    assert sg.page1solutions_page_count(html) == 38
    assert sg.page1solutions_page_count("<html>no pager</html>") is None


def test_page1solutions_page_count_reads_the_pager_and_nothing_else():
    """The pager is this family's whole enumeration check.

    A footer nav or a related-content widget carries ?page= links of its own,
    and an inflated count walks pages the gallery does not have - re-collecting
    page 1 under the case ids it already emitted on any CMS that serves it.
    """
    html = load_fixture("page1solutions_ciaravino_silicone.html").replace(
        "</body>",
        '<div class="site-footer"><a href="/blog/?page=99">older posts</a></div>'
        '<script>var related = "/news/?page=250";</script></body>')
    assert sg.page1solutions_page_count(html) == 38


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
    html = load_fixture("page1solutions_ciaravino_silicone.html").replace(
        "Breast Augmentation (Silicone Implants)", "Mommy Makeover", 1)
    cases = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")
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
    html = load_fixture("page1solutions_ciaravino_silicone.html").replace(
        "<strong>Height:</strong>", chart_line + "<strong>Height:</strong>", 1)
    cases = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")
    assert cases[0].pairs == []
    assert any("augmentation with lift" in w for w in cases[0].warnings)
    assert cases[1].pairs                       # its neighbours are untouched


def test_page1solutions_reports_a_case_with_no_chart():
    html = re.sub(r'<div class="patient-meta-info">.*?</div>', "",
                  load_fixture("page1solutions_ciaravino_silicone.html"),
                  flags=re.S)
    cases = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")
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
        if len(imgs) != 2 or sg._p1s_asset_folder(imgs[0]["src"]) != folder:
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
        load_fixture("page1solutions_ciaravino_silicone.html"), "375", 1)
    case = {c.case_id: c for c in sg.page1solutions_parse_listing_page(
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
        load_fixture("page1solutions_ciaravino_silicone.html"), "375", 2)
    case = {c.case_id: c for c in sg.page1solutions_parse_listing_page(
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
    case = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert [(p.before_url, p.after_url) for p in case.pairs] == [
        (P1S_SILICONE + f"44/0{b}.jpg", P1S_SILICONE + f"44/0{a}.jpg")
        for b, a in ((2, 1), (4, 3), (6, 5))]
    assert not any("skipped" in w for w in case.warnings)


def test_page1solutions_still_rejects_a_slide_against_the_cases_own_numbering():
    """After-first is this case's convention, so ascending is now the reversal."""
    html = _p1s_marked_block("./44/02.jpg", "./44/01.jpg",
                             ("./44/03.jpg", "./44/04.jpg"))
    case = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
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
    case = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert case.pairs == []
    assert any("data-before in the after slot" in w for w in case.warnings)


def test_page1solutions_reports_markers_that_match_no_slide_image():
    """A guard that silently checked nothing is invisible from both ends."""
    html = _p1s_marked_block("./44/01.jpg", "./44/02.jpg",
                             marked_before="./99/01.jpg",
                             marked_after="./99/02.jpg")
    case = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
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
    case = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")[0]
    assert case.pairs == []
    assert any("all live in one folder" in w for w in case.warnings)


def test_page1solutions_reports_a_block_it_could_not_key(capsys):
    """A block with no numbered asset path cannot be keyed - but it is a case.

    Dropping it silently makes it invisible from both ends: the pager counts
    pages, not cases, so nothing downstream can sum what went missing.
    """
    html = load_fixture("page1solutions_ciaravino_silicone.html").replace(
        'src="./376/', 'src="https://cdn.example.com/376/')
    cases = sg.page1solutions_parse_listing_page(html, P1S_SILICONE, "silicone")
    assert [c.case_id for c in cases] == ["silicone-378", "silicone-375"]
    assert sg.page1solutions_listing_case_count(html) == 3
    out = capsys.readouterr().out
    assert "no numbered asset path" in out
    # The block is named by whatever it does publish, so the drop is evidenced.
    assert "https://cdn.example.com/376/01.jpg" in out


P1S_TEST_CFG = sg.ClinicConfig(
    slug="p1sfamily", consent_ref="p1sfamily-agreement",
    base_url="https://p1s.example.com",
    gallery_paths=["/gallery/breast-augmentation-silicone-implants/"],
    kind="page1solutions_paged")


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


# ---------------------------------------------------------------------------
# gallatin parser (bespoke WordPress; one inline list, paired by document order)
# ---------------------------------------------------------------------------

GALLATIN_URL = "https://gallatinplasticsurgery.com/gallery/breast-augmentation/"


def _gallatin() -> dict:
    return {c.case_id: c for c in sg.gallatin_parse_listing(
        load_fixture("gallatin_listing.html"), GALLATIN_URL)}


def test_gallatin_reads_view_and_half_off_the_filename():
    case = _gallatin()["117"]
    assert [(p.key, p.view_hint) for p in case.pairs] == [("front1", "front")]
    assert case.pairs[0].before_url.endswith("Patient-117-Before-Front.jpg")
    assert case.pairs[0].after_url.endswith(
        "Patient-117-Front-After-6-months-post-op-.jpg")


def test_gallatin_full_res_drops_the_wordpress_derivative_suffix():
    assert sg.gallatin_full_res(
        "https://x/Patient-117-Before-Front-1024x1024.jpg"
    ) == "https://x/Patient-117-Before-Front.jpg"
    # A trailing sequence number is not a size suffix and must survive.
    assert sg.gallatin_full_res("https://x/Patient-135-After-1-1.png").endswith(
        "Patient-135-After-1-1.png")


def test_gallatin_tolerates_typos_in_the_filename_tokens():
    """'Befoe' and 'Sode' are real uploads; a strict token match loses the pair."""
    case = _gallatin()["121"]
    assert [(p.key, p.view_hint) for p in case.pairs] == [("side1", "side")]
    assert case.pairs[0].before_url.endswith("Patient-121-Side-Befoe.jpg")
    assert case.pairs[0].after_url.endswith(
        "Patient-121-Sode-After-6-months-post-op.jpg")


def test_gallatin_pairs_a_couple_published_after_first():
    """The last couple on the page publishes its after image before its before."""
    case = _gallatin()["31"]
    pair = case.pairs[0]
    assert pair.before_url.endswith("Patient-31-Before-Side.jpg")
    assert pair.after_url.endswith("Patient-31-After-Side.jpg")


def test_gallatin_reads_the_half_off_the_caption_when_the_filename_says_nothing():
    """One upload is a bare camera name: no patient number, no half, no view.

    The caption still resolves its HALF - that is what keeps the walk in step
    over it rather than shifting every couple after it. What the caption cannot
    supply is the patient, which is why the couple itself is held.
    """
    assert sg._gallatin_half(
        "20250827105422627.png",
        "2 months post-op with 425cc full profile silicone gel implants") == "after"
    assert sg._gallatin_view("20250827105422627.png") is None


def test_gallatin_reads_the_case_specs_off_its_captions():
    case = _gallatin()["117"]
    assert sg.volume_cc(case.specs) == 410
    assert case.specs.profile == "high"
    assert case.specs.months_post_op == 6.0
    assert case.specs.age == 29


def test_gallatin_does_not_decode_a_bare_abbreviation_from_its_caption():
    """The captain's profile vocabulary reads a LABELLED implant field only.

    gallatin publishes no chart, so its captions are prose and a bare 'UHP'
    there is not the clinic stating a profile - the same chart-not-narrative
    rule that keeps placement unrecorded. Profile does reach a training
    caption, so a missing one beats a wrong one.
    """
    html = load_fixture("gallatin_listing.html").replace(
        "410 cc high profile", "410cc UHP")
    case = {c.case_id: c
            for c in sg.gallatin_parse_listing(html, GALLATIN_URL)}["117"]
    assert sg.volume_cc(case.specs) == 410      # the volume still counts
    assert case.specs.profile is None
    # The clinic's own spelled-out profile is still read, as it always was.
    assert _gallatin()["117"].specs.profile == "high"


def test_gallatin_records_no_placement_from_its_caption_prose():
    """Placement comes from CHART text only, never narrative (AGENTS.md).

    The caption says 'in partial submuscular pocket' and gallatin publishes no
    chart at all, so the field stays unrecorded: a missing placement beats a
    wrong one, and the marina precedent settled that prose naming a placement
    may be explaining options rather than reporting this patient's.
    """
    case = _gallatin()["117"]
    assert "submuscular" in case.specs.summary
    assert case.specs.placement is None
    assert case.specs.incision is None


@pytest.mark.parametrize("caption,months", [
    ("6 months post-op with 410 cc high profile silicone gel implants", 6.0),
    ("6 weeks post-op with 400cc moderate profile silicone gel implants", 1.38),
    ("16 monthd post-op with 385cc high profile silicone gel implants", 16.0),
    ("2 months with 380cc Moderate profile smooth round silicone gel implants", 2.0),
    ("bilateral breast augmentation", None),
    # The post-op marker is not always hard against the interval, and is
    # sometimes only implied - these are follow-up intervals all the same.
    ("3 months, 400cc implants", 3.0),
    ("6 month follow up with 350cc implants", 6.0),
    ("6 months after surgery with 350cc implants", 6.0),
    ("patient 6 months out with 350cc", 6.0),
])
def test_gallatin_timepoint_tolerates_the_captions_as_written(caption, months):
    assert sg.gallatin_months_post_op(caption) == months


def test_gallatin_leaves_an_undecodable_profile_unrecorded():
    """'full profile' and 'low profile' are not in the schema or the ruling."""
    html = load_fixture("gallatin_listing.html").replace(
        "high profile", "full profile")
    case = {c.case_id: c for c in sg.gallatin_parse_listing(html, GALLATIN_URL)}["117"]
    assert case.specs.profile is None
    assert sg.volume_cc(case.specs) == 410      # the volume still counts


def test_gallatin_excludes_a_combined_case_and_says_which_term():
    html = load_fixture("gallatin_listing.html").replace(
        "before bilateral breast augmentation in partial submuscular pocket",
        "before a mommy makeover", 1)
    cases = {c.case_id: c for c in sg.gallatin_parse_listing(html, GALLATIN_URL)}
    assert cases["117"].pairs == []
    assert any("mommy makeover" in w for w in cases["117"].warnings)
    assert cases["121"].pairs                   # its neighbours are untouched


def test_gallatin_accounting_separates_a_ruling_from_a_parse_failure(capsys):
    """A purity-screened case DID pair; only its ruling kept it out.

    Reporting its items as unpaired classifies a captain ruling as a parser
    miss, and the per-clinic accounting has to sum every rendered item into
    exactly one disposition.
    """
    html = load_fixture("gallatin_listing.html").replace(
        "before bilateral breast augmentation in partial submuscular pocket",
        "before a mommy makeover", 1)
    sg.gallatin_parse_listing(html, GALLATIN_URL)
    line = capsys.readouterr().out
    assert "renders 8 item(s); 6 of them paired into 3 pair(s)" in line
    assert "across 3 case(s)" in line
    assert "1 pair(s) excluded as combined procedures" in line
    # The two held items are the bare-name couple, reported as their own term.
    assert "2 item(s) unresolved" in line


def test_gallatin_reports_a_listing_that_rendered_nothing_as_a_failure(capsys):
    """Zero items is an error page served as 200, not a clean empty gallery.

    The gallery publishes no case total to reconcile against, so an all-zero
    accounting line is the only trace a total collection failure leaves.
    """
    assert sg.gallatin_parse_listing(
        '<html><body><ul class="gps-gallery-list"></ul></body></html>',
        GALLATIN_URL) == []
    assert "WARN" in capsys.readouterr().out


def test_gallatin_accounting_sums_every_item_when_one_will_not_pair(capsys):
    html = load_fixture("gallatin_listing.html").replace(
        "</li>",
        '</li><li class="gps-gallery-item">'
        '<img data-src="https://x/Patient-500-Before-Front.jpg">'
        '<div class="image-meta"><h6 class="caption">31 year old patient before '
        'bilateral breast augmentation</h6></div></li>', 1)
    sg.gallatin_parse_listing(html, GALLATIN_URL)
    line = capsys.readouterr().out
    # 9 rendered = 2 pairs x 2 paired + 5 unresolved, none of them excluded:
    # the stray, the couple it straddles, and the bare-name couple.
    assert "renders 9 item(s); 4 of them paired into 2 pair(s)" in line
    assert "0 pair(s) excluded as combined procedures" in line
    assert "5 item(s) unresolved" in line


def test_gallatin_unresolvable_item_shifts_the_pairing_by_one(capsys):
    """A stray item must not mis-pair every couple after it.

    The walk advances by ONE item when a couple is not one before and one
    after, so the shift costs the stray item and nothing else - and what is
    still unresolvable is printed rather than silently dropped.
    """
    html = load_fixture("gallatin_listing.html").replace(
        '<ul class="gps-gallery-list">',
        '<ul class="gps-gallery-list"><li class="gps-gallery-item">'
        '<img data-src="https://x/Patient-500-Before-Front.jpg">'
        '<div class="image-meta"><h6 class="caption">31 year old patient before '
        'bilateral breast augmentation</h6></div></li>', 1)
    cases = {c.case_id: c for c in sg.gallatin_parse_listing(html, GALLATIN_URL)}
    assert set(cases) == {"117", "121", "31"}
    assert all(len(c.pairs) == 1 for c in cases.values())
    assert "did not resolve" in capsys.readouterr().out


def test_gallatin_never_pairs_two_different_patients(capsys):
    """One before and one after is not enough: they must name the same patient.

    A stray item inserted AFTER the first makes the next couple straddle two
    patients. Pairing it on half alone fabricates a before/after spanning two
    people - a corruption the pair id, the 400px floor, the censorship gate and
    the schema all pass, and that surfaces only as a model that learned nothing.
    """
    html = load_fixture("gallatin_listing.html").replace(
        "</li>",
        '</li><li class="gps-gallery-item">'
        '<img data-src="https://x/Patient-500-Before-Front.jpg">'
        '<div class="image-meta"><h6 class="caption">31 year old patient before '
        'bilateral breast augmentation</h6></div></li>', 1)
    cases = sg.gallatin_parse_listing(html, GALLATIN_URL)
    for case in cases:
        for pair in case.pairs:
            named = {m.group(1) for url in (pair.before_url, pair.after_url)
                     for m in [sg.GALLATIN_PATIENT_RE.search(url)] if m}
            assert named <= {case.case_id}
    # The stray and the couple it straddles are reported, not paired anyway.
    assert {c.case_id for c in cases} == {"121", "31"}
    assert "did not resolve" in capsys.readouterr().out


def test_gallatin_holds_a_couple_only_one_half_of_which_names_a_patient(capsys):
    """A bare camera-name upload has no patient number, so nothing checks it.

    The gallery publishes exactly this shape (Patient-151-.png next to
    20250827105422627.png), and one stray item is enough to stand a numbered
    half beside a FOREIGN bare-named one - the same fabricated cross-patient
    pair the numbered guard exists to stop, with no evidence left on the page
    to tell the two apart. Held rather than paired: a wrong pair is worse than
    a missing one (captain ruling, 2026-08-26).
    """
    cases = {c.case_id: c for c in sg.gallatin_parse_listing(
        load_fixture("gallatin_listing.html"), GALLATIN_URL)}
    assert "151" not in cases
    assert set(cases) == {"117", "121", "31"}
    out = capsys.readouterr().out
    # Held, and counted as held - not quietly absent.
    assert "2 item(s) unresolved" in out
    assert "Patient-151-.png" in out and "20250827105422627.png" in out


GALLATIN_ITEM = ('<li class="gps-gallery-item"><img data-src="https://x/{name}">'
                 '<div class="image-meta"><h6 class="caption">{caption}</h6>'
                 '</div></li>')


def _gallatin_listing(*items: tuple[str, str]) -> str:
    return ('<html><body><ul class="gps-gallery-list">'
            + "".join(GALLATIN_ITEM.format(name=n, caption=c) for n, c in items)
            + "</ul></body></html>")


def test_gallatin_takes_the_specs_from_the_first_caption_that_states_them():
    """Not every caption of a case repeats every field.

    A pair carrying no volume_cc produces no training caption and drops out of
    the trainable corpus, so a case whose FIRST caption omits the volume the
    clinic published on its second must not be locked to the first.
    """
    case = sg.gallatin_parse_listing(_gallatin_listing(
        ("Patient-900-Before-Front.jpg",
         "31 year old patient before bilateral breast augmentation in partial "
         "submuscular pocket"),
        ("Patient-900-Front-After.jpg", "6 months post-op result"),
        ("Patient-900-Before-Side.jpg",
         "31 year old patient before bilateral breast augmentation"),
        ("Patient-900-Side-After.jpg",
         "6 months post-op with 410 cc high profile silicone gel implants"),
    ), GALLATIN_URL)[0]
    assert [p.key for p in case.pairs] == ["front1", "side2"]
    assert sg.volume_cc(case.specs) == 410
    assert case.specs.profile == "high"
    assert case.specs.age == 31
    assert case.warnings == []


def test_gallatin_reports_a_case_whose_captions_disagree_on_the_volume():
    case = sg.gallatin_parse_listing(_gallatin_listing(
        ("Patient-901-Before-Front.jpg", "31 year old patient before bilateral "
         "breast augmentation"),
        ("Patient-901-Front-After.jpg", "6 months post-op with 410 cc implants"),
        ("Patient-901-Before-Side.jpg", "31 year old patient before bilateral "
         "breast augmentation"),
        ("Patient-901-Side-After.jpg", "6 months post-op with 375 cc implants"),
    ), GALLATIN_URL)[0]
    assert sg.volume_cc(case.specs) == 410
    assert any("disagrees" in w and "410cc" in w for w in case.warnings)


@pytest.mark.parametrize("age_phrase", [
    "29 year old patient", "29-year-old patient", "29 years old patient"])
def test_gallatin_timepoint_does_not_read_the_patients_age(age_phrase):
    """The captions open with the age, and an age is a number-and-unit too."""
    assert sg.gallatin_months_post_op(age_phrase) is None
    assert sg.gallatin_months_post_op(
        f"{age_phrase}, 6 months post-op with 410 cc implants") == 6.0


# ---------------------------------------------------------------------------
# 2026-08-25 batch: clinic registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug,kind", [
    ("gryskiewicz", "rmgallery2"),
    ("ciaravino", "page1solutions_paged"),
    ("gallatin", "gallatin"),
])
def test_2026_08_25_batch_is_registered_with_a_traceable_consent_ref(slug, kind):
    """Section 6 lets a surgeon revoke; a pair must name the form that covers it."""
    cfg = sg.CLINICS[slug]
    assert cfg.kind == kind
    assert cfg.consent_ref == f"{slug}-agreement-2026-08-25"
    assert all(p.startswith("/") and p.endswith("/") for p in cfg.gallery_paths)


def test_gryskiewicz_collects_only_the_three_augmentation_galleries():
    """augmentation-with-lift is a fourth category and is excluded by construction."""
    paths = sg.CLINICS["gryskiewicz"].gallery_paths
    assert paths == [
        "/gallery/breast/silicone-breast-augmentation/",
        "/gallery/breast/saline-breast-augmentation/",
        "/gallery/breast/dual-plane-breast-augmentation/",
    ]
    assert not any("lift" in p for p in paths)


# ---------------------------------------------------------------------------
# View-typed pairs: recording what a photograph is without guessing its side
# ---------------------------------------------------------------------------


def _lateral_pair():
    return sg.ImagePair(key="pair2", before_url="b.jpg", after_url="a.jpg")


def test_view_type_without_laterality_holds_the_pair():
    """'side' with no left/right records the photograph and emits nothing.

    CLAUDE.md allows a laterality label only from a landmark visible in both a
    case's front and its lateral. Recording the TYPE keeps the reliable half of
    the call without guessing the half that needs the landmark.
    """
    ann = {"pairs": {"pair2": {"view": "side"}}}
    assert sg.resolve_view(_lateral_pair(), ann) == (None, None)
    assert "held pending a left/right label" in sg.view_skip_reason(
        _lateral_pair(), ann)


def test_adding_a_laterality_releases_a_view_typed_pair():
    """One field is all that stands between a held pair and an emitted one."""
    ann = {"pairs": {"pair2": {"view": "side", "laterality": "right"}}}
    view, source = sg.resolve_view(_lateral_pair(), ann)
    assert view == "side-right"
    assert "view type from visual inspection" in source
    assert "laterality from visual inspection" in source


def test_case_level_laterality_also_releases_a_view_typed_pair():
    ann = {"laterality": "left", "pairs": {"pair2": {"view": "oblique"}}}
    assert sg.resolve_view(_lateral_pair(), ann)[0] == "oblique-left"


def test_pair_laterality_wins_over_the_case_default():
    ann = {"laterality": "left",
           "pairs": {"pair2": {"view": "side", "laterality": "right"}}}
    assert sg.resolve_view(_lateral_pair(), ann)[0] == "side-right"


def test_an_unannotated_pair_is_reported_differently_from_a_held_one():
    """Held and never-looked-at are separate dispositions in the accounting."""
    assert sg.view_skip_reason(_lateral_pair(), {}) == "no view annotation"


@pytest.mark.parametrize("laterality", ["Left", "l", "L", "unknown"])
def test_a_laterality_the_release_path_rejects_is_named_not_called_missing(
        laterality):
    """The annotator filled the field in; the accounting must not deny it.

    Releasing a held pair is meant to cost one field and no re-crawl, so a
    value resolve_view will not take has to be reported as the value it is.
    """
    ann = {"pairs": {"pair2": {"view": "side", "laterality": laterality}}}
    assert sg.resolve_view(_lateral_pair(), ann) == (None, None)
    reason = sg.view_skip_reason(_lateral_pair(), ann)
    assert repr(laterality) in reason
    assert "no laterality" not in reason


@pytest.mark.parametrize("view", ["Front", "Side", "oblique-l", "front view"])
def test_a_view_the_release_path_rejects_is_named_not_called_missing(view):
    """'no view annotation' about an annotated pair sends work back to be redone.

    A pair carrying a view AND a laterality is fully annotated; reporting it as
    one nobody has looked at is the same confusion the laterality message was
    fixed for, on the release path this batch's held lateral pairs depend on.
    """
    ann = {"pairs": {"pair2": {"view": view, "laterality": "left"}}}
    assert sg.resolve_view(_lateral_pair(), ann) == (None, None)
    reason = sg.view_skip_reason(_lateral_pair(), ann)
    assert repr(view) in reason
    assert reason != "no view annotation"


def test_a_case_level_laterality_the_release_path_rejects_is_named_too():
    ann = {"laterality": "LEFT", "pairs": {"pair2": {"view": "oblique"}}}
    assert sg.resolve_view(_lateral_pair(), ann) == (None, None)
    assert "'LEFT'" in sg.view_skip_reason(_lateral_pair(), ann)


def test_a_page_documented_view_type_still_needs_its_laterality():
    """gallatin's filename says 'Side' and never which side."""
    pair = sg.ImagePair(key="side2", before_url="b.jpg", after_url="a.jpg",
                        view_hint="side")
    assert sg.resolve_view(pair, {}) == (None, None)
    assert "held pending" in sg.view_skip_reason(pair, {})
    assert sg.resolve_view(pair, {"laterality": "right"})[0] == "side-right"


def test_a_full_schema_view_annotation_still_wins_outright():
    pair = sg.ImagePair(key="pair1", before_url="b.jpg", after_url="a.jpg")
    ann = {"pairs": {"pair1": {"view": "front"}}}
    assert sg.resolve_view(pair, ann) == (
        "front", "visual inspection of downloaded images")
