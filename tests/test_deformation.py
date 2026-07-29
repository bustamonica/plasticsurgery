"""Unit tests for morph.deformation (SPEC §3 core)."""

import numpy as np
import pytest

from morphengine.geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
from morphengine.morph.deformation import (anatomical_weight, breast_region,
                                           radial_falloff, placement_falloff)


class TestRadialFalloff:
    def test_endpoints(self):
        assert radial_falloff(np.array([0.0]))[0] == pytest.approx(1.0)
        assert radial_falloff(np.array([1.0]))[0] == pytest.approx(0.0, abs=1e-12)

    def test_monotonic_decreasing(self):
        r = np.linspace(0, 1, 50)
        f = radial_falloff(r)
        assert np.all(np.diff(f) <= 1e-12)

    def test_clamped_outside(self):
        assert radial_falloff(np.array([1.7]))[0] == pytest.approx(0.0, abs=1e-12)
        assert radial_falloff(np.array([-0.3]))[0] == pytest.approx(1.0)

    def test_dome_profile(self):
        assert radial_falloff(np.array([0.0]), kind="dome")[0] == pytest.approx(1.0)
        assert radial_falloff(np.array([1.0]), kind="dome")[0] == pytest.approx(0.0)
        # dome sits above cosine in mid-range (more volume per apex)
        r = np.linspace(0.05, 0.95, 20)
        assert np.all(radial_falloff(r, kind="dome") > radial_falloff(r, kind="cosine"))

    def test_bad_kind(self):
        with pytest.raises(ValueError):
            radial_falloff(np.array([0.5]), kind="nope")


class TestPlacementFalloff:
    # volume-neutral redistribution needs a distribution, not single points:
    # build a synthetic (theta, r) disk with a dome-like base profile
    rng_disk = np.linspace(0.0, 1.0, 60)
    TH, RR = np.meshgrid(np.linspace(-np.pi, np.pi, 360), rng_disk)
    TH, RR = TH.ravel(), RR.ravel()
    BASE = np.sqrt(np.clip(1.0 - RR**2, 0.0, None)) + 1e-6

    def _mult(self, placement):
        out = placement_falloff(self.TH, self.RR, placement, self.BASE)
        return out / self.BASE  # recover the multiplier

    def test_apex_preserving(self):
        for p in ("submuscular", "subglandular", "dual-plane"):
            m = self._mult(p)
            assert m[self.RR == 0.0] == pytest.approx(np.ones(int((self.RR == 0).sum())))

    def test_volume_neutral(self):
        for p in ("submuscular", "subglandular", "dual-plane"):
            out = placement_falloff(self.TH, self.RR, p, self.BASE)
            assert out.sum() == pytest.approx(self.BASE.sum(), rel=1e-6)

    def test_superior_ordering(self):
        sup = np.sin(self.TH) > 0.3
        m_musc = self._mult("submuscular")[sup].mean()
        m_dual = self._mult("dual-plane")[sup].mean()
        m_gland = self._mult("subglandular")[sup].mean()
        assert m_musc < m_dual < m_gland
        # and the inferior pole compensates (redistribution)
        inf = np.sin(self.TH) < -0.3
        assert self._mult("submuscular")[inf].mean() > \
               self._mult("subglandular")[inf].mean()

    def test_bad_placement(self):
        with pytest.raises(ValueError):
            placement_falloff(self.TH, self.RR, "subdermal", self.BASE)


class TestAnatomicalWeight:
    def test_inferior_favored(self):
        assert anatomical_weight(np.array([-np.pi / 2]))[0] > \
               anatomical_weight(np.array([np.pi / 2]))[0]

    def test_bounds(self):
        theta = np.linspace(-np.pi, np.pi, 200)
        w = anatomical_weight(theta)
        assert w.min() >= 0.7 - 1e-9 and w.max() <= 1.3 + 1e-9


class TestBreastRegion:
    def test_region_contains_mound_excludes_back(self):
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        mask = breast_region(mesh, lm, "left", margin=1.0)
        # apex vertex (near nipple, elevated) must be in region
        apex_idx = int(np.argmax(mesh.vertices[:, 2]))
        assert mask[apex_idx]
        # posterior pole must not be
        back_idx = int(np.argmin(mesh.vertices[:, 2]))
        assert not mask[back_idx]
        # region size sane
        assert 50 < mask.sum() < len(mesh.vertices) // 3


class TestSkirtedCap:
    """SPEC rev.8: (1-r^2)^beta dome with C1-smooth skirt rim (no ring crease)."""

    def test_apex_anchor_and_rim_zero(self):
        from morphengine.morph.deformation import skirted_cap
        r = np.linspace(0.0, 1.0, 501)
        for beta in (0.05, 0.3, 0.57, 0.9, 1.0, 1.6):
            p = skirted_cap(r, beta)
            assert p[0] == pytest.approx(1.0)
            assert p[-1] == pytest.approx(0.0, abs=1e-12)

    def test_beta_ge_1_unchanged(self):
        from morphengine.morph.deformation import skirted_cap
        r = np.linspace(0.0, 1.0, 301)
        assert np.allclose(skirted_cap(r, 1.4), np.clip(1 - r * r, 0, None) ** 1.4)

    def test_core_matches_beta_dome(self):
        from morphengine.morph.deformation import SKIRT_RC, skirted_cap
        r = np.linspace(0.0, SKIRT_RC, 200)
        assert np.allclose(skirted_cap(r, 0.5), (1 - r * r) ** 0.5)

    def test_rim_cliff_eliminated(self):
        """Value 1% inside the rim: new profile ~0, old profile a visible cliff."""
        from morphengine.morph.deformation import skirted_cap
        r_edge = np.array([0.99])
        for beta in (0.05, 0.3, 0.57, 0.9):
            new = skirted_cap(r_edge, beta)[0]
            old = (1 - r_edge[0] ** 2) ** beta
            assert new < 0.01, f"beta={beta}: residual rim value {new}"
            assert new < 0.1 * old, f"beta={beta}: {new} vs old {old}"

    def test_rim_slope_smooth_vs_old(self):
        from morphengine.morph.deformation import skirted_cap
        r = np.linspace(0.995, 1.0, 51)
        for beta in (0.05, 0.3, 0.57, 0.9):
            new_slope = np.abs(np.gradient(skirted_cap(r, beta), r)).max()
            old_slope = np.abs(np.gradient(np.clip(1 - r * r, 0, None) ** beta, r)).max()
            assert new_slope < 0.1 * old_slope

    def test_monotone_and_positive(self):
        from morphengine.morph.deformation import skirted_cap
        r = np.linspace(0.0, 1.0, 2001)
        for beta in (0.05, 0.3, 0.57, 0.9):
            p = skirted_cap(r, beta)
            assert (p >= 0).all()
            assert np.diff(p).max() <= 1e-9

    def test_c1_continuity_at_rc(self):
        from morphengine.morph.deformation import SKIRT_RC, skirted_cap
        eps = 1e-4
        for beta in (0.05, 0.3, 0.57, 0.9):
            d_left = (skirted_cap(np.array([SKIRT_RC]), beta)[0]
                      - skirted_cap(np.array([SKIRT_RC - eps]), beta)[0]) / eps
            d_right = (skirted_cap(np.array([SKIRT_RC + eps]), beta)[0]
                       - skirted_cap(np.array([SKIRT_RC]), beta)[0]) / eps
            assert d_left == pytest.approx(d_right, rel=0.05, abs=1e-3)
