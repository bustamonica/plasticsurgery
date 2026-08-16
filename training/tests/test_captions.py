"""Caption assembly tests for build_dataset.build_caption.

The caption is the only thing that makes an axis controllable at inference, so
the three the product exposes each get their own class below: the view
(finding 1 - it was absent from every caption), the profile (finding 2 - it was
defaulted to "a balanced profile" on the 61% of pairs that record none), and the
volume (finding 3 - already correct, and pinned here so it stays that way).
"""

import json
from pathlib import Path

import pytest

from build_dataset import VIEW_LANGUAGE, build_caption

PARITY_CASES = Path(__file__).resolve().parent.parent / "caption_parity_cases.json"

BASE_META = {
    "pair_id": "clinic01-0001",
    "shape": "round",
    "volume_cc": 350,
    "view": "front",
    "consent_ref": "ref",
}


def test_golden_caption_no_clothing_field():
    caption = build_caption(dict(BASE_META, profile="moderate-plus", brand="mentor"))
    assert caption == (
        "The photograph is a front view. "
        "Edit this photo to simulate the outcome of breast augmentation surgery "
        "with 350 cc round implants giving even fullness and visible roundness "
        "in the upper breast, using a balanced moderate-plus profile, in the "
        "style of soft cohesive silicone gel implants with a natural feel. "
        "The change should read as a natural-looking increase of roughly one "
        "to one and a half cup sizes. Keep the person's identity, pose, skin "
        "tone, clothing, lighting and background exactly the same."
    )


class TestView:
    """Finding 1: the view reached no caption at all, so it could not be asked for."""

    @pytest.mark.parametrize(
        "view", ["front", "oblique-left", "oblique-right", "side-left", "side-right"]
    )
    def test_every_schema_view_states_itself_in_the_caption(self, view):
        caption = build_caption(dict(BASE_META, view=view))
        assert caption.startswith(VIEW_LANGUAGE[view] + " Edit this photo")

    @pytest.mark.parametrize(
        ("view", "phrase"),
        [
            ("front", "a front view."),
            ("oblique-left", "an oblique three-quarter view with the subject's left side"),
            ("oblique-right", "an oblique three-quarter view with the subject's right side"),
            ("side-left", "a side profile with the subject's left side"),
            ("side-right", "a side profile with the subject's right side"),
        ],
    )
    def test_each_view_reads_distinctly(self, view, phrase):
        assert phrase in build_caption(dict(BASE_META, view=view))

    def test_the_five_views_produce_five_distinct_captions(self):
        captions = {build_caption(dict(BASE_META, view=v)) for v in VIEW_LANGUAGE}
        assert len(captions) == len(VIEW_LANGUAGE) == 5

    def test_laterality_follows_the_corpus_convention(self):
        # AGENTS.md: '-left' means the subject's LEFT side faces the camera.
        # A caption that inverted this would train the axis backwards.
        assert "left side toward the camera" in build_caption(dict(BASE_META, view="side-left"))
        assert "right side toward the camera" in build_caption(dict(BASE_META, view="side-right"))

    @pytest.mark.parametrize("view", [None, "", "unknown", "three-quarter"])
    def test_an_unrecorded_view_asserts_none(self, view):
        meta = dict(BASE_META)
        if view is None:
            del meta["view"]
        else:
            meta["view"] = view
        caption = build_caption(meta)
        assert caption.startswith("Edit this photo")
        assert "The photograph is" not in caption


class TestProfile:
    """Finding 2: an unrecorded profile was asserted as a moderate one."""

    @pytest.mark.parametrize(
        ("profile", "phrase"),
        [
            ("moderate", "a moderate profile with a wide base and gentle forward projection"),
            ("moderate-plus", "a balanced moderate-plus profile"),
            ("high", "a high profile with noticeable forward projection and a rounder look"),
            ("extra-high", "an extra-high profile with maximum forward projection"),
        ],
    )
    def test_a_recorded_profile_still_renders(self, profile, phrase):
        assert f", using {phrase}." in build_caption(dict(BASE_META, profile=profile))

    @pytest.mark.parametrize("profile", [None, "", "unknown", "ultra-high"])
    def test_an_unrecorded_profile_asserts_no_profile(self, profile):
        meta = dict(BASE_META)
        if profile is not None:
            meta["profile"] = profile
        caption = build_caption(meta)
        assert "profile" not in caption
        assert "using" not in caption
        # ...and the sentence still closes cleanly onto the size clause.
        assert (
            "with 350 cc round implants giving even fullness and visible roundness "
            "in the upper breast. The change should read as" in caption
        )

    def test_an_unrecorded_profile_still_carries_the_brand_clause(self):
        caption = build_caption(dict(BASE_META, brand="mentor"))
        assert "upper breast, in the style of soft cohesive silicone gel implants" in caption
        assert "profile" not in caption

    def test_unknown_and_moderate_do_not_collide(self):
        # The old default made these two identical, which is exactly what
        # stopped the projection axis from being learnable.
        assert build_caption(dict(BASE_META, profile="unknown")) != build_caption(
            dict(BASE_META, profile="moderate")
        )


