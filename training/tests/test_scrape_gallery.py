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
