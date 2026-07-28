"""Tests for datafactory.render (SPEC M1.2 / M1.6).

CI-friendly: fixture renders use synthetic_torso(resolution=4) at 64-128 px.
"""

import numpy as np
import pytest
import trimesh

from morphengine.datafactory.render import (
    BACKGROUND_RGB,
    Camera,
    RenderResult,
    SoftwareRenderer,
    front_camera,
    oblique_camera,
)
from morphengine.geometry.fixtures import synthetic_torso

SIZE = 96


@pytest.fixture(scope="module")
def torso():
    return synthetic_torso(resolution=4)


@pytest.fixture(scope="module")
def front_result(torso):
    cam = front_camera(torso.bounds, image_size=SIZE)
    return SoftwareRenderer(cam).render(torso)


def _two_triangle_mesh():
    """Two overlapping triangles facing +z, near one (z=2) tilted so its
    shade differs from the far one (z=0); both wound CCW seen from +z so
    their normals point at the camera."""
    # near tri is smaller and centered so it only partly covers the far tri
    verts = np.array([
        [-1.5, -1.5, 0.0], [1.5, -1.5, 0.0], [0.0, 1.5, 0.0],      # far
        [-0.5, -0.5, 2.0], [0.5, -0.5, 2.0], [0.0, 0.6, 2.5],      # near (tilted)
    ])
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False)


class TestShapesDtypes:
    def test_channels(self, front_result):
        r = front_result
        assert isinstance(r, RenderResult)
        assert r.rgb.shape == (SIZE, SIZE, 3) and r.rgb.dtype == np.uint8
        assert r.depth.shape == (SIZE, SIZE) and r.depth.dtype == np.float32
        assert r.normal.shape == (SIZE, SIZE, 3) and r.normal.dtype == np.float32
        assert r.mask.shape == (SIZE, SIZE) and r.mask.dtype == bool

    def test_camera_frozen(self, torso):
        cam = front_camera(torso.bounds, image_size=64)
        with pytest.raises(Exception):
            cam.image_size = 128  # frozen dataclass


