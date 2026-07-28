"""Pure-numpy z-buffer software renderer (SPEC M1.2).

Deterministic: same mesh + camera -> byte-identical output arrays. No GL/GPU,
no RNG, no dict-order dependence. trimesh is used only as the mesh container
(mesh.vertices / mesh.faces / mesh.face_normals).

Conventions
-----------
- Camera space: +x right, +y up, +z along the viewing direction (away from
  the camera into the scene). Depth is therefore positive in front of the
  camera and equals the camera-space z coordinate (cm).
- Projection: perspective, square image, vertical fov = Camera.fov_deg,
  principal point at the image center, pixel centers at (col+0.5, row+0.5).
- Shading: smooth per-pixel normals (perspective-correct vertex-normal
  interpolation; the normal channel stores the same smooth normals, so rgb
  and normal maps are consistent):
      rgb = albedo * (AMBIENT + max(n.l, 0)) + SPECULAR * max(n.h, 0)**SHININESS
  with the key light from upper-left-front in camera space
  (l = normalize((-0.5, 0.7, -1.0))) and view direction (0, 0, -1)
  (Blinn half vector h = normalize(l + view)).

Rasterization is vectorized across triangle chunks: screen-space barycentric
edge functions are evaluated on padded per-triangle bounding-box grids with
numpy broadcasting (loop over chunks only, never over pixels), then a single
stable lexsort on (pixel, depth) resolves the z-buffer and the winning
triangle per pixel in one pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

# --- shading constants (SPEC M1.2) -------------------------------------------
ALBEDO = np.array([0.72, 0.57, 0.48], dtype=np.float64)
AMBIENT = 0.15
SPECULAR = 0.2
SHININESS = 32.0
BACKGROUND_RGB = (245, 242, 238)
_LIGHT_CAM = np.array([-0.5, 0.7, -1.0], dtype=np.float64)  # upper-left-front
_VIEW_CAM = np.array([0.0, 0.0, -1.0], dtype=np.float64)    # toward the camera

# --- rasterizer tuning --------------------------------------------------------
_MIN_Z = 1e-6          # triangles with any vertex closer than this are dropped
_MIN_AREA = 1e-12      # degenerate screen-space triangles are dropped
_CELL_BUDGET = 2_000_000  # max padded (tri, dy, dx) grid cells per chunk
_MAX_CHUNK_TRIS = 4096


@dataclass(frozen=True)
class Camera:
    position: tuple[float, float, float]   # cm
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov_deg: float
    image_size: int                        # square


@dataclass
class RenderResult:
    rgb: np.ndarray      # (H,W,3) uint8 — Lambert+Blinn shaded
    depth: np.ndarray    # (H,W) float32 camera-space z (cm); bg = np.nan
    normal: np.ndarray   # (H,W,3) float32 camera-space unit normals; bg = 0
    mask: np.ndarray     # (H,W) bool


def _view_rotation(camera: Camera) -> tuple[np.ndarray, np.ndarray]:
    """Return (R, position) with camera-space coords = (p - position) @ R.T."""
    pos = np.asarray(camera.position, dtype=np.float64)
    tgt = np.asarray(camera.target, dtype=np.float64)
    up = np.asarray(camera.up, dtype=np.float64)
    fwd = tgt - pos
    n = np.linalg.norm(fwd)
    if n == 0.0:
        raise ValueError("camera position equals target")
    fwd /= n
    right = np.cross(fwd, up)
    n = np.linalg.norm(right)
    if n == 0.0:
        raise ValueError("camera up parallel to viewing direction")
    right /= n
    up_cam = np.cross(right, fwd)
    return np.stack([right, up_cam, fwd], axis=0), pos


def _chunk_slices(heights: np.ndarray, widths: np.ndarray):
    """Yield slices over the valid-triangle arrays, bounding padded cells."""
    n = len(heights)
    i = 0
    while i < n:
        j = i
        mh = mw = 0
        while j < n and (j - i) < _MAX_CHUNK_TRIS:
            nh = max(mh, int(heights[j]))
            nw = max(mw, int(widths[j]))
            if j > i and (j - i + 1) * nh * nw > _CELL_BUDGET:
                break
            mh, mw = nh, nw
            j += 1
        yield slice(i, j)
        i = j


class SoftwareRenderer:
    """Pure-numpy z-buffer rasterizer. Deterministic; no GL/GPU deps.

    Perspective projection; backface culling enabled by default (safe for the
    closed fixture meshes; disable via ``backface_cull=False`` for open
    meshes). Shading per SPEC M1.2. Perf budget: <=20 s per 256x256 render of
    a ~40k-triangle mesh (measured far below that; see tests/timing report).
    """

    def __init__(self, camera: Camera, backface_cull: bool = True):
        self.camera = camera
        self.backface_cull = bool(backface_cull)
        self._R, self._pos = _view_rotation(camera)
        s = int(camera.image_size)
        if s <= 0:
            raise ValueError("image_size must be positive")
        self._size = s
        self._focal = 0.5 * s / math.tan(math.radians(camera.fov_deg) * 0.5)
        # Shading basis in camera space (constant across renders).
        self._light = _LIGHT_CAM / np.linalg.norm(_LIGHT_CAM)
        half = self._light + _VIEW_CAM
        self._half = half / np.linalg.norm(half)

    def render(self, mesh: trimesh.Trimesh) -> RenderResult:
        s = self._size
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)

        cam = (verts - self._pos) @ self._R.T            # (N,3) camera space
        z = cam[:, 2]
        ncam = np.asarray(mesh.face_normals, dtype=np.float64) @ self._R.T
        vncam = np.asarray(mesh.vertex_normals, dtype=np.float64) @ self._R.T

        f0, f1, f2 = faces[:, 0], faces[:, 1], faces[:, 2]
        iz = np.where(z > _MIN_Z, 1.0 / np.maximum(z, _MIN_Z), 0.0)
        u = self._focal * cam[:, 0] * iz + 0.5 * s
        v = 0.5 * s - self._focal * cam[:, 1] * iz

        zt = np.stack([z[f0], z[f1], z[f2]], axis=1)
        ut = np.stack([u[f0], u[f1], u[f2]], axis=1)
        vt = np.stack([v[f0], v[f1], v[f2]], axis=1)

        area = ((ut[:, 1] - ut[:, 0]) * (vt[:, 2] - vt[:, 0])
                - (ut[:, 2] - ut[:, 0]) * (vt[:, 1] - vt[:, 0]))
        valid = (zt > _MIN_Z).all(axis=1) & (np.abs(area) > _MIN_AREA)
        if self.backface_cull:
            valid &= ncam[:, 2] < 0.0

        idx = np.nonzero(valid)[0]
        if idx.size == 0:
            return self._background_result()

        ut, vt, zt = ut[idx], vt[idx], zt[idx]
        area = area[idx]
        ncam = ncam[idx]
        izt = 1.0 / zt

        # Integer pixel bboxes (pixel centers at c+0.5 / r+0.5), clamped.
        cmin = np.clip(np.floor(ut.min(axis=1) - 0.5).astype(np.int64), 0, s - 1)
        cmax = np.clip(np.floor(ut.max(axis=1) - 0.5).astype(np.int64), 0, s - 1)
        rmin = np.clip(np.floor(vt.min(axis=1) - 0.5).astype(np.int64), 0, s - 1)
        rmax = np.clip(np.floor(vt.max(axis=1) - 0.5).astype(np.int64), 0, s - 1)

        # --- rasterize: collect covered (pixel, depth, triangle) cells ------
        lin_parts: list[np.ndarray] = []
        dep_parts: list[np.ndarray] = []
        tri_parts: list[np.ndarray] = []
        bary_parts: list[np.ndarray] = []
        heights = rmax - rmin + 1
        widths = cmax - cmin + 1
        tri_ids = np.arange(idx.size, dtype=np.int64)

        for sl in _chunk_slices(heights, widths):
            bh = int(heights[sl].max())
            bw = int(widths[sl].max())
            dy = np.arange(bh, dtype=np.int64)[None, :, None]
            dx = np.arange(bw, dtype=np.int64)[None, None, :]

            py = rmin[sl][:, None, None] + dy + 0.5      # (T,bh,1)
            px = cmin[sl][:, None, None] + dx + 0.5      # (T,1,bw)
            u0 = ut[sl, 0][:, None, None]
            u1 = ut[sl, 1][:, None, None]
            u2 = ut[sl, 2][:, None, None]
            v0 = vt[sl, 0][:, None, None]
            v1 = vt[sl, 1][:, None, None]
            v2 = vt[sl, 2][:, None, None]
            a = area[sl][:, None, None]

            b0 = ((u1 - px) * (v2 - py) - (u2 - px) * (v1 - py)) / a
            b1 = ((u2 - px) * (v0 - py) - (u0 - px) * (v2 - py)) / a
            b2 = 1.0 - b0 - b1

            in_box = ((dy <= (rmax[sl] - rmin[sl])[:, None, None])
                      & (dx <= (cmax[sl] - cmin[sl])[:, None, None]))
            inside = in_box & (b0 >= 0.0) & (b1 >= 0.0) & (b2 >= 0.0)
            if not inside.any():
                continue

            w = (b0 * izt[sl, 0][:, None, None]
                 + b1 * izt[sl, 1][:, None, None]
                 + b2 * izt[sl, 2][:, None, None])
            dep = 1.0 / w                                # perspective-correct z
            lin = ((rmin[sl][:, None, None] + dy) * s
                   + (cmin[sl][:, None, None] + dx))

            lin_parts.append(np.broadcast_to(lin, dep.shape)[inside])
            dep_parts.append(dep[inside])
            tri_parts.append(
                np.broadcast_to(tri_ids[sl][:, None, None], dep.shape)[inside])
            bary_parts.append(
                np.stack([b0[inside], b1[inside], b2[inside]], axis=1))

        if not lin_parts:
            return self._background_result()

        lin = np.concatenate(lin_parts)
        dep = np.concatenate(dep_parts)
        tri = np.concatenate(tri_parts)
        bary = np.concatenate(bary_parts)

        # --- z-buffer resolve: stable sort by (pixel, depth) -----------------
        order = np.lexsort((dep, lin))       # primary lin, secondary dep
        lin_s = lin[order]
        first = np.concatenate(([True], lin_s[1:] != lin_s[:-1]))
        win = order[first]
        lin_u = lin_s[first]
        dep_u = dep[win]
        tri_u = tri[win]
        bary_u = bary[win]

        # --- per-pixel normals: perspective-correct vertex-normal interp -----
        # (smooth shading; isolated triangles reduce exactly to face normals)
        f_win = faces[idx[tri_u]]                        # (W,3) vertex ids
        iz_win = izt[tri_u]                              # (W,3) per-vertex 1/z (valid-tri space)
        n_win = (bary_u[:, 0:1] * vncam[f_win[:, 0]] * iz_win[:, 0:1]
                 + bary_u[:, 1:2] * vncam[f_win[:, 1]] * iz_win[:, 1:2]
                 + bary_u[:, 2:3] * vncam[f_win[:, 2]] * iz_win[:, 2:3])
        n_win *= dep_u[:, None]                          # divide by interp 1/z
        n_win /= np.linalg.norm(n_win, axis=1, keepdims=True)

        # --- shade winning pixels --------------------------------------------
        ndl = np.maximum(n_win @ self._light, 0.0)
        ndh = np.maximum(n_win @ self._half, 0.0)
        shade = ALBEDO * (AMBIENT + ndl)[:, None] + SPECULAR * ndh[:, None] ** SHININESS
        shade_u8 = np.rint(np.clip(shade, 0.0, 1.0) * 255.0).astype(np.uint8)

        # --- compose output channels -----------------------------------------
        depth_flat = np.full(s * s, np.nan, dtype=np.float64)
        depth_flat[lin_u] = dep_u
        normal_flat = np.zeros((s * s, 3), dtype=np.float64)
        normal_flat[lin_u] = n_win
        rgb_flat = np.empty((s * s, 3), dtype=np.uint8)
        rgb_flat[:] = np.asarray(BACKGROUND_RGB, dtype=np.uint8)
        rgb_flat[lin_u] = shade_u8
        mask_flat = np.zeros(s * s, dtype=bool)
        mask_flat[lin_u] = True

        return RenderResult(
            rgb=rgb_flat.reshape(s, s, 3),
            depth=depth_flat.reshape(s, s).astype(np.float32),
            normal=normal_flat.reshape(s, s, 3).astype(np.float32),
            mask=mask_flat.reshape(s, s),
        )

    def _background_result(self) -> RenderResult:
        s = self._size
        rgb = np.empty((s, s, 3), dtype=np.uint8)
        rgb[:] = np.asarray(BACKGROUND_RGB, dtype=np.uint8)
        return RenderResult(
            rgb=rgb,
            depth=np.full((s, s), np.nan, dtype=np.float32),
            normal=np.zeros((s, s, 3), dtype=np.float32),
            mask=np.zeros((s, s), dtype=bool),
        )


# --- camera helpers -----------------------------------------------------------

def _bbox_center_diag(mesh_bbox: np.ndarray) -> tuple[np.ndarray, float]:
    bb = np.asarray(mesh_bbox, dtype=np.float64)
    if bb.shape != (2, 3):
        raise ValueError("mesh_bbox must have shape (2,3): [[min],[max]]")
    mn, mx = bb[0], bb[1]
    center = 0.5 * (mn + mx)
    diag = float(np.linalg.norm(mx - mn))
    if diag <= 0.0:
        raise ValueError("degenerate mesh_bbox (zero diagonal)")
    return center, diag


def _fit_fov_deg(diag: float, distance: float, margin: float = 1.15) -> float:
    """Fov that frames the bbox bounding sphere with `margin`."""
    return math.degrees(2.0 * math.atan((0.5 * diag * margin) / distance))


def front_camera(mesh_bbox: np.ndarray, image_size: int = 256) -> Camera:
    """Centered camera on +z, distance ~3x bbox diagonal, fit 1.15 margin."""
    center, diag = _bbox_center_diag(mesh_bbox)
    dist = 3.0 * diag
    position = center + np.array([0.0, 0.0, dist])
    return Camera(
        position=tuple(float(x) for x in position),
        target=tuple(float(x) for x in center),
        up=(0.0, 1.0, 0.0),
        fov_deg=_fit_fov_deg(diag, dist),
        image_size=int(image_size),
    )


def oblique_camera(mesh_bbox: np.ndarray, azimuth_deg: float = 40.0,
                   image_size: int = 256) -> Camera:
    """Front camera rotated by azimuth around +y (up); elevation as front."""
    center, diag = _bbox_center_diag(mesh_bbox)
    dist = 3.0 * diag
    th = math.radians(azimuth_deg)
    offset = np.array([math.sin(th) * dist, 0.0, math.cos(th) * dist])
    position = center + offset
    return Camera(
        position=tuple(float(x) for x in position),
        target=tuple(float(x) for x in center),
        up=(0.0, 1.0, 0.0),
        fov_deg=_fit_fov_deg(diag, dist),
        image_size=int(image_size),
    )