class TestVolumeIsExact:
    """Finding 3: 10 cc granularity depends on the literal figure. Do not break it."""

    @pytest.mark.parametrize("cc", [140, 245, 250, 255, 335, 500, 505, 695, 700])
    def test_the_literal_figure_is_stated_never_rounded(self, cc):
        assert f"with {cc} cc " in build_caption(dict(BASE_META, volume_cc=cc))

    def test_every_10cc_step_across_the_corpus_range_is_a_distinct_caption(self):
        captions = {build_caption(dict(BASE_META, volume_cc=cc)) for cc in range(140, 710, 10)}
        assert len(captions) == len(range(140, 710, 10))

    def test_adjacent_10cc_steps_differ(self):
        assert build_caption(dict(BASE_META, volume_cc=350)) != build_caption(
            dict(BASE_META, volume_cc=360)
        )

    @pytest.mark.parametrize("cc", [140, 249, 250, 399, 400, 549, 550, 699, 700])
    def test_size_language_never_contradicts_the_figure(self, cc):
        # The band is derived from the same cc it accompanies, so the two can
        # never disagree; assert that rather than trusting it.
        from build_dataset import size_language

        caption = build_caption(dict(BASE_META, volume_cc=cc))
        assert f"with {cc} cc " in caption
        assert f"The change should read as {size_language(cc)}." in caption


class TestCrossLanguageParity:
    """build_caption() and buildCustomModelPrompt() must agree byte for byte.

    Both sides read training/caption_parity_cases.json; this half asserts the
    Python one, lib/prompt.test.ts the TypeScript one. Neither owns the golden.
    """

    @pytest.fixture(scope="class")
    def cases(self):
        return json.loads(PARITY_CASES.read_text())["cases"]

    def test_the_contract_covers_every_view_and_the_unknown_profile(self, cases):
        views = {c["meta"].get("view") for c in cases}
        assert views == set(VIEW_LANGUAGE) | {None}
        assert any("profile" not in c["meta"] for c in cases)

    def test_every_golden_caption_is_reproduced(self, cases):
        for case in cases:
            assert build_caption(case["meta"]) == case["caption"], case["name"]


class TestClothingVariants:
    def test_nude_drops_clothing_from_preserve_list_and_adds_skin_instruction(self):
        caption = build_caption(dict(BASE_META, clothing="nude"))
        assert "skin tone, lighting and background exactly the same." in caption
        assert "skin tone, clothing, lighting" not in caption
        assert "render realistic natural skin and anatomy" in caption

    def test_bra_keeps_clothing_and_scopes_edit_under_bra(self):
        caption = build_caption(dict(BASE_META, clothing="bra"))
        assert "skin tone, clothing, lighting and background exactly the same." in caption
        assert "under the existing bra and keep the bra itself unchanged." in caption

    def test_top_keeps_clothing_and_scopes_edit_under_top(self):
        caption = build_caption(dict(BASE_META, clothing="top"))
        assert "skin tone, clothing, lighting and background exactly the same." in caption
        assert "under the existing top and keep the top itself unchanged." in caption

    def test_absent_clothing_defaults_to_preserve_clothing(self):
        caption = build_caption(BASE_META)
        assert "skin tone, clothing, lighting and background exactly the same." in caption
        assert "photographed nude" not in caption
        assert "wearing a" not in caption


class TestOptionalFields:
    def test_brand_adds_style_clause(self):
        caption = build_caption(dict(BASE_META, brand="natrelle"))
        assert ", in the style of cohesive silicone gel implants" in caption

    @pytest.mark.parametrize("brand", ["other", "unknown"])
    def test_unmapped_brand_adds_no_style_clause(self, brand):
        caption = build_caption(dict(BASE_META, brand=brand))
        assert "in the style of" not in caption

    def test_unknown_shape_falls_back_to_implants(self):
        caption = build_caption(dict(BASE_META, shape="square", profile="high"))
        assert "with 350 cc implants, using" in caption


class TestSizeLanguage:
    @pytest.mark.parametrize(
        ("cc", "phrase"),
        [
            (100, "a subtle increase of roughly half to one cup size"),
            (249, "a subtle increase of roughly half to one cup size"),
            (250, "a natural-looking increase of roughly one to one and a half cup sizes"),
            (399, "a natural-looking increase of roughly one to one and a half cup sizes"),
            (400, "a clearly noticeable increase of roughly one and a half to two cup sizes"),
            (549, "a clearly noticeable increase of roughly one and a half to two cup sizes"),
            (550, "a full increase of roughly two to two and a half cup sizes"),
            (699, "a full increase of roughly two to two and a half cup sizes"),
            (700, "a dramatic increase of roughly two and a half or more cup sizes"),
            (1000, "a dramatic increase of roughly two and a half or more cup sizes"),
        ],
    )
    def test_size_buckets(self, cc, phrase):
        assert f"The change should read as {phrase}." in build_caption(dict(BASE_META, volume_cc=cc))
