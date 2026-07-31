"""Tests for the Anny real-body adapter (geometry.anny_body, M2/GATE 1).

Pure-numpy tests run everywhere; model-backed tests are skipped unless the
optional ``anny`` package (and torch/warp/roma) is installed.
"""

import numpy as np
import pytest

from morphengine.geometry.anny_body import (
    AnnyBodyProvider,
    anny_available,
    to_engine_frame,
)
from morphengine.implants.schema import ImplantParams, Placement, Shape

requires_anny = pytest.mark.skipif(not anny_available(), reason="anny not installed")

UHP_350 = ImplantParams(volume_cc=350, base_width_cm=10.1, projection_cm=5.2,
                        shape=Shape.ROUND, placement=Placement.SUBMUSCULAR)


class TestFrameConversion:
    def test_axes_and_units(self):
        # anny: meters, +x left, +z up, -y anterior.
        v_m = np.array([
            [1.0, 0.0, 0.0],   # +x left
            [0.0, -1.0, 0.0],  # -y anterior
            [0.0, 0.0, 1.0],   # +z up
        ])
        v_cm = to_engine_frame(v_m)
        assert v_cm[0, 0] == pytest.approx(100.0)   # left stays +x
        assert v_cm[1, 2] == pytest.approx(100.0)   # anterior becomes +z
        assert v_cm[2, 1] == pytest.approx(100.0)   # up becomes +y


@requires_anny
class TestAnnyBodyProvider:
    def test_mesh_and_landmarks_sane(self):
        mesh, lm, meta = AnnyBodyProvider().sample()
        assert meta["watertight"]
        assert len(mesh.vertices) > 13_000
        # symmetric nipples, sternum behind nipples, clavicle above nipples
        assert lm.nipple_left[0] == pytest.approx(-lm.nipple_right[0], abs=0.5)
        assert lm.sternum_mid[2] < lm.nipple_left[2]
        assert lm.clavicle_mid[1] > lm.nipple_left[1] + 5.0
        # physiological ranges (defaults phenotype): slim-average female frame
        assert 24.0 <= lm.chest_width_cm <= 44.0
        R_left = (lm.lateral_left[0] - lm.medial_left[0]) / 2.0
        assert 3.5 <= R_left <= 9.0

    def test_deterministic(self):
        m1, lm1, _ = AnnyBodyProvider().sample()
        m2, lm2, _ = AnnyBodyProvider().sample()
        assert np.allclose(m1.bounds, m2.bounds)
        assert np.allclose(lm1.nipple_left, lm2.nipple_left)

    def test_inner_shell_not_displaced_and_volume_closes(self):
        """GATE 1 invariant on the real body: the mpfb2 inner cavity shell
        (anterior-facing, far behind the wall) must not move; 350 cc closes."""
        from morphengine.morph.engine import MorphEngine
        mesh, lm, _ = AnnyBodyProvider().sample()
        res = MorphEngine().morph(mesh, lm, UHP_350)
        shell = np.where(mesh.vertices[:, 2] < lm.nipple_left[2] - 12.0)[0]
        assert len(shell) > 0
        disp = np.linalg.norm(res.mesh.vertices[shell] - mesh.vertices[shell], axis=1)
        assert disp.max() < 1e-6
        for side in ("left", "right"):
            assert abs(res.achieved_volume_cc[side] - 350.0) <= max(2.0, 0.015 * 350)


@requires_anny
class TestAnnyBodySampler:
    def test_seeded_determinism(self):
        from morphengine.datafactory.bodies import AnnyBodySampler
        m1, lm1, p1 = AnnyBodySampler(seed=7).sample()
        m2, lm2, p2 = AnnyBodySampler(seed=7).sample()
        assert p1 == p2
        assert np.allclose(m1.bounds, m2.bounds)
        assert np.allclose(lm1.clavicle_mid, lm2.clavicle_mid)

    def test_draws_vary_and_stay_in_range(self):
        from morphengine.datafactory.bodies import (
            ANNY_PHENOTYPE_RANGES,
            AnnyBodySampler,
        )
        s = AnnyBodySampler(seed=3)
        samples = [s.sample() for _ in range(2)]
        (m1, lm1, p1), (m2, lm2, p2) = samples
        assert p1["phenotype"] != p2["phenotype"]
        for p in (p1, p2):
            assert p["provider"] == "anny"
            assert p["phenotype"]["gender"] == 1.0
            for key, (lo, hi) in ANNY_PHENOTYPE_RANGES.items():
                assert lo <= p["phenotype"][key] <= hi
        for lm in (lm1, lm2):
            assert 24.0 <= lm.chest_width_cm <= 44.0
            assert lm.clavicle_mid[1] > 30.0
