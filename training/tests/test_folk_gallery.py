"""Tests for the Folk Plastic Surgery Webflow gallery parser (folk_gallery.py).

`fixtures/gallery/folk_listing_excerpt.html` is the real markup of six real
case blocks, chosen because each one breaks a different assumption. The
responsive `srcset`/`sizes` attributes are stripped (the parser never reads
them and they made the fixture unreviewable); nothing else is altered, and no
patient images are stored.

Cases kept and why:
  1  sided volumes written volume-then-side
  3  a Natrelle "low-profile plus" - real vocabulary with NO schema equivalent
  4  `Volume: Between 450cc to 500cc` - a range, which is not a measurement
  5  a plain volume with a profile and a placement
  10 a DIFFERENT implant per breast
  21 a case publishing no implant text at all
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import folk_gallery as fg  # noqa: E402
import scrape_gallery as sg  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gallery"


@pytest.fixture(scope="module")
def cases():
    html = (FIXTURES / "folk_listing_excerpt.html").read_text()
    return {c.case_id: c for c in fg.folk_parse_listing(html, "https://x/gallery")}


def test_every_published_case_is_returned(cases):
    assert sorted(cases, key=int) == ["1", "3", "4", "5", "10", "21"]


# ---------------------------------------------------------------------------
# The photographs are in the lightbox manifest, not the <img> tags
# ---------------------------------------------------------------------------


def test_composites_come_from_the_lightbox_manifest(cases):
    """Each case carries five lightbox slots, four of them unbound; the real
    set of composites is the JSON of the one populated manifest. Reading the
    thumbnails finds one image per case."""
    assert [len(cases[c].pairs) for c in ("1", "3", "4", "5", "10", "21")] == \
        [3, 3, 5, 5, 3, 2]


def test_every_pair_is_a_composite_to_split_at_the_midpoint(cases):
    for case in cases.values():
        for pair in case.pairs:
            assert pair.split_composite is True
            assert pair.before_url == pair.after_url


def test_no_downscaled_srcset_rendition_is_emitted(cases):
    """`-p-500`/`-p-800` renditions fall under the 400px floor once split."""
    for case in cases.values():
        for pair in case.pairs:
            assert "-p-500" not in pair.before_url
            assert "-p-800" not in pair.before_url
            assert pair.before_url.endswith("_highres.webp")


# ---------------------------------------------------------------------------
# Webflow conditional visibility
# ---------------------------------------------------------------------------


def test_conditionally_hidden_values_are_not_read():
    """Webflow renders EVERY option of a conditional field and hides the ones
    that do not apply. Patient 1 publishes four post-op timelines of which
    three carry `w-condition-invisible`; reading them all records three
    timelines the clinic never claimed for that patient."""
    block = ('<div class="case-display w-dyn-item">'
             '<div class="post-op-timeline-text">1-11 Weeks</div>'
             '<div class="post-op-timeline-text w-condition-invisible">3-5 Months</div>'
             '<div class="post-op-timeline-text w-condition-invisible">1 Year +</div>'
             '</div>')
    text = fg._text(fg._visible_only(block))
    assert "1-11 Weeks" in text
    assert "3-5 Months" not in text
    assert "1 Year" not in text


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


def test_volume_written_before_the_side_keeps_both_breasts(cases):
    """'300 cc (right side), 250cc (left side)' - the ordering the shared
    narrative reader gets backwards."""
    specs = cases["1"].specs
    assert (specs.left_cc, specs.right_cc) == (250.0, 300.0)


def test_side_can_sit_several_words_after_its_figure(cases):
    """Patient 10: '250cc moderate classic (right), 300cc moderate profile
    plus (left)'. A short window read this as one-sided and lost the left."""
    specs = cases["10"].specs
    assert (specs.left_cc, specs.right_cc) == (300.0, 250.0)


@pytest.mark.parametrize("text", [
    "Implant Type: Silicone Gel Volume: Between 450cc to 500cc",
    "Sientra silicone gel breast implants. Volume: Between 250cc and 300cc",
])
def test_a_published_range_is_not_a_volume(text):
    assert fg.folk_parse_volumes(text) == (None, None)


def test_side_written_before_the_volume_keeps_both_breasts():
    """Patient 14: 'Right side 325cc, Left side 350cc' once read as 325/325."""
    assert fg.folk_parse_volumes(
        "Mentor Implants: Right side 325cc, Left side 350cc Female C A") == (350.0, 325.0)


@pytest.mark.parametrize("text", [
    "Smooth round moderate profile saline implants (300cc filled to 325cc)",
    "Smooth, round, saline 300 cc implants filled to 325cc each",
])
def test_a_saline_fill_records_no_volume(text):
    """Patients 23 and 27 publish two figures without saying which is the
    implant, so neither one is kept."""
    assert fg.folk_parse_volumes(text) == (None, None)


def test_range_case_publishes_no_volume(cases):
    """Under the captain's 2026-08-26 ruling such a case is skipped rather
    than emitted on the midpoint of a range."""
    assert cases["4"].specs.left_cc is None


