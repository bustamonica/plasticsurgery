"""MorphEngine: implant params → deformed mesh (SPEC §2.6, rev.3/4).

Per-breast algorithm:
  1. guardrail check (once, params-level)
  2. landmark-anchored local frame + region selection (margin ≥ s_bw)
  3. in-plane base-width scaling about the nipple (clamped [0.8, 1.5])
  4. dome field: h·(1−r²)^β with per-implant fullness β = πa²h/V − 1 along
     local surface normals; placement + anatomical multipliers redistribute
     (volume-neutral, apex-preserving)
  5. slide-share guardrail (rev.4): if the in-plane expansion alone accounts
     for the requested volume, warn (implant/chest mismatch) and skip the dome
  6. volume closure on a uniform field multiplier m (bisection); volume is
     the hard constraint — measured projection is the volume-consistent
     output (rated projection ≠ in-vivo projection gain)
  7. both sides independently, symmetric params (v0); one implant of
     `volume_cc` per breast
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from ..geometry.landmarks import ChestLandmarks
from ..geometry.measure import displaced_volume_cc, measure_projection_cm
from ..implants.schema import ImplantParams, Shape
from .deformation import (anatomical_weight, breast_region, local_frame,
                          placement_falloff, region_axes, skirted_cap)
from .guardrails import GuardrailResult, check_compatibility

REGION_MARGIN = 1.0
_INPLANE_SCALE_BOUNDS = (0.8, 1.5)
# Nipple-areola complex (rev.7): the NAC keeps its size/shape — within
# NIPPLE_HOLD_CM of the nipple the patch translates rigidly; smoothstep
# blend annulus out to NIPPLE_BLEND_CM (~4 cm areola diameter is typical).
NIPPLE_HOLD_CM = 2.0
NIPPLE_BLEND_CM = 4.0


@dataclass
class MorphResult:
    mesh: trimesh.Trimesh
    deformation: np.ndarray          # (N,3) per-vertex displacement applied
    achieved_volume_cc: dict         # {"left": float, "right": float}
    measurements: dict               # per side: projection_cm, base_width_cm
    guardrails: GuardrailResult


class MorphEngine:
    def __init__(self, volume_tol_cc: float = 2.0, max_iters: int = 25):
        self.volume_tol_cc = volume_tol_cc
        self.max_iters = max_iters

    # -- internal ---------------------------------------------------------

    def _side_field(self, mesh, lm, side, params):
        """Build (inplane_disp, normal_field, region_mask) for one side."""
        frame = local_frame(mesh, lm, side)
        half = lm.breast_half(side)
        landmark_bw = (abs((half.lateral - half.nipple) @ frame.x_hat)
                       + abs((half.medial - half.nipple) @ frame.x_hat))
        s_bw = float(np.clip(params.base_width_cm / landmark_bw,
                             *_INPLANE_SCALE_BOUNDS))
        # region must contain the full SCALED dome support (rev.3): a dome
        # wider than the existing base is otherwise truncated by the mask
        mask = breast_region(mesh, lm, side,
                             margin=max(REGION_MARGIN, s_bw * 1.02))
        # posterior depth gate (rev.9): nothing deeper than the chest wall may
        # move. Real meshes with an inner cavity shell (mpfb2/Anny) have
        # anterior-FACING inner verts that pass the rev.2 facing test and get
        # dragged forward, corrupting volume closure (+5.7% on the Anny body).
        # t = anterior coordinate from the nipple: chest wall ≈ -proj (with
        # slope allowance), inner shell ≈ -17 cm. Gate is relative to the
        # implant with an absolute cap: -min(2*proj + 3, 14).
        _, _, t_depth = frame.coords(mesh.vertices)
        depth_limit = -min(2.0 * params.projection_cm + 3.0, 14.0)
        mask = mask & (t_depth > depth_limit)
        # sagittal guard (rev.10): a breast dome never crosses the midline.
        # On fuller real bodies the apex estimate can sit near the cleavage,
        # letting the region ellipse straddle x=0 — cross-midplane displacement
        # is counted in BOTH midplane-split half-volumes and closure fails
        # (+44% observed). Strict inequality matches _half_volume's x=0 slice
        # and keeps seam verts out of both domes (no double displacement).
        sgn = 1.0 if side == "left" else -1.0
        mask = mask & (mesh.vertices[:, 0] * sgn > 0.0)
        a_e, b_e = region_axes(lm, side, frame, REGION_MARGIN)
        u, w, _ = frame.coords(mesh.vertices)

        r_norm = np.sqrt((u / (a_e * s_bw)) ** 2 + (w / (b_e * s_bw)) ** 2)
        theta = np.arctan2(w, u)

        a = a_e * s_bw
        h = params.projection_cm
        beta = max(np.pi * a * a * h / params.volume_cc - 1.0, 0.05)
        cap = skirted_cap(r_norm, beta)  # rev.8: C1 rim, no ring crease

        field = h * placement_falloff(theta, r_norm, params.placement.value, cap)
        if params.shape == Shape.ANATOMICAL:
            field = field * anatomical_weight(theta)

        normal_field = np.zeros_like(mesh.vertices)
        # displace along LOCAL surface normals (rev.2): enclosed volume then
        # equals ∫field dA by construction — the anterior-direction version
        # lost up to ~30% on the steep mound slope and forced closure to
        # distort the apex. Also more physical: tissue drapes up over the dome.
        normal_field[mask] = field[mask, None] * mesh.vertex_normals[mask]

        inplane = np.zeros_like(mesh.vertices)
        inplane[mask] = ((s_bw - 1.0) * u[mask, None] * frame.x_hat
                         + (s_bw - 1.0) * w[mask, None] * frame.y_hat)

        # --- nipple-areola holdout (rev.7) ---------------------------------
        # The NAC keeps its size/shape: within NIPPLE_HOLD_CM the patch
        # translates RIGIDLY by the apex displacement (riding forward on the
        # new mound), smoothstep blend out to NIPPLE_BLEND_CM; in-plane
        # scaling fades to zero over the same zone so the areola is never
        # stretched. Volume closure absorbs the small holdout delta.
        r_cm = r_norm * a
        t = np.clip((r_cm - NIPPLE_HOLD_CM)
                    / (NIPPLE_BLEND_CM - NIPPLE_HOLD_CM), 0.0, 1.0)
        s = 1.0 - t
        w_hold = s * s * (3.0 - 2.0 * s)          # C1 smoothstep, 1 inside -> 0 outside
        if np.any(w_hold > 0.0):
            apex_idx = int(np.argmin(
                np.linalg.norm(mesh.vertices - half.nipple[None, :], axis=1)))
            rigid_vec = field[apex_idx] * mesh.vertex_normals[apex_idx]
            wh = w_hold[mask][:, None]
            normal_field[mask] = ((1.0 - wh) * normal_field[mask]
                                  + wh * rigid_vec[None, :])
            inplane[mask] = inplane[mask] * (1.0 - wh)
        return inplane, normal_field, mask

    @staticmethod
    def _apply_side(vertices, inplane, m, normal_field, side):
        """inplane + m·normal_field, position-clamped at the midplane (rev.10).

        The mask guard keeps cross-side verts out of the FIELD, but inplane
        slide and steep-slope normals can still push same-side verts ACROSS
        x=0; midplane-split half-volumes then count that displacement in both
        sides and per-side closure misses by ±20 cc. Clamping to the vert's
        own hemisphere keeps the closure measurement and the final mesh
        consistent.
        """
        disp = inplane + m * normal_field
        active = np.linalg.norm(disp, axis=1) > 0.0
        if side == "left":
            disp[active, 0] = np.maximum(disp[active, 0], -vertices[active, 0])
        else:
            disp[active, 0] = np.minimum(disp[active, 0], -vertices[active, 0])
        return vertices + disp

    def _close_volume(self, mesh_orig, faces, inplane, normal_field,
                      lm, side, target_cc):
        """Bisection on a uniform field multiplier to hit target added volume
        (rev.3). Volume is the hard constraint; the dome height scales with
        the multiplier, yielding the volume-consistent in-vivo projection
        (rated projection ≠ in-vivo projection — SPEC §2.6 rev.3)."""
        def added(m):
            verts = MorphEngine._apply_side(mesh_orig.vertices, inplane, m,
                                            normal_field, side)
            cand = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
            return displaced_volume_cc(mesh_orig, cand, lm, side)

        lo, hi = 0.0, 1.0
        while added(hi) < target_cc and hi < 64.0:
            hi *= 2.0
        best_m, best_err = hi, abs(added(hi) - target_cc)
        for _ in range(self.max_iters):
            mid = 0.5 * (lo + hi)
            val = added(mid)
            if val < target_cc:
                lo = mid
            else:
                hi = mid
            err = abs(val - target_cc)
            if err < best_err:
                best_m, best_err = mid, err
            if err <= self.volume_tol_cc:
                return mid, err
        return best_m, best_err

    def _measure_base_width(self, mesh_orig, lm, side, m, normal_field, mask):
        """Base width from the applied normal-field support (SPEC §2.3 rev.3):
        reference-frame u-extent of region verts with applied normal
        displacement above threshold, ±1 cm slice at nipple height,
        hemithorax clip. Degrades gracefully (rev.4): relaxes the threshold
        rather than raising when the dome is negligible. Thresholds are
        apex-relative (rev.8): the skirted rim tapers to zero, so an
        absolute cm threshold read ~8% under on high-profile domes; 2% of
        apex lands at r≈0.97 of the footprint across the beta range."""
        frame = local_frame(mesh_orig, lm, side)
        u, w, _ = frame.coords(mesh_orig.vertices)
        applied = m * np.linalg.norm(normal_field, axis=1)
        apex = float(applied[mask].max()) if mask.any() else 0.0
        midline = (mesh_orig.vertices[:, 0] > 0.0 if side == "left"
                   else mesh_orig.vertices[:, 0] < 0.0)
        slice_mask = np.abs(w) <= 1.0
        for thresh in (0.02 * apex, 0.05 * apex, 1e-4):
            sel = mask & (applied > thresh) & slice_mask & midline
            if sel.any():
                return float(u[sel].max() - u[sel].min())
        sel = mask & slice_mask & midline
        if not sel.any():
            raise ValueError(f"empty region near nipple height for side {side!r}")
        return float(u[sel].max() - u[sel].min())

    # -- public -----------------------------------------------------------

    def morph(self, mesh: trimesh.Trimesh, lm: ChestLandmarks,
              params: ImplantParams) -> MorphResult:
        guardrails = check_compatibility(lm, params)
        effective = guardrails.clamped_params if guardrails.clamped else params

        work = mesh.copy()
        achieved: dict[str, float] = {}
        applied_bw: dict[str, float] = {}
        # rev.7: BOTH side fields are computed from the ORIGINAL mesh, then
        # both applied — previously the second field was built on the
        # first-deformed mesh and achieved[left] measured before the right
        # side existed, an order artifact that made volumes asymmetric.
        pending: dict[str, tuple] = {}
        for side in ("left", "right"):
            inplane, normal_field, mask = self._side_field(mesh, lm, side,
                                                           effective)
            # rev.4 slide-share guardrail: in-plane expansion itself adds
            # volume; if it dominates the request, the implant/chest pairing
            # is a mismatch — warn instead of silently crushing the dome
            slid_verts = MorphEngine._apply_side(mesh.vertices, inplane, 0.0,
                                                 normal_field, side)
            slid = trimesh.Trimesh(vertices=slid_verts,
                                   faces=mesh.faces, process=False)
            slide_v = displaced_volume_cc(mesh, slid, lm, side)
            m = 0.0
            if slide_v >= effective.volume_cc:
                guardrails.warnings.append(
                    f"{side}: base-width expansion alone accounts for the "
                    f"requested volume — implant/chest mismatch; dome skipped")
                guardrails.ok = False
            else:
                if slide_v > 0.8 * effective.volume_cc:
                    guardrails.warnings.append(
                        f"{side}: base-width expansion dominates the volume "
                        f"change ({slide_v:.0f} cc of {effective.volume_cc:.0f})")
                    guardrails.ok = False
                m, err = self._close_volume(mesh, mesh.faces, inplane,
                                            normal_field, lm, side,
                                            effective.volume_cc)
                if err > self.volume_tol_cc:
                    guardrails.warnings.append(
                        f"{side}: volume closure did not converge "
                        f"(residual {err:.1f} cc)")
                    guardrails.ok = False
            pending[side] = (inplane, m, normal_field, mask)

        for side in ("left", "right"):
            inplane, m, normal_field, mask = pending[side]
            work.vertices = self._apply_side(work.vertices, inplane, m,
                                             normal_field, side)
        for side in ("left", "right"):
            inplane, m, normal_field, mask = pending[side]
            achieved[side] = displaced_volume_cc(mesh, work, lm, side)
            applied_bw[side] = self._measure_base_width(mesh, lm, side, m,
                                                        normal_field, mask)

        measurements = {
            side: {
                "projection_cm": measure_projection_cm(work, lm, side,
                                                       margin=REGION_MARGIN),
                "base_width_cm": applied_bw[side],
            }
            for side in ("left", "right")
        }
        return MorphResult(
            mesh=work,
            deformation=work.vertices - mesh.vertices,
            achieved_volume_cc=achieved,
            measurements=measurements,
            guardrails=guardrails,
        )
