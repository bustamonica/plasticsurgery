"""Schema validation and rejection-path tests for ingest.py."""

import json

import pytest

import ingest


class TestValidateMeta:
    def test_valid_meta_has_no_errors(self, valid_meta, tmp_path):
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    def test_missing_consent_ref_is_rejected(self, valid_meta, tmp_path):
        del valid_meta["consent_ref"]
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        errors = ingest.validate_meta(valid_meta, folder)
        assert any("consent_ref" in e for e in errors)

    @pytest.mark.parametrize("field", ["pair_id", "shape", "volume_cc", "view"])
    def test_each_required_field_is_enforced(self, valid_meta, tmp_path, field):
        del valid_meta[field]
        folder = tmp_path / "clinic01-0001"
        folder.mkdir()
        errors = ingest.validate_meta(valid_meta, folder)
        assert any(field in e for e in errors)

    def test_invalid_shape(self, valid_meta, tmp_path):
        valid_meta["shape"] = "square"
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert any("invalid shape" in e for e in ingest.validate_meta(valid_meta, folder))

    def test_unknown_shape_is_accepted(self, valid_meta, tmp_path):
        # Clinics often do not document round/teardrop; schema allows 'unknown'.
        valid_meta["shape"] = "unknown"
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    def test_invalid_view(self, valid_meta, tmp_path):
        valid_meta["view"] = "back"
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert any("invalid view" in e for e in ingest.validate_meta(valid_meta, folder))

    @pytest.mark.parametrize("volume", [99, 1001, "350", 350.5])
    def test_volume_bounds_and_type(self, valid_meta, tmp_path, volume):
        valid_meta["volume_cc"] = volume
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert any("volume_cc" in e for e in ingest.validate_meta(valid_meta, folder))

    @pytest.mark.parametrize("volume", [100, 1000])
    def test_volume_bounds_inclusive(self, valid_meta, tmp_path, volume):
        valid_meta["volume_cc"] = volume
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    def test_pair_id_must_match_folder_name(self, valid_meta, tmp_path):
        folder = tmp_path / "different-name"
        folder.mkdir()
        errors = ingest.validate_meta(valid_meta, folder)
        assert any("does not match folder name" in e for e in errors)


class TestClothingIsMandatory:
    """`clothing` is optional in the schema but mandatory at ingest.

    407 pairs on disk carry no clothing value; build_caption() reads that as
    clothed and instructs the model to preserve clothing that is not in a nude
    photograph (report `ba-viz-clinic-ask-24` section 4.3). Legacy pairs are
    expected to fail this gate until the clinics' retro-labelling lands.
    """

    def test_absent_clothing_is_rejected(self, valid_meta, tmp_path):
        del valid_meta["clothing"]
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        errors = ingest.validate_meta(valid_meta, folder)
        assert any("clothing" in e for e in errors)

    def test_the_error_says_what_to_do_about_it(self, valid_meta, tmp_path):
        del valid_meta["clothing"]
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        error = next(e for e in ingest.validate_meta(valid_meta, folder) if "clothing" in e)
        assert "'bra', 'nude', 'top'" in error
        assert "BOTH photos" in error

    @pytest.mark.parametrize("value", ["nude", "bra", "top"])
    def test_present_values_are_handled_exactly_as_before(self, valid_meta, tmp_path, value):
        valid_meta["clothing"] = value
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    def test_invalid_clothing_is_rejected(self, valid_meta, tmp_path):
        valid_meta["clothing"] = "swimsuit"
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert any("invalid clothing" in e for e in ingest.validate_meta(valid_meta, folder))


class TestOptionalChartFields:
    """The 2026-08 schema extension: optional, but typo-checked when present."""

    def test_a_fully_specified_chart_row_passes(self, valid_meta, tmp_path):
        valid_meta.update(
            placement="dual-plane", incision="inframammary", height_cm=168,
            weight_kg=57.5, bra_size_before="32B", chest_width_cm=31.5,
            months_post_op=6,
        )
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    @pytest.mark.parametrize(
        "field", ["placement", "incision", "height_cm", "weight_kg", "chest_width_cm"]
    )
    def test_each_new_field_is_optional(self, valid_meta, tmp_path, field):
        assert field not in valid_meta
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert ingest.validate_meta(valid_meta, folder) == []

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("placement", "over-the-muscle"),
            ("incision", "belly-button"),
            ("height_cm", 6),           # feet, not centimetres
            ("weight_kg", "57"),        # string, not a number
            ("chest_width_cm", 315),    # millimetres, not centimetres
            ("months_post_op", -1),
        ],
    )
    def test_out_of_contract_values_are_rejected(self, valid_meta, tmp_path, field, value):
        valid_meta[field] = value
        folder = tmp_path / valid_meta["pair_id"]
        folder.mkdir()
        assert any(field in e for e in ingest.validate_meta(valid_meta, folder))


