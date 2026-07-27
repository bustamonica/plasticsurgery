"""MorphEngine: implant params → deformed mesh (SPEC §2.6, rev.2).

Per-breast algorithm:
  1. guardrail check (once, params-level)
  2. landmark-anchored local frame + region selection (margin=1.0)
  3. in-plane base-width scaling about the nipple (clamped [0.8, 1.5])
  4. dome field: h·(1−r²)^β with per-implant fullness β = πa²h/V − 1, so
     rated volume / base width / projection are consistent by construction;
     placement + anatomical multipliers redistribute (volume-neutral,
     apex-preserving)
  5. volume closure on λ in field·(1+λr²) — adjusts mid/rim fullness, never
     the apex, so closure cannot distort rated projection
  6. both sides independently, symmetric params (v0); one implant of
     `volume_cc` per breast
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from ..geometry.landmarks import ChestLandmarks
from ..geometry.measure import (displaced_volume_cc, measure_base_width_cm,
                                measure_projection_cm)
from ..implants.schema import ImplantParams, Shape
from .deformation import (anatomical_weight, breast_region, local_frame,
                          placement_falloff, region_axes)
from .guardrails import GuardrailResult, check_compatibility

REGION_MARGIN = 1.0
_INPLANE_SCALE_BOUNDS = (0.8, 1.5)


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
        a_e, b_e = region_axes(lm, side, frame, REGION_MARGIN)
        u, w, _ = frame.coords(mesh.vertices)

        r_norm = np.sqrt((u / (a_e * s_bw)) ** 2 + (w / (b_e * s_bw)) ** 2)
        theta = np.arctan2(w, u)

        a = a_e * s_bw
        h = params.projection_cm
        beta = max(np.pi * a * a * h / params.volume_cc - 1.0, 0.05)
        cap = np.clip(1.0 - r_norm**2, 0.0, None) ** beta

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
        return inplane, normal_field, mask

    def _close_volume(self, mesh_orig, faces, inplane, normal_field,
                      lm, side, target_cc):
        """Bisection on a uniform field multiplier to hit target added volume
        (rev.3). Volume is the hard constraint; the dome height scales with
        the multiplier, yielding the volume-consistent in-vivo projection
        (rated projection ≠ in-vivo projection — SPEC §2.6 rev.3)."""
        def added(m):
            verts = mesh_orig.vertices + inplane + m * normal_field
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
                return mid
        return best_m

    def _measure_base_width(self, mesh_orig, lm, side, m, normal_field, mask):
        """Base width from the applied normal-field support (SPEC §2.3 rev.3):
        reference-frame u-extent of region verts with applied normal
        displacement > 0.25 cm, ±1 cm slice at nipple height, hemithorax clip.
        (Absolute-elevation measurement catches the torso's natural slope and
        the in-plane slide along it; the dome footprint is the honest metric
        for the scaling machinery. Volume + projection stay independently
        plane/slice-measured.)"""
        frame = local_frame(mesh_orig, lm, side)
        u, w, _ = frame.coords(mesh_orig.vertices)
        applied = m * np.linalg.norm(normal_field, axis=1)
        sel = mask & (applied > 0.25) & (np.abs(w) <= 1.0)
        midline = (mesh_orig.vertices[:, 0] > 0.0 if side == "left"
                   else mesh_orig.vertices[:, 0] < 0.0)
        sel &= midline
        if not sel.any():
            raise ValueError(f"no dome support near nipple height for side {side!r}")
        return float(u[sel].max() - u[sel].min())

    # -- public -----------------------------------------------------------

    def morph(self, mesh: trimesh.Trimesh, lm: ChestLandmarks,
              params: ImplantParams) -> MorphResult:
        guardrails = check_compatibility(lm, params)
        effective = guardrails.clamped_params if guardrails.clamped else params

        work = mesh.copy()
        achieved: dict[str, float] = {}
        applied_bw: dict[str, float] = {}
        for side in ("left", "right"):
            inplane, normal_field, mask = self._side_field(work, lm, side,
                                                           effective)
            m = self._close_volume(mesh, work.faces, inplane, normal_field,
                                   lm, side, effective.volume_cc)
            work.vertices = work.vertices + inplane + m * normal_field
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
