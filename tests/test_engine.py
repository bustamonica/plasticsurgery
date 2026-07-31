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


class TestNippleHoldout:
    """SPEC rev.7: the nipple-areola complex keeps its size/shape — the patch
    within NIPPLE_HOLD_CM of the nipple translates rigidly forward."""

    def test_nipple_patch_moves_rigidly(self):
        from morphengine.morph.deformation import breast_region, local_frame
        from morphengine.morph.engine import NIPPLE_HOLD_CM

        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        params = ImplantParams(volume_cc=300, base_width_cm=11.1,
                               projection_cm=4.5, shape=Shape.ROUND,
                               placement=Placement.DUAL_PLANE)
        res = MorphEngine().morph(mesh, lm, params)

        frame = local_frame(mesh, lm, "left")
        u, w, _ = frame.coords(mesh.vertices)
        r_cm = np.sqrt(u * u + w * w)
        # planar radius alone defines a cylinder through the torso — intersect
        # with the side's facing region mask (as the engine does)
        idx = np.nonzero(breast_region(mesh, lm, "left")
                         & (r_cm <= NIPPLE_HOLD_CM))[0]
        assert idx.size >= 8

        # rigidity: pairwise distances inside the patch are preserved
        sel = idx[np.linspace(0, idx.size - 1, min(40, idx.size)).astype(int)]
        p0, p1 = mesh.vertices[sel], res.mesh.vertices[sel]
        d0 = np.linalg.norm(p0[:, None, :] - p0[None, :, :], axis=-1)
        d1 = np.linalg.norm(p1[:, None, :] - p1[None, :, :], axis=-1)
        assert np.abs(d0 - d1).max() < 2e-3

        # ...but the patch did ride forward substantially (not frozen in place)
        moved = (res.mesh.vertices - mesh.vertices)[idx].mean(axis=0)
        assert np.linalg.norm(moved) > 1.0

    def test_volume_still_closed_with_holdout(self):
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        params = ImplantParams(volume_cc=300, base_width_cm=11.1,
                               projection_cm=4.5, shape=Shape.ROUND,
                               placement=Placement.DUAL_PLANE)
        res = MorphEngine().morph(mesh, lm, params)
        for side in ("left", "right"):
            assert abs(res.achieved_volume_cc[side] - 300.0) <= max(2.0, 0.015 * 300)


class TestPosteriorDepthGate:
    """rev.9: inner cavity shells (anterior-facing normals, far behind the
    chest wall) must not be displaced — they corrupted volume closure on real
    mpfb2/Anny bodies (+5.7%)."""

    def _mesh_with_inner_shell(self):
        """Fixture torso + fake inner shell: breast-area verts pushed 17 cm
        back (normals recomputed anterior-facing, like mpfb2's cavity cap)."""
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        half = lm.breast_half("left")
        d = np.linalg.norm(mesh.vertices - half.nipple[None, :], axis=1)
        near = np.where(d < 6.0)[0]
        shell_v = mesh.vertices[near].copy()
        shell_v[:, 2] -= 17.0                      # 17 cm behind the wall
        # keep only faces whose three verts are ALL inside the ball, then
        # retarget them to the new (appended) vertices
        in_ball = np.zeros(len(mesh.vertices), dtype=bool)
        in_ball[near] = True
        shell_f = mesh.faces[in_ball[mesh.faces].all(axis=1)].copy()
        # local indices for the standalone shell mesh — concatenate() applies
        # the global offset itself
        remap = {old: i for i, old in enumerate(near)}
        shell_f = np.vectorize(remap.get)(shell_f)
        import trimesh as _t
        combined = _t.util.concatenate([mesh, _t.Trimesh(shell_v, shell_f, process=False)])
        return combined, lm

    def test_inner_shell_excluded_from_mask(self):
        mesh, lm = self._mesh_with_inner_shell()
        params = ImplantParams(volume_cc=350, base_width_cm=10.1,
                               projection_cm=5.2, shape=Shape.ROUND,
                               placement=Placement.SUBMUSCULAR)
        engine = MorphEngine()
        _, _, mask = engine._side_field(mesh, lm, "left", params)
        n_orig = len(mesh.vertices) - 0
        # shell verts are the last appended ones; none may be in the mask
        # (find them by depth: z far below the breast band)
        shell_idx = np.where(mesh.vertices[:, 2] < lm.nipple_left[2] - 12.0)[0]
        assert len(shell_idx) > 0
        assert not mask[shell_idx].any()

    def test_inner_shell_not_displaced_and_volume_closes(self):
        mesh, lm = self._mesh_with_inner_shell()
        params = ImplantParams(volume_cc=350, base_width_cm=10.1,
                               projection_cm=5.2, shape=Shape.ROUND,
                               placement=Placement.SUBMUSCULAR)
        res = MorphEngine().morph(mesh, lm, params)
        shell_idx = np.where(mesh.vertices[:, 2] < lm.nipple_left[2] - 12.0)[0]
        disp = np.linalg.norm(
            res.mesh.vertices[shell_idx] - mesh.vertices[shell_idx], axis=1)
        assert disp.max() < 1e-6
        for side in ("left", "right"):
            assert abs(res.achieved_volume_cc[side] - 350.0) <= max(2.0, 0.015 * 350)

    def test_fixture_region_unchanged_by_gate(self):
        """Regression guard: on the clean fixture the gate excludes nothing in
        the functional displacement zone — every vertex within 4 cm of the
        nipple stays in the mask. (Verts nearer the imf crease are excluded by
        the region ellipse / facing test, independent of the rev.9 gate.)"""
        mesh = synthetic_torso()
        lm = FixtureLandmarkProvider().locate(mesh)
        params = ImplantParams(volume_cc=300, base_width_cm=11.1,
                               projection_cm=4.5, shape=Shape.ROUND,
                               placement=Placement.DUAL_PLANE)
        engine = MorphEngine()
        _, _, mask = engine._side_field(mesh, lm, "left", params)
        half = lm.breast_half("left")
        d = np.linalg.norm(mesh.vertices - half.nipple[None, :], axis=1)
        core = np.where(d < 4.0)[0]
        assert len(core) > 50
        assert mask[core].all()  # entire primary displacement zone intact
