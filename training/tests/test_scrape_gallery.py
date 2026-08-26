"""Tests for scrape_gallery.py parsing against real (trimmed) gallery snapshots.

Fixtures under fixtures/gallery/ are text/HTML excerpts of real case pages
(gallery image tags + spec blocks). No patient images are stored.
"""

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
