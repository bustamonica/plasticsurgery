"""Anny real-body provider (Track A / M2).

Loads a real parametric human body from the Apache-2.0 `anny` package
(NAVER LABS, MakeHuman/mpfb2 assets, CC0), converts it to the engine frame
(cm, +x patient-left, +y up, +z anterior) and derives ChestLandmarks
geometrically from the mesh.

Anny frame: meters, +x patient-left, +z up, -y anterior (Blender-style).

This module imports anny lazily — the package is an optional dependency.
"""
from __future__ import annotations

import numpy as np
import trimesh

from .landmarks import ChestLandmarks


def anny_available() -> bool:
    try:
        import anny  # noqa: F401
        return True
    except ImportError:
        return False


def to_engine_frame(vertices_m: np.ndarray) -> np.ndarray:
    """anny (m, +x left, +z up, -y anterior) -> engine (cm, +x left, +y up, +z anterior)."""
    v = np.asarray(vertices_m, dtype=np.float64)
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1) * 100.0


class AnnyBodyProvider:
    """Builds real female bodies via the anny parametric model.

    Deterministic given the phenotype dict. Model construction is cached by
    anny itself (~/.cache/anny); first build in a fresh env costs ~2 min.
    """

    def __init__(self, phenotype: dict | None = None, **model_kwargs):
        self.phenotype = dict(phenotype or dict(
            gender=1.0, age=0.5, muscle=0.5, weight=0.5, height=0.5))
        self.model_kwargs = dict(triangulate_faces=True, **model_kwargs)
        self._model = None

    def _get_model(self):
        if self._model is None:
            import anny
            self._model = anny.create_fullbody_model(**self.model_kwargs)
        return self._model

    def sample(self) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        """(mesh, landmarks, meta) — engine frame, cm, neutral pose."""
        import roma
        import torch

        model = self._get_model()
        n_bones = len(model.bone_labels)
        rot = torch.eye(3, dtype=model.dtype).expand(n_bones, 3, 3)
        trans = torch.zeros((n_bones, 3), dtype=model.dtype)
        pose = roma.Rigid(rot, trans)[None].to_homogeneous()
        out = model(pose_parameters=pose, phenotype_kwargs=self.phenotype)
        v_m = out["vertices"][0].cpu().numpy()
        faces = model.faces.cpu().numpy()

        v_cm = to_engine_frame(v_m)
        mesh = trimesh.Trimesh(vertices=v_cm, faces=faces, process=True)
        lm = derive_chest_landmarks(mesh)
        meta = dict(provider="anny", phenotype=self.phenotype,
                    n_vertices=len(v_cm), watertight=bool(mesh.is_watertight))
        return mesh, lm, meta


def _surface_vertex(mesh: trimesh.Trimesh, x: float, y: float) -> np.ndarray:
    """Vertex on the anterior OUTER chest surface nearest to (x, y) in-plane.

    The mpfb2 main component contains an inner shell (chest-cavity cap) ~17 cm
    behind the skin, so queries must filter to the outermost (max-z) layer.
    """
    v = mesh.vertices
    band = (v[:, 0] > x - 2.0) & (v[:, 0] < x + 2.0) & \
           (v[:, 1] > y - 2.0) & (v[:, 1] < y + 2.0) & (v[:, 2] > 0.0)
    idx = np.where(band)[0]
    if len(idx) == 0:
        raise ValueError(f"no anterior vertex near ({x}, {y})")
    zmax = v[idx, 2].max()
    outer = idx[v[idx, 2] > zmax - 6.0]          # outer layer only
    near = outer[np.argmin((v[outer, 0] - x) ** 2 + (v[outer, 1] - y) ** 2)]
    return v[near]


