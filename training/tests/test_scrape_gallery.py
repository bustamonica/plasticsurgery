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
    assert sg.etna_ajax_case_paths(html) == [
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
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=3)
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
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=2)
    assert got == first + second
    sizes = [p[1]["case_count"] for p in session.posts]
    assert sizes == [str(sg.ETNA_AJAX_PAGE_SIZE),
                     str(sg.ETNA_AJAX_RETRY_PAGE_SIZE)]


def test_etna_endpoint_posts_to_the_declared_url_with_the_action_query(
        tmp_path, monkeypatch):
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    session = _RecordingSession([_ajax_payload([], 0, 0)])
    f = _fetcher(tmp_path, monkeypatch, session)
    sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=1)
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
    assert sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, 1) == paths
    offline = sg.PoliteFetcher(tmp_path, delay=0, offline=True)
    assert sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], offline, listing, 1) == paths
    assert len(session.posts) == 1


def test_etna_endpoint_sweep_is_cache_bounded_offline(tmp_path, monkeypatch):
    """An offline re-parse must survive a cache taken before the sweep existed.

    Proving a shared parser's blast radius means re-parsing every cached case for
    every clinic offline. That has to keep working, so a missing endpoint
    response ends the sweep instead of the run.
    """
    listing = load_fixture("etna_tccs_listing_endpoint.html")
    offline = sg.PoliteFetcher(tmp_path, delay=0, offline=True)
    assert sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], offline, listing, 50) == []


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
    sg.collect_cases(cfg, f, gallery_endpoint=True)
    assert (tmp_path / "tccs_listing_endpoint.html").exists()
    # The shared entry is untouched: same bytes, still the stale total.
    assert (tmp_path / "tccs_listing.html").read_text() == stale


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
    got = sg.etna_endpoint_case_paths(sg.CLINICS["tccs"], f, listing, total=60)
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
