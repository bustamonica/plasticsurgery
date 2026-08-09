"""Caption assembly tests for build_dataset.build_caption, incl. clothing variants."""

import pytest

from build_dataset import build_caption

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
        "Edit this photo to simulate the outcome of breast augmentation surgery "
        "with 350 cc round implants giving even fullness and visible roundness "
        "in the upper breast, using a balanced moderate-plus profile, in the "
        "style of soft cohesive silicone gel implants with a natural feel. "
        "The change should read as a natural-looking increase of roughly one "
        "to one and a half cup sizes. Keep the person's identity, pose, skin "
        "tone, clothing, lighting and background exactly the same."
    )


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

    def test_unknown_profile_falls_back_to_balanced(self):
        caption = build_caption(dict(BASE_META, profile="unknown"))
        assert "using a balanced profile." in caption

    def test_unknown_shape_falls_back_to_implants(self):
        caption = build_caption(dict(BASE_META, shape="square"))
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
