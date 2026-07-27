"""Deformation fields: region selection + falloff functions (SPEC §2.4).

Local frame convention (per breast): origin at nipple; `anterior` = smoothed
surface normal; `x_hat` = mediolateral (toward lateral); `y_hat` = superoinferior
(toward clavicle). Region is an elliptical cylinder in that frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from ..geometry.landmarks import ChestLandmarks


@dataclass(frozen=True)
class LocalFrame:
    origin: np.ndarray   # nipple (3,)
    anterior: np.ndarray # unit
    x_hat: np.ndarray    # unit, mediolateral
    y_hat: np.ndarray    # unit, superoinferior

    def coords(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(u, w, t): mediolateral, superoinferior, anterior coordinates."""
        d = points - self.origin
        return d @ self.x_hat, d @ self.y_hat, d @ self.anterior


def local_frame(mesh: trimesh.Trimesh, lm: ChestLandmarks, side: str,
                smooth_k: int = 12) -> LocalFrame:
    """Landmark-anchored local frame (SPEC §2.4 rev.1).

    rev.1: anterior axis comes from the chest-wall plane through the side's
    imf/lateral/medial landmarks — NOT from smoothed mesh normals, which tilt
    on deformed/coarse meshes and made region selection non-deterministic.
    `mesh` is retained in the signature for future surface-aware refinements.
    """
    half = lm.breast_half(side)
    n = half.nipple

    v1 = half.lateral - half.imf
    v2 = half.medial - half.imf
    anterior = np.cross(v1, v2)
    anterior /= np.linalg.norm(anterior)
    if anterior @ (n - half.imf) < 0:  # must point toward the nipple
        anterior = -anterior

    x_hat = half.lateral - half.medial
    x_hat -= (x_hat @ anterior) * anterior
    x_hat /= np.linalg.norm(x_hat)

    y_hat = np.cross(anterior, x_hat)
    y_hat /= np.linalg.norm(y_hat)
    if y_hat @ (lm.clavicle_mid - n) < 0:
        y_hat = -y_hat

    return LocalFrame(n, anterior, x_hat, y_hat)


def region_axes(lm: ChestLandmarks, side: str, frame: LocalFrame,
                margin: float) -> tuple[float, float]:
    """(a_e, b_e): ellipse semi-axes. Lateral axis from landmarks; vertical
    axis = IMF distance mirrored superiorly (v0 superior boundary)."""
    half = lm.breast_half(side)
    n = half.nipple
    a_e = max(abs((half.lateral - n) @ frame.x_hat),
              abs((half.medial - n) @ frame.x_hat)) * margin
    inf = abs((half.imf - n) @ frame.y_hat)
    b_e = inf * margin
    return a_e, b_e


def breast_region(mesh: trimesh.Trimesh, lm: ChestLandmarks, side: str,
                  margin: float = 1.15) -> np.ndarray:
    """Boolean vertex mask (N,) of the breast region (SPEC §2.4 rev.2).

    2D ellipse in the frame's (u, w) plane ∩ forward-facing vertices
    (vertex normal · anterior > −0.2). rev.2: the previous depth clip
    measured from the nipple silently discarded the mound's lower slope
    (the chest wall slopes away from the imf plane on real torsos); facing
    the normal is the robust anterior/posterior test.
    """
    frame = local_frame(mesh, lm, side)
    a_e, b_e = region_axes(lm, side, frame, margin)
    u, w, _ = frame.coords(mesh.vertices)
    ellipse = (u / a_e) ** 2 + (w / b_e) ** 2 <= 1.0
    facing = mesh.vertex_normals @ frame.anterior > -0.2
    return ellipse & facing


def radial_falloff(r_norm: np.ndarray, kind: str = "cosine") -> np.ndarray:
    """1.0 at center → 0.0 at r_norm=1 (SPEC §2.4, rev.1).

    'cosine': 0.5*(1+cos(pi r)) — smooth, low volume-per-apex.
    'dome':   sqrt(1 - r²) — hemi-ellipsoid profile; matches breast-implant
              dome geometry, so volume closure lands near the implant's
              rated projection. Used by MorphEngine.
    """
    r = np.clip(np.asarray(r_norm, dtype=float), 0.0, 1.0)
    if kind == "cosine":
        return 0.5 * (1.0 + np.cos(np.pi * r))
    if kind == "dome":
        return np.sqrt(np.clip(1.0 - r**2, 0.0, None))
    raise ValueError(f"unknown falloff kind {kind!r}")


_PLACEMENT_K = {"submuscular": -0.55, "dual-plane": -0.10, "subglandular": 0.30}


def placement_falloff(theta: np.ndarray, r_norm: np.ndarray, placement: str,
                      base: np.ndarray) -> np.ndarray:
    """Placement-specific upper-pole modulation (SPEC §2.4, rev.2).

    mult = 1 + k·(sup·r − r·ratio), with ratio chosen so the multiplier is
    **volume-neutral against `base`** (Σ base·mult = Σ base) — placement
    redistributes tissue inferior/superior instead of adding/removing volume.
    Also apex-preserving (r=0 → mult=1, so rated projection is untouched).
    submuscular flattens the superior slope (k<0), subglandular fills it
    (k>0), dual-plane is the midpoint. Continuous; bounded.
    """
    if placement not in _PLACEMENT_K:
        raise ValueError(f"unknown placement {placement!r}")
    k = _PLACEMENT_K[placement]
    base = np.asarray(base, dtype=float)
    r = np.clip(np.asarray(r_norm, dtype=float), 0.0, 1.0)
    sup_r = np.clip(np.sin(theta), 0.0, 1.0) * r
    denom = max(float((base * r).sum()), 1e-9)
    ratio = float((base * sup_r).sum()) / denom
    mult = 1.0 + k * (sup_r - r * ratio)
    return base * np.clip(mult, 0.0, 1.5)


def anatomical_weight(theta: np.ndarray) -> np.ndarray:
    """Teardrop weighting (SPEC §2.4): inferior pole favored, [0.7, 1.3]."""
    return 1.0 - 0.3 * np.sin(theta)