class TestIngestMain:
    def run_ingest(self, tmp_path, capsys):
        staging = tmp_path / "staging"
        rc = ingest_main(tmp_path / "raw", staging)
        return rc, staging, capsys.readouterr().out

    def test_valid_pair_is_accepted(self, make_pair, valid_meta, tmp_path, capsys):
        make_pair("clinic01-0001", valid_meta)
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 0
        assert "1 accepted, 0 rejected" in out
        staged = staging / "clinic01-0001"
        assert (staged / "before.jpg").exists()
        assert (staged / "after.jpg").exists()
        assert (staged / "meta.json").exists()

    def test_pair_without_consent_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        del valid_meta["consent_ref"]
        make_pair("clinic01-0001", valid_meta)
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "consent_ref" in out
        assert not (staging / "clinic01-0001").exists()

    def test_duplicate_pixel_hash_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        make_pair("clinic01-0001", valid_meta)
        meta2 = dict(valid_meta, pair_id="clinic01-0002")
        # Identical before-image pixels as pair 1 -> dedup must catch it.
        make_pair("clinic01-0002", meta2)
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 0  # pair 1 still accepted
        assert "duplicate" in out
        assert "1 accepted, 1 rejected" in out
        assert not (staging / "clinic01-0002").exists()

    def test_tiny_image_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        make_pair("clinic01-0001", valid_meta, size=(256, 256))
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "too small" in out
        assert not (staging / "clinic01-0001").exists()

    def test_418px_image_is_accepted(self, make_pair, valid_meta, tmp_path, capsys):
        # drkolker publishes much of its gallery at 418px; MIN_DIMENSION was
        # lowered to 400 to keep those consented pairs (PR #2 ruling).
        make_pair("clinic01-0001", valid_meta, size=(418, 418))
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 0
        assert "1 accepted, 0 rejected" in out

    def test_invalid_json_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        folder = make_pair("clinic01-0001", valid_meta)
        (folder / "meta.json").write_text("{not json")
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "not valid JSON" in out

    def test_empty_raw_root_fails(self, tmp_path, capsys):
        (tmp_path / "raw").mkdir()
        rc, _, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "No pairs found" in out

    def test_pair_without_clothing_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        del valid_meta["clothing"]
        make_pair("clinic01-0001", valid_meta)
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "clothing" in out
        assert not (staging / "clinic01-0001").exists()

    def test_censored_pair_is_rejected(self, make_pair, valid_meta, tmp_path, capsys):
        import cv2

        from conftest import make_torso

        before, after = make_torso(seed=11), make_torso(seed=12)
        h, w = after.shape[:2]
        cv2.circle(after, (int(w * 0.40), int(h * 0.38)), int(min(h, w) * 0.06),
                   (219, 178, 133), -1)  # sixsurgery-style opaque nipple circle
        make_pair("clinic01-0001", valid_meta, images=(before, after))
        rc, staging, out = self.run_ingest(tmp_path, capsys)
        assert rc == 1
        assert "censored or annotated" in out
        assert "do not crop around it" in out
        assert not (staging / "clinic01-0001").exists()

    def test_uncensored_photographic_pair_is_accepted(
        self, make_pair, valid_meta, tmp_path, capsys
    ):
        from conftest import make_torso

        make_pair("clinic01-0001", valid_meta, images=(make_torso(seed=21), make_torso(seed=22)))
        rc, _, out = self.run_ingest(tmp_path, capsys)
        assert rc == 0
        assert "1 accepted, 0 rejected" in out


def ingest_main(raw, staging):
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["ingest.py", str(raw), str(staging)]):
        return ingest.main()