def derive_chest_landmarks(mesh: trimesh.Trimesh) -> ChestLandmarks:
    """Geometric chest landmarks from a real body mesh (engine frame, cm).

    Strategy: nipple = robust centroid of the most-anterior vertices in the
    chest band of each side; chest-wall baseline = median z of the lateral
    chest strip at nipple height; breast region = connected mound of vertices
    elevated > 5 mm above the wall baseline; R = mean half-extent of that
    region (x and y); projection = nipple z - wall z.
    """
    v = mesh.vertices
    # chest band: between 55% and 80% of trunk height (hips ~0, shoulders ~44)
    zmax = v[:, 1].max()
    band = (v[:, 1] > 0.45 * zmax) & (v[:, 1] < 0.75 * zmax) & (v[:, 2] > 0.0)

    nipples, centers, radii, projs = {}, {}, {}, {}
    for side, sgn in (("left", +1.0), ("right", -1.0)):
        half = band & (v[:, 0] * sgn > 2.0) & (np.abs(v[:, 0]) < 22.0)
        idx = np.where(half)[0]
        if len(idx) == 0:
            raise ValueError(f"no chest-band vertices on side {side}")
        # top 8 most-anterior vertices -> robust nipple estimate
        top = idx[np.argsort(v[idx, 2])[-8:]]
        nip = v[top].mean(axis=0)

        # ring profile: median outer-layer z in planar rings around the nipple.
        # The mound is a plateau that falls steeply at the breast boundary;
        # the chest wall baseline is the ring well clear of the mound.
        same_side = band & (v[:, 0] * sgn > -2.0)
        rr = np.linalg.norm(v[:, :2] - nip[:2], axis=1)
        ring_med = {}
        for r0, r1 in ((7.0, 9.0), (1.5, 2.5), (2.5, 3.5), (3.5, 4.5),
                       (4.5, 5.5), (5.5, 6.5), (6.5, 7.5)):
            sel = np.where(same_side & (rr >= r0) & (rr < r1))[0]
            if len(sel) >= 6:
                zmax = v[sel, 2].max()
                outer = sel[v[sel, 2] > zmax - 6.0]
                ring_med[(r0 + r1) / 2.0] = float(np.median(v[outer, 2]))
        wall_z = ring_med.get(8.0, 0.0)
        proj = max(0.5, float(nip[2] - wall_z))
        # R: first ring whose median drops below nip - 0.8*proj
        thresh = nip[2] - 0.8 * proj
        R_est = 6.0
        for r_mid in sorted(k for k in ring_med if k < 8.0):
            if ring_med[r_mid] < thresh:
                R_est = r_mid
                break
        R = float(np.clip(R_est, 3.5, 9.0))

        # midline clearance: on fuller phenotypes the most-anterior surface
        # sits near the cleavage (x~2), and an R-sized footprint there crosses
        # the midline — left/right domes overlap, double-counted volume, and
        # closure fails (+34%/+100% observed). Enforce medial >= 1 cm from
        # the midline: shrink R if possible, else re-anchor the apex laterally.
        clear = 1.0
        x_side = abs(nip[0])
        if x_side - R < clear:
            R = float(np.clip(min(R, x_side - clear), 3.5, 9.0)) if x_side - clear >= 3.5 else 3.5
        if x_side - R < clear:
            new_x = R + clear
            nip = _surface_vertex(mesh, sgn * new_x, nip[1])
            proj = max(0.5, float(nip[2] - wall_z))
            # the returned vertex may sit medial of the request — final clamp
            R = max(3.0, min(R, abs(nip[0]) - clear))

        nipples[side] = nip
        centers[side] = np.array([nip[0], nip[1], nip[2] - proj])
        radii[side] = R
        projs[side] = proj

    # chest width: torso cross-section at nipple height. Arms connect to the
    # torso in the slab graph, so connectivity cannot separate them — but the
    # torso is ~20 cm deep (z-span) while an arm cross-section is ≤8 cm. Bin
    # the slab by |x|, compute z-span per bin, and end the torso at the first
    # bin whose z-span drops below 9 cm and stays there (persistence guards
    # against isolated sliver bins like the nipple plateau edge).
    ny = (nipples["left"][1] + nipples["right"][1]) / 2.0
    slab = v[np.abs(v[:, 1] - ny) < 2.0]
    x_abs = np.abs(slab[:, 0])
    spans = {}
    for lo in range(0, int(x_abs.max())):
        s = slab[(x_abs >= lo) & (x_abs < lo + 1)]
        if len(s) >= 3:
            spans[lo] = float(s[:, 2].max() - s[:, 2].min())
    half_w = 13.0
    los = sorted(spans)
    for i, lo in enumerate(los):
        if spans[lo] < 9.0 and all(spans.get(hi, 0.0) < 9.0 for hi in los[i:i + 3]):
            half_w = float(lo)
            break
    else:
        half_w = float(los[-1] + 1) if los else 13.0
    chest_width = float(np.clip(2.0 * half_w, 24.0, 44.0))

    # clavicle: anatomically ~1.6 base-radii above the nipple on the midline
    clav_y = ny + 1.6 * max(radii.values())
    clavicle_mid = _surface_vertex(mesh, 0.0, clav_y)
    sternum_mid = _surface_vertex(mesh, 0.0, ny)

    def base(side):
        return centers[side]

    return ChestLandmarks(
        nipple_left=nipples["left"],
        nipple_right=nipples["right"],
        imf_left=base("left") + np.array([0.0, -radii["left"], 0.0]),
        imf_right=base("right") + np.array([0.0, -radii["right"], 0.0]),
        lateral_left=base("left") + np.array([+radii["left"], 0.0, 0.0]),
        lateral_right=base("right") + np.array([-radii["right"], 0.0, 0.0]),
        medial_left=base("left") + np.array([-radii["left"], 0.0, 0.0]),
        medial_right=base("right") + np.array([+radii["right"], 0.0, 0.0]),
        clavicle_mid=clavicle_mid,
        sternum_mid=sternum_mid,
        chest_width_cm=chest_width,
    )
