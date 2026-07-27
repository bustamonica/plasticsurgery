"""Tests for MorphEngine behavior + guardrails (SPEC §3 core)."""

import numpy as np
import pytest

from morphengine.geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
from morphengine.geometry.measure import upper_pole_slope
from morphengine.implants.schema import ImplantParams, Placement, Shape
from morphengine.morph.engine import MorphEngine
from morphengine.morph.guardrails import check_compatibility

ROUND_350 = ImplantParams(volume_cc=350, base_width_cm=12.0, projection_cm=4.7,
                          shape=Shape.ROUND, placement=Placement.SUBMUSCULAR)


def run(mesh, params):
    lm = FixtureLandmarkProvider().locate(mesh)
    return MorphEngine().morph(mesh, lm, params)


class TestDeterminism:
    def test_identical_runs(self):
        r1 = run(synthetic_torso(), ROUND_350)
        r2 = run(synthetic_torso(), ROUND_350)
        np.testing.assert_array_equal(r1.deformation, r2.deformation)


class TestSymmetry:
    def test_achieved_volumes_symmetric(self):
        r = run(synthetic_torso(), ROUND_350)
        l, rr = r.achieved_volume_cc["left"], r.achieved_volume_cc["right"]
        assert abs(l - rr) / max(l, rr) < 0.05


class TestGuardrails:
    def test_oversized_base_width_clamps(self):
        narrow = synthetic_torso(chest_width_cm=24.0)  # hemi bound = 10.8
        lm = FixtureLandmarkProvider().locate(narrow)
        g = check_compatibility(lm, ROUND_350)         # base 12.0 > 10.8
        assert g.clamped and not g.ok and g.warnings
        assert g.clamped_params.base_width_cm == pytest.approx(10.8)
        # engine uses the clamped params
        r = MorphEngine().morph(narrow, lm, ROUND_350)
        assert r.guardrails.clamped
        assert r.measurements["left"]["base_width_cm"] < 12.0

    def test_clean_params_ok(self):
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        assert check_compatibility(lm, ROUND_350).ok

    def test_volume_out_of_range_warns(self):
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        big = ROUND_350.model_copy(update={"volume_cc": 1400.0})
        g = check_compatibility(lm, big)
        assert not g.ok and any("volume" in w for w in g.warnings)


class TestSlideGuardrail:
    def test_extreme_expansion_warns_and_does_not_crash(self):
        # 100 cc with 15 cm base width on a 12 cm base: the in-plane
        # expansion alone exceeds the requested volume (verifier crash case)
        extreme = ImplantParams(volume_cc=100, base_width_cm=15.0,
                                projection_cm=2.0, shape=Shape.ROUND,
                                placement=Placement.SUBMUSCULAR)
        r = run(synthetic_torso(), extreme)
        assert not r.guardrails.ok
        assert any("mismatch" in w or "dominates" in w or "skipped" in w
                   for w in r.guardrails.warnings)
        assert np.isfinite(r.deformation).all()
        assert r.measurements["left"]["base_width_cm"] > 0


class TestPlacement:
    def test_submuscular_emptier_upper_pole(self):
        # upper_pole_slope measures drop-off from the apex: submuscular
        # (muscle-compressed upper pole) drops FASTER → larger slope value
        submusc = run(synthetic_torso(), ROUND_350)
        subgland = run(synthetic_torso(),
                       ROUND_350.model_copy(update={"placement": Placement.SUBGLANDULAR}))
        lm = FixtureLandmarkProvider().locate(synthetic_torso())
        s_musc = upper_pole_slope(submusc.mesh, lm, "left")
        s_gland = upper_pole_slope(subgland.mesh, lm, "left")
        assert s_musc > s_gland


class TestAnatomical:
    def test_teardrop_shifts_volume_inferior(self):
        from morphengine.morph.deformation import breast_region, local_frame
        anat = ROUND_350.model_copy(update={"shape": Shape.ANATOMICAL})
        base_mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(base_mesh)
        # frame from the PRE-morph mesh: landmark-anchored and comparable
        frame = local_frame(base_mesh, lm, "left")

        def inf_minus_sup(res):
            mask = breast_region(res.mesh, lm, "left", margin=1.0)
            _, w, t = frame.coords(res.mesh.vertices[mask])
            return t[w < 0].mean() - t[w > 0].mean()

        r_round = MorphEngine().morph(synthetic_torso(), lm, ROUND_350)
        r_anat = MorphEngine().morph(synthetic_torso(), lm, anat)
        assert inf_minus_sup(r_anat) > inf_minus_sup(r_round)