def test_case_with_no_implant_text_publishes_no_volume(cases):
    assert cases["21"].specs.left_cc is None
    assert sg.volume_cc(cases["21"].specs) is None


def test_plain_volume_applies_to_both_breasts(cases):
    specs = cases["5"].specs
    assert (specs.left_cc, specs.right_cc) == (275.0, 275.0)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_profile_and_placement_are_read(cases):
    assert cases["5"].specs.profile == "moderate-plus"
    assert cases["5"].specs.placement == "submuscular"


def test_low_profile_plus_has_no_schema_value_and_is_omitted(cases):
    """Natrelle Inspira LP+ is real vocabulary, but 'low' is not in the
    schema's profile enum, so it is left undocumented rather than rounded up."""
    assert cases["3"].specs.profile is None


def test_a_moderate_plus_is_not_also_read_as_a_moderate():
    """The plain patterns must not fire on their own '... plus' form, or one
    stated projection reads as two and the conflict guard discards it."""
    assert fg.folk_profile("smooth round moderate profile plus") == "moderate-plus"


def test_full_is_high_and_extra_full_is_extra_high():
    """Captain's ruling of 2026-08-26, shared through sg.FULL_PROJECTION_PATTERNS:
    the two are distinct rungs, so neither trips the conflict guard alone."""
    assert fg.folk_profile("smooth round full profile silicone") == "high"
    assert fg.folk_profile("smooth round extra full profile silicone") == "extra-high"


def test_different_implant_per_breast_is_warned_not_silently_resolved(cases):
    """`profile` is one field per pair, so a decoded value describes at most
    one side. The arps rule: report the contradiction."""
    case = cases["10"]
    assert any("different implant per breast" in w for w in case.warnings)


# ---------------------------------------------------------------------------
# Purity and config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,reason", [
    ("Breast augmentation with mastopexy", "mastopexy"),
    ("Breast implant exchange", "implant exchange"),
    ("Breast augmentation and tummy tuck", "abdominoplasty"),
    ("Breast augmentation with bilateral mastopexies", "mastopexy"),
    ("Breast augmentation with fat injections", "fat transfer"),
])
def test_purity_is_screened_on_the_case_text(text, reason):
    """Never on the asset name - every photograph in this gallery is named
    `breast-augmentation-before-and-after-...` whatever the case says."""
    assert fg.folk_impure_reason(text) == reason


def test_pure_cases_are_not_screened_out(cases):
    for case_id in ("1", "5", "10"):
        assert not any("not a pure augmentation" in w for w in cases[case_id].warnings)


def test_folk_is_registered_with_its_own_kind_and_measured_crop():
    cfg = sg.CLINICS["folk"]
    assert cfg.kind == "folk"
    assert cfg.consent_ref == "folk-consent-2026-08-25-rosemont-16"
    # Measured on this clinic and never transferred: the mark needs 0.096 of
    # half width at worst and is cropped at 0.105.
    assert cfg.bottom_crop_frac == 0.105


def _composite(before_seed, after_seed):
    """A landscape before|after composite whose halves are seeded noise."""
    import io

    import numpy as np
    from PIL import Image
    halves = [np.random.default_rng(seed).integers(0, 256, (64, 64), dtype=np.uint8)
              for seed in (before_seed, after_seed)]
    buf = io.BytesIO()
    Image.fromarray(np.hstack(halves)).save(buf, format="PNG")
    return buf.getvalue()


def _folk_block(patient, url):
    manifest = json.dumps({"items": [{"url": url, "type": "image"}]})
    return ('<div class="case-display w-dyn-item"><div>Patient #</div>'
            f'<div>{patient}</div><div>300cc smooth round silicone implants</div>'
            f'<script type="application/json" class="w-json">{manifest}</script></div>')


def test_collect_cases_drops_a_republished_patient_on_the_before_half(tmp_path):
    """A duplicate patient would sit on both sides of the by-patient split.
    Patient 2 republishes patient 1's before-half beside a different after;
    patient 3 shares only an after-half and is a different woman; patient 4's
    composite cannot be fetched and is kept rather than dropped."""
    cfg = sg.CLINICS["folk"]
    urls = {n: f"https://cdn.example.com/case-{n}_highres.webp" for n in (1, 2, 3, 4)}
    (tmp_path / "folk_listing.html").write_text(
        "".join(_folk_block(n, urls[n]) for n in (1, 2, 3, 4)))
    for patient, seeds in ((1, (1, 5)), (2, (1, 6)), (3, (2, 5))):
        path = tmp_path / sg.image_cache_key("folk", urls[patient])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_composite(*seeds))

    cases = sg.collect_cases(cfg, sg.PoliteFetcher(tmp_path, delay=0, offline=True))

    assert [c.case_id for c in cases] == ["1", "2", "3", "4"]
    assert [bool(c.pairs) for c in cases] == [True, False, True, True]
    assert any("photographs of case 1" in w for w in cases[1].warnings)
