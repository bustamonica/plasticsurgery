"""Mesh measurement utilities (SPEC §2.3)."""

from __future__ import annotations

import numpy as np
import trimesh

from ..geometry.landmarks import ChestLandmarks
from ..morph.deformation import breast_region, local_frame


def chest_wall_plane(lm: ChestLandmarks, side: str) -> tuple[np.ndarray, np.ndarray]:
    """(point, unit normal) of the chest-wall plane behind one breast: fit
    through that side's imf/lateral/medial, normal pointing anterior."""
    half = lm.breast_half(side)
    v1 = half.lateral - half.imf
    v2 = half.medial - half.imf
    normal = np.cross(v1, v2)
    normal /= np.linalg.norm(normal)
    if normal @ (half.nipple - half.imf) < 0:
        normal = -normal
    return half.imf.copy(), normal


def measure_projection_cm(mesh: trimesh.Trimesh, lm: ChestLandmarks, side: str,
                          margin: float = 1.0) -> float:
    """Max distance from chest-wall plane to surface within the breast region."""
    point, normal = chest_wall_plane(lm, side)
    mask = breast_region(mesh, lm, side, margin=margin)
    if not mask.any():
        raise ValueError(f"empty breast region for side {side!r}")
    heights = (mesh.vertices[mask] - point) @ normal
    return float(heights.max())


def measure_base_width_cm(mesh: trimesh.Trimesh, lm: ChestLandmarks, side: str,
                          margin: float = 1.0,
                          reference: trimesh.Trimesh | None = None) -> float:
    """Mediolateral extent of elevated breast tissue, widest slice within
    ±1 cm of nipple height.

    `reference` (optional): pre-morph mesh. When given, elevation is measured
    as ADDED height above the chest-wall plane vs. the reference — isolating
    the dome from the torso's natural slope (SPEC §2.3 rev.2). Otherwise
    elevation is absolute height above the plane (pre-morph measurement).
    rev.2: landmark-anchored axes + hemithorax clip.
    """
    point, normal = chest_wall_plane(lm, side)
    half = lm.breast_half(side)

    x_hat = half.lateral - half.medial
    x_hat -= (x_hat @ normal) * normal
    x_hat /= np.linalg.norm(x_hat)
    y_hat = np.cross(normal, x_hat)
    y_hat /= np.linalg.norm(y_hat)
    if y_hat @ (lm.clavicle_mid - half.nipple) < 0:
        y_hat = -y_hat

    d = mesh.vertices - point
    t = d @ normal
    if reference is not None:
        t = t - (reference.vertices - point) @ normal
        # footprint extent from pre-displacement coordinates: local-normal
        # displacement shifts rim vertices laterally, which would otherwise
        # inflate the measured width (SPEC §2.3 rev.3)
        d = reference.vertices - point
    w = d @ y_hat - (half.nipple - point) @ y_hat
    u = d @ x_hat

    sel = (breast_region(mesh, lm, side, margin=1.5) & (t > 0.25)
           & (np.abs(w) <= 1.0))
    # hemithorax clip: the (margin-scaled) region ellipse can reach past the
    # midline and catch the other breast's elevated rim (SPEC §2.3 rev.2)
    midline = mesh.vertices[:, 0] > 0.0 if side == "left" else mesh.vertices[:, 0] < 0.0
    sel &= midline
    if not sel.any():
        raise ValueError(f"no elevated tissue near nipple height for side {side!r}")
    return float(u[sel].max() - u[sel].min())


def _half_volume(mesh: trimesh.Trimesh, side: str) -> float:
    """Volume of the +x (left) or −x (right) half of a watertight mesh."""
    normal = np.array([1.0, 0.0, 0.0]) if side == "left" else np.array([-1.0, 0.0, 0.0])
    half_mesh = mesh.slice_plane([0.0, 0.0, 0.0], normal, cap=True)
    if len(half_mesh.vertices) == 0:
        raise ValueError(f"midplane slice empty for side {side!r}")
    return abs(float(half_mesh.volume))


def displaced_volume_cc(mesh_before: trimesh.Trimesh, mesh_after: trimesh.Trimesh,
                        lm: ChestLandmarks, side: str) -> float:
    """Volume added on one side. Watertight meshes → exact midplane-split diff.
    Non-watertight → first-order surface integral over the region."""
    if mesh_before.is_watertight and mesh_after.is_watertight:
        return _half_volume(mesh_after, side) - _half_volume(mesh_before, side)

    # First-order fallback (rough estimate only — ignores h²·H curvature
    # terms, which are large on convex mounds; NOT used by the engine, which
    # requires watertight meshes). SPEC §2.3 rev.4.
    import warnings
    warnings.warn("displaced_volume_cc: non-watertight input — first-order "
                  "estimate only (large error possible)", RuntimeWarning,
                  stacklevel=2)
    mask = breast_region(mesh_before, lm, side, margin=1.0)
    disp = mesh_after.vertices[mask] - mesh_before.vertices[mask]
    normals = mesh_before.vertex_normals[mask]
    normal_disp = np.einsum("ij,ij->i", disp, normals)
    region_faces = [i for i, f in enumerate(mesh_before.faces) if mask[f].all()]
    if not region_faces:
        raise ValueError("no fully-region faces for volume integral")
    total_area = float(mesh_before.area_faces[region_faces].sum())
    return float(normal_disp.mean() * total_area)


def upper_pole_slope(mesh: trimesh.Trimesh, lm: ChestLandmarks, side: str,
                     margin: float = 1.0) -> float:
    """|dt/dw| over the superior half of the breast region (least squares),
    measured down from the apex. LARGER = faster drop-off = EMPTIER upper
    pole (submuscular look); SMALLER = sustained fullness (subglandular)."""
    frame = local_frame(mesh, lm, side)
    mask = breast_region(mesh, lm, side, margin=margin)
    u, w, t = frame.coords(mesh.vertices[mask])
    sup = w > 0
    if sup.sum() < 3:
        raise ValueError(f"too few superior vertices for side {side!r}")
    A = np.column_stack([w[sup], np.ones(int(sup.sum()))])
    k, _ = np.linalg.lstsq(A, t[sup], rcond=None)[0]
    return float(abs(k))
