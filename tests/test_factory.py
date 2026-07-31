"""Tests for datafactory.factory.DatasetFactory (SPEC §M1.6).

The real SoftwareRenderer (SPEC §M1.2) is a sibling module built in parallel;
these tests inject a StubRenderer with the same
``Renderer(camera).render(mesh) -> RenderResult`` contract that returns cheap
synthetic arrays. They validate the factory's own logic — pair pipeline,
guardrail gating, manifest, files, determinism — not pixels.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from morphengine.datafactory.factory import DatasetFactory
from morphengine.geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
from morphengine.implants.db import ImplantDB

# ---------------------------------------------------------------- stub renderer


@dataclass
class StubRenderResult:
    rgb: np.ndarray      # (H,W,3) uint8
    depth: np.ndarray    # (H,W) float32, bg NaN
    normal: np.ndarray   # (H,W,3) float32, bg 0
    mask: np.ndarray     # (H,W) bool


class StubRenderer:
    """Cheap orthographic-projected vertex mask; constant depth; zeros
    normals; flat gray rgb. Deterministic."""

    def __init__(self, camera):
        self.camera = camera

    def render(self, mesh) -> StubRenderResult:
        size = int(self.camera.image_size)
        xy = np.asarray(mesh.vertices)[:, :2]
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        span = np.maximum(hi - lo, 1e-9)
        uv = ((xy - lo) / span * (size - 1)).astype(int)

        mask = np.zeros((size, size), dtype=bool)
        mask[uv[:, 1], uv[:, 0]] = True
        depth = np.full((size, size), np.nan, dtype=np.float32)
        depth[mask] = 1.0
        normal = np.zeros((size, size, 3), dtype=np.float32)
        rgb = np.full((size, size, 3), 128, dtype=np.uint8)
        return StubRenderResult(rgb=rgb, depth=depth, normal=normal, mask=mask)


class StubFactory(DatasetFactory):
    """DatasetFactory that never imports the (parallel-built) render module."""

    def _camera(self, mesh, camera_kind, image_size):
        return SimpleNamespace(kind=camera_kind, image_size=image_size)


def make_factory(out_dir) -> StubFactory:
    return StubFactory(out_dir, renderer_cls=StubRenderer, resolution=4)


# ------------------------------------------------------------------ tests

REQUIRED_ROW_KEYS = {
    "pair_id", "sku_id", "brand", "product_line", "profile_class",
    "profile_label", "volume_cc", "base_width_cm", "projection_cm", "shape",
    "placement", "camera_kind", "image_size", "body_params", "engine",
    "files", "prompt", "seed",
}
REQUIRED_ENGINE_KEYS = {"achieved_volume_cc", "ok", "warnings"}
REQUIRED_FILE_KEYS = {"before", "after", "depth_before", "depth_after",
                      "normal_after", "mask_before"}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("dataset")
    rows = make_factory(out).generate(3, seed=42, image_size=128)
    return out, rows


def test_generates_requested_count(generated):
    _, rows = generated
    assert len(rows) == 3
    ids = [r["pair_id"] for r in rows]
    assert len(set(ids)) == 3


def test_all_files_exist(generated):
    out, rows = generated
    assert (out / "manifest.jsonl").is_file()
    for row in rows:
        assert set(row["files"]) == REQUIRED_FILE_KEYS
        for rel in row["files"].values():
            path = out / rel
            assert path.is_file(), f"missing {path}"
            assert not Path(rel).is_absolute()
    # channel dtypes / shapes
    row = rows[0]
    depth = np.load(out / row["files"]["depth_before"])
    normal = np.load(out / row["files"]["normal_after"])
    mask = np.load(out / row["files"]["mask_before"])
    assert depth.dtype == np.float32 and depth.shape == (128, 128)
    assert normal.dtype == np.float32 and normal.shape == (128, 128, 3)
    assert mask.dtype == bool and mask.shape == (128, 128)
    assert np.isnan(depth[~mask]).all()


def test_manifest_rows_have_required_keys(generated):
    out, rows = generated
    lines = (out / "manifest.jsonl").read_text().strip().splitlines()
    assert len(lines) == len(rows)
    for line, row in zip(lines, rows):
        parsed = json.loads(line)
        assert parsed == row  # manifest content == returned rows
        assert set(row) == REQUIRED_ROW_KEYS
        assert set(row["engine"]) == REQUIRED_ENGINE_KEYS
        assert set(row["engine"]["achieved_volume_cc"]) == {"left", "right"}
        assert row["image_size"] == 128
        assert row["seed"] == 42
        assert row["prompt"].startswith("photorealistic breast augmentation result")
        assert f"{row['volume_cc']:g} cc" in row["prompt"]
        assert row["shape"] in row["prompt"]
        assert row["placement"] in row["prompt"]
        assert row["camera_kind"] in row["prompt"]


def test_engine_ok_and_volume_closure(generated):
    _, rows = generated
    for row in rows:
        assert row["engine"]["ok"] is True
        assert row["engine"]["warnings"] == []
        for side in ("left", "right"):
            achieved = row["engine"]["achieved_volume_cc"][side]
            # Engine closure itself converges within volume_tol_cc (2 cc).
            # The post-hoc per-side achieved is re-measured after BOTH sides
            # are applied; at the test fixture resolution (4) mesh
            # quantization + icosphere x-asymmetry add up to ~3% error on
            # large implants (SPEC rev.3 known limitation), hence the band.
            tol = max(2.0, 0.03 * row["volume_cc"])
            assert abs(achieved - row["volume_cc"]) <= tol + 1e-6


def test_determinism_two_runs(tmp_path):
    rows_a = make_factory(tmp_path / "a").generate(3, seed=42, image_size=128)
    rows_b = make_factory(tmp_path / "b").generate(3, seed=42, image_size=128)
    assert rows_a == rows_b  # identical manifest rows (dicts)
    manifest_a = (tmp_path / "a" / "manifest.jsonl").read_bytes()
    manifest_b = (tmp_path / "b" / "manifest.jsonl").read_bytes()
    assert manifest_a == manifest_b


def test_manifest_written_atomically(tmp_path):
    out = tmp_path / "ds"
    make_factory(out).generate(2, seed=7, image_size=128)
    assert (out / "manifest.jsonl").is_file()
    assert not (out / "manifest.jsonl.tmp").exists()


def test_guardrail_clamp_gates_pair(tmp_path):
    """An implant wider than 90% of the hemithorax clamps → make_pair None."""
    db = ImplantDB.from_json()
    wide = next(s for s in db.find() if s.base_width_cm > 0.90 * 30.0 / 2.0)
    mesh = synthetic_torso(chest_width_cm=30.0, resolution=4)
    lm = FixtureLandmarkProvider().locate(mesh)
    factory = make_factory(tmp_path / "gated")
    row = factory.make_pair(mesh, lm, {"chest_width_cm": 30.0}, wide,
                            wide.placement_options[0], "front")
    assert row is None  # clamped pair skipped, never emitted
    assert not list((tmp_path / "gated").rglob("*.png"))


def test_append_continues_dataset(tmp_path):
    """append=True continues indices and manifest instead of restarting."""
    out = tmp_path / "ds"
    f = make_factory(out)
    rows1 = f.generate(2, seed=42, image_size=128)
    rows2 = f.generate(1, seed=43, image_size=128, append=True)
    manifest = (out / "manifest.jsonl").read_text().strip().split("\n")
    assert len(manifest) == 3
    ids = [json.loads(line)["pair_id"] for line in manifest]
    assert ids[0].startswith("00000_") and ids[1].startswith("00001_")
    assert ids[2].startswith("00002_")  # index continues, no collision
    assert len(set(ids)) == 3
    assert f.report_["total"] == 3 and f.report_["written"] == 1


def test_append_without_prior_is_plain_run(tmp_path):
    out = tmp_path / "ds"
    f = make_factory(out)
    f.generate(2, seed=42, image_size=128, append=True)
    assert len((out / "manifest.jsonl").read_text().strip().split("\n")) == 2


def test_custom_body_sampler_injection(tmp_path):
    """generate() accepts any object with the BodySampler.sample() contract."""
    class ConstantSampler:
        def __init__(self):
            self.calls = 0

        def sample(self):
            self.calls += 1
            mesh = synthetic_torso(resolution=4)
            lm = FixtureLandmarkProvider().locate(mesh)
            return mesh, lm, {"provider": "constant"}

    sampler = ConstantSampler()
    rows = make_factory(tmp_path / "ds").generate(
        2, seed=42, image_size=128, body_sampler=sampler)
    assert len(rows) == 2
    assert sampler.calls >= 2
    assert all(r["pair_id"] for r in rows)


@pytest.mark.skipif(
    not __import__("morphengine.geometry.anny_body", fromlist=["anny_available"]).anny_available(),
    reason="anny not installed")
def test_anny_sampler_end_to_end(tmp_path):
    """Factory v2: one real-body pair through the full pipeline."""
    from morphengine.datafactory.bodies import AnnyBodySampler
    rows = make_factory(tmp_path / "ds").generate(
        1, seed=42, image_size=128, body_sampler=AnnyBodySampler(seed=42))
    assert len(rows) == 1
    row = rows[0]
    assert row["body_params"]["provider"] == "anny"
    tol = max(2.0, 0.03 * row["volume_cc"])
    assert abs(row["engine"]["achieved_volume_cc"]["left"] - row["volume_cc"]) <= tol
