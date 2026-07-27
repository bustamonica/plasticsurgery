"""Procedural synthetic torso fixture (SPEC §2.2). Zero external assets.

Watertight ellipsoid torso + two hemispherical breast mounds, built by
radially displacing an icosphere — topology never changes, so the mesh stays
watertight. Landmarks are analytic, derived from construction parameters.
"""

from __future__ import annotations

import numpy as np
import trimesh

from .landmarks import ChestLandmarks, LandmarkProvider

FIXTURE_PARAMS_KEY = "fixture_params"


def _breast_base(chest_width_cm: float, torso_depth_cm: float, height_cm: float,
                 breast_x_cm: float, breast_y_cm: float, side_sign: float) -> np.ndarray:
    """Center of a breast mound base on the anterior ellipsoid surface."""
    a, b, c = chest_width_cm / 2, height_cm / 2, torso_depth_cm / 2
    bz = c * np.sqrt(max(0.0, 1 - (breast_x_cm / a) ** 2 - (breast_y_cm / b) ** 2))
    return np.array([side_sign * breast_x_cm, breast_y_cm, bz])


def _analytic_landmarks(p: dict) -> ChestLandmarks:
    cw, depth, h = p["chest_width_cm"], p["torso_depth_cm"], p["height_cm"]
    R, proj = p["breast_radius_cm"], p["breast_projection_cm"]
    bx, by = p["breast_x_cm"], p["breast_y_cm"]
    b, c = h / 2, depth / 2

    Bl = _breast_base(cw, depth, h, bx, by, +1.0)
    Br = _breast_base(cw, depth, h, bx, by, -1.0)
    anterior = np.array([0.0, 0.0, proj])

    clav_y = 0.55 * b
    clav_z = c * np.sqrt(max(0.0, 1 - (clav_y / b) ** 2))
    ster_z = c * np.sqrt(max(0.0, 1 - (by / b) ** 2))

    return ChestLandmarks(
        nipple_left=Bl + anterior,
        nipple_right=Br + anterior,
        imf_left=Bl + np.array([0.0, -R, 0.0]),
        imf_right=Br + np.array([0.0, -R, 0.0]),
        lateral_left=Bl + np.array([+R, 0.0, 0.0]),
        lateral_right=Br + np.array([-R, 0.0, 0.0]),
        medial_left=Bl + np.array([-R, 0.0, 0.0]),
        medial_right=Br + np.array([+R, 0.0, 0.0]),
        clavicle_mid=np.array([0.0, clav_y, clav_z]),
        sternum_mid=np.array([0.0, by, ster_z]),
        chest_width_cm=cw,
    )


def synthetic_torso(
    chest_width_cm: float = 34.0,
    torso_depth_cm: float = 20.0,
    height_cm: float = 50.0,
    breast_radius_cm: float = 6.0,
    breast_projection_cm: float = 3.0,
    breast_x_cm: float = 7.0,
    breast_y_cm: float = 2.0,
    resolution: int = 6,
) -> trimesh.Trimesh:
    """Watertight procedural torso. Deterministic given parameters.

    `resolution` = icosphere subdivisions (6 ≈ 40k verts; ≥5 recommended so
    the breast region contains enough vertices for stable measurements).
    """
    a, b, c = chest_width_cm / 2, height_cm / 2, torso_depth_cm / 2
    mesh = trimesh.creation.icosphere(subdivisions=resolution, radius=1.0)
    mesh.vertices *= np.array([a, b, c])

    for sign in (+1.0, -1.0):
        B = _breast_base(chest_width_cm, torso_depth_cm, height_cm,
                         breast_x_cm, breast_y_cm, sign)
        d = np.linalg.norm(mesh.vertices - B, axis=1)
        bump = breast_projection_cm * np.sqrt(np.clip(1 - (d / breast_radius_cm) ** 2, 0.0, None))
        mesh.vertices[:, 2] += bump

    mesh.metadata[FIXTURE_PARAMS_KEY] = dict(
        chest_width_cm=chest_width_cm, torso_depth_cm=torso_depth_cm,
        height_cm=height_cm, breast_radius_cm=breast_radius_cm,
        breast_projection_cm=breast_projection_cm,
        breast_x_cm=breast_x_cm, breast_y_cm=breast_y_cm,
        resolution=resolution,
    )
    return mesh


class FixtureLandmarkProvider(LandmarkProvider):
    """Exact analytic landmarks for meshes built by synthetic_torso()."""

    def locate(self, mesh: trimesh.Trimesh) -> ChestLandmarks:
        params = mesh.metadata.get(FIXTURE_PARAMS_KEY)
        if params is None:
            raise ValueError(
                "mesh lacks metadata['fixture_params'] — was it built by "
                "synthetic_torso()? (OBJ round-trips drop metadata)"
            )
        return _analytic_landmarks(params)
