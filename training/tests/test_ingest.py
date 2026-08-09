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


def ingest_main(raw, staging):
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["ingest.py", str(raw), str(staging)]):
        return ingest.main()
