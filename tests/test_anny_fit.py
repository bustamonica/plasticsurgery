"""Tests for geometry.anny_fit (M3 mesh->Anny converter).

Pure-numpy frame tests run everywhere; fitting tests need the optional anny
stack and take ~1-3 min total (model build is disk-cached).
"""

import numpy as np
import pytest

from morphengine.geometry.anny_body import to_engine_frame
from morphengine.geometry.anny_fit import (
    AnnyMeshFitter,
    anny_available,
    to_anny_frame,
)
from morphengine.implants.schema import ImplantParams, Placement, Shape

requires_anny = pytest.mark.skipif(not anny_available(), reason="anny not installed")

GT_PHENOTYPE = dict(gender=1.0, age=0.35, weight=0.65, height=0.55, muscle=0.45)
UHP_350 = ImplantParams(volume_cc=350, base_width_cm=10.1, projection_cm=5.2,
                        shape=Shape.ROUND, placement=Placement.SUBMUSCULAR)


class TestFrameHelpers:
    def test_roundtrip(self):
        rng = np.random.default_rng(0)
        v_m = rng.normal(size=(50, 3))            # anny frame, meters
        assert np.allclose(to_anny_frame(to_engine_frame(v_m)), v_m)
        v_cm = rng.normal(size=(50, 3)) * 100.0   # engine frame, cm
        assert np.allclose(to_engine_frame(to_anny_frame(v_cm)), v_cm)


def _target_body_m(phenotype: dict, noise_mm: float = 0.0,
                   seed: int = 0) -> np.ndarray:
    """Anny ground-truth body in the anny frame (meters), identity pose."""
    import roma
    import torch
    from anny import create_fullbody_model
    model = create_fullbody_model(triangulate_faces=True)
    n_bones = len(model.bone_labels)
    rot = torch.eye(3, dtype=model.dtype).expand(n_bones, 3, 3)
    trans = torch.zeros((n_bones, 3), dtype=model.dtype)
    pose = roma.Rigid(rot, trans)[None].to_homogeneous()
    with torch.no_grad():
        v = model(pose_parameters=pose, phenotype_kwargs=phenotype)["vertices"]
    v = v[0].cpu().numpy()
    if noise_mm > 0:
        rng = np.random.default_rng(seed)
        v = v + rng.normal(scale=noise_mm / 1000.0, size=v.shape)
    return v


@requires_anny
class TestAnnyMeshFitter:
    def test_roundtrip_recovers_surface_and_shape(self):
        v_t = _target_body_m(GT_PHENOTYPE)
        res = AnnyMeshFitter(max_n_iters=5).fit(v_t)
        assert res.pve_mm < 6.0                     # sub-6mm surface fidelity
        assert res.phenotype["gender"] == 1.0       # fixed, not drifted
        assert abs(res.phenotype["weight"] - 0.65) < 0.2
        assert abs(res.phenotype["height"] - 0.55) < 0.2
        assert res.vertices_m.shape == v_t.shape

    def test_noisy_target(self):
        """1.5 mm scan-like noise must not break the fit."""
        v_t = _target_body_m(GT_PHENOTYPE, noise_mm=1.5)
        res = AnnyMeshFitter(max_n_iters=5).fit(v_t)
        assert res.pve_mm < 8.0

    def test_fit_engine_mesh_landmarks_and_morph(self):
        """Full M3 path: engine-frame target -> fit -> landmarks -> morph."""
        from morphengine.geometry.anny_body import AnnyBodyProvider
        from morphengine.morph.engine import MorphEngine
        target, _, _ = AnnyBodyProvider(phenotype=GT_PHENOTYPE).sample()
        mesh, lm, meta = AnnyMeshFitter(max_n_iters=5).fit_engine_mesh(target)
        assert meta["provider"] == "anny-fit"
        assert meta["pve_mm"] < 6.0
        assert 24.0 <= lm.chest_width_cm <= 44.0
        R = (lm.lateral_left[0] - lm.medial_left[0]) / 2.0
        assert 3.0 <= R <= 9.0  # 3.0 = adapter's midline-clearance floor
        assert lm.medial_left[0] >= 0.9  # dome never reaches the midline
        res = MorphEngine().morph(mesh, lm, UHP_350)
        for side in ("left", "right"):
            assert abs(res.achieved_volume_cc[side] - 350.0) <= max(2.0, 0.015 * 350)