class TestBackground:
    def test_nan_depth_background(self, front_result):
        r = front_result
        assert (~r.mask).any(), "expected some background pixels"
        assert np.isnan(r.depth[~r.mask]).all()
        assert np.isfinite(r.depth[r.mask]).all()

    def test_zero_normal_background(self, front_result):
        r = front_result
        assert (r.normal[~r.mask] == 0.0).all()
        norms = np.linalg.norm(r.normal[r.mask], axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    def test_rgb_background(self, front_result):
        r = front_result
        bg = np.asarray(BACKGROUND_RGB, dtype=np.uint8)
        assert (r.rgb[~r.mask] == bg).all()
        # foreground is shaded with the skin albedo, not the background color
        assert (r.rgb[r.mask] != bg).any(axis=1).all()


class TestZBuffer:
    def test_nearer_triangle_wins(self):
        mesh = _two_triangle_mesh()
        cam = Camera(position=(0.0, 0.0, 10.0), target=(0.0, 0.0, 0.0),
                     up=(0.0, 1.0, 0.0), fov_deg=30.0, image_size=64)
        r = SoftwareRenderer(cam).render(mesh)

        center = r.depth[32, 32]
        # camera at z=10 -> near tri surface ~7.79 cm away, far tri 10 cm away
        assert center == pytest.approx(7.7876, abs=1e-3)
        # far-only region: lower-left of the far triangle, outside the near
        # tri's screen footprint (near tri projects to rows ~22-39, cols ~24-40)
        far_px = r.depth[45, 20]
        assert far_px == pytest.approx(10.0, abs=1e-3)
        # overlap is shallower (nearer) than far-only pixels
        assert center < far_px
        # near tri is tilted -> its shade differs from the flat far tri
        assert not np.array_equal(r.rgb[32, 32], r.rgb[45, 20])
        # inside the overlap the winning normal is the near triangle's
        # (world normal (0,-0.5,1.1) -> camera space (0,-0.4138,-0.9104))
        np.testing.assert_allclose(r.normal[32, 32], [0.0, -0.4138, -0.9104],
                                   atol=1e-4)

    def test_no_cull_renders_backfaces(self):
        mesh = _two_triangle_mesh()
        # flip winding -> normals point away from the camera
        mesh.faces = mesh.faces[:, ::-1]
        cam = Camera(position=(0.0, 0.0, 10.0), target=(0.0, 0.0, 0.0),
                     up=(0.0, 1.0, 0.0), fov_deg=30.0, image_size=64)
        assert not SoftwareRenderer(cam).render(mesh).mask.any()
        assert SoftwareRenderer(cam, backface_cull=False).render(mesh).mask.any()


class TestDeterminism:
    def test_same_mesh_same_arrays(self, torso):
        cam = front_camera(torso.bounds, image_size=64)
        r1 = SoftwareRenderer(cam).render(torso)
        r2 = SoftwareRenderer(cam).render(torso)
        assert np.array_equal(r1.rgb, r2.rgb)
        assert np.array_equal(r1.depth, r2.depth, equal_nan=True)
        assert np.array_equal(r1.normal, r2.normal)
        assert np.array_equal(r1.mask, r2.mask)

    def test_repeat_same_renderer(self, torso):
        renderer = SoftwareRenderer(front_camera(torso.bounds, image_size=64))
        r1 = renderer.render(torso)
        r2 = renderer.render(torso)
        assert np.array_equal(r1.rgb, r2.rgb)
        assert np.array_equal(r1.depth, r2.depth, equal_nan=True)


class TestMaskBboxSanity:
    def test_mask_bbox_matches_mesh_framing(self, torso, front_result):
        r = front_result
        frac = r.mask.mean()
        assert 0.05 < frac < 0.95, f"implausible mask fraction {frac}"

        rows = np.nonzero(r.mask.any(axis=1))[0]
        cols = np.nonzero(r.mask.any(axis=0))[0]
        # fully inside the image with margin (1.15 fit margin => not touching)
        assert rows[0] > 0 and cols[0] > 0
        assert rows[-1] < SIZE - 1 and cols[-1] < SIZE - 1
        # roughly centered
        rc = (rows[0] + rows[-1]) / 2
        cc = (cols[0] + cols[-1]) / 2
        assert abs(rc - (SIZE - 1) / 2) < 0.2 * SIZE
        assert abs(cc - (SIZE - 1) / 2) < 0.2 * SIZE
        # torso is taller than wide: mask bbox aspect should agree
        mn, mx = torso.bounds
        mesh_aspect = (mx[0] - mn[0]) / (mx[1] - mn[1])  # width / height
        mask_aspect = (cols[-1] - cols[0] + 1) / (rows[-1] - rows[0] + 1)
        assert mask_aspect == pytest.approx(mesh_aspect, rel=0.15)

    def test_depth_range_within_camera_distance(self, torso, front_result):
        cam = front_camera(torso.bounds, image_size=SIZE)
        dist = float(np.linalg.norm(np.asarray(cam.position) - np.asarray(cam.target)))
        diag = float(np.linalg.norm(torso.bounds[1] - torso.bounds[0]))
        d = front_result.depth[front_result.mask]
        # visible surface lies within one bbox half-diagonal of the target
        assert (d > 0).all()
        assert (d > dist - 0.5 * diag).all() and (d < dist + 0.5 * diag).all()


class TestCameraHelpers:
    def test_front_camera_geometry(self, torso):
        cam = front_camera(torso.bounds, image_size=128)
        center = torso.bounds.mean(axis=0)
        diag = float(np.linalg.norm(torso.bounds[1] - torso.bounds[0]))
        np.testing.assert_allclose(cam.target, center)
        # on +z axis through the center at ~3x diagonal
        assert cam.position[0] == pytest.approx(center[0])
        assert cam.position[1] == pytest.approx(center[1])
        assert cam.position[2] - center[2] == pytest.approx(3.0 * diag)
        assert cam.up == (0.0, 1.0, 0.0)
        assert 0.0 < cam.fov_deg < 90.0

    def test_oblique_rotation_and_framing(self, torso):
        front = front_camera(torso.bounds, image_size=64)
        obl = oblique_camera(torso.bounds, azimuth_deg=40.0, image_size=64)
        # same target, same distance, same fov; position rotated around +y
        np.testing.assert_allclose(obl.target, front.target)
        d_f = np.linalg.norm(np.asarray(front.position) - np.asarray(front.target))
        d_o = np.linalg.norm(np.asarray(obl.position) - np.asarray(obl.target))
        assert d_o == pytest.approx(d_f)
        assert obl.fov_deg == pytest.approx(front.fov_deg)
        assert obl.position[1] == pytest.approx(front.position[1])  # elevation same
        assert obl.position[0] > front.position[0]  # +azimuth swings toward +x

        rf = SoftwareRenderer(front).render(torso)
        ro = SoftwareRenderer(obl).render(torso)
        assert rf.mask.any() and ro.mask.any()
        assert not np.array_equal(rf.rgb, ro.rgb)  # genuinely different view

    def test_default_image_size(self, torso):
        assert front_camera(torso.bounds).image_size == 256
        assert oblique_camera(torso.bounds).image_size == 256

    def test_bad_bbox_rejected(self):
        with pytest.raises(ValueError):
            front_camera(np.zeros((3, 3)))
        with pytest.raises(ValueError):
            front_camera(np.zeros((2, 3)))  # zero diagonal


class TestEmptyScene:
    def test_all_background(self):
        # single triangle facing away from the camera
        mesh = trimesh.Trimesh(vertices=[[-1, -1, 0], [0, 1, 0], [1, -1, 0]],
                               faces=[[0, 1, 2]], process=False)
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0), up=(0, 1, 0),
                     fov_deg=30.0, image_size=32)
        r = SoftwareRenderer(cam).render(mesh)
        assert not r.mask.any()
        assert np.isnan(r.depth).all()
        assert (r.normal == 0).all()
        assert (r.rgb == np.asarray(BACKGROUND_RGB, dtype=np.uint8)).all()
