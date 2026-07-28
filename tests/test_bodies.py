"""Tests for datafactory.bodies.BodySampler (SPEC §M1.6)."""

import numpy as np

from morphengine.datafactory.bodies import RANGES, BodySampler
from morphengine.geometry.fixtures import FixtureLandmarkProvider


def test_sample_n_count():
    bodies = BodySampler(seed=1, resolution=4).sample_n(4)
    assert len(bodies) == 4
    for mesh, lm, params in bodies:
        assert mesh is not None and lm is not None and isinstance(params, dict)


def test_all_watertight():
    for mesh, _, _ in BodySampler(seed=3, resolution=4).sample_n(5):
        assert mesh.is_watertight


def test_params_in_ranges():
    for _, _, params in BodySampler(seed=5, resolution=4).sample_n(10):
        for key, (lo, hi) in RANGES.items():
            assert lo <= params[key] <= hi, f"{key}={params[key]} outside [{lo}, {hi}]"


def test_landmark_roundtrip():
    """FixtureLandmarkProvider reconstructs the analytic landmarks of the
    exact construction kwargs recorded in body_params."""
    provider = FixtureLandmarkProvider()
    for mesh, lm, params in BodySampler(seed=7, resolution=4).sample_n(3):
        assert lm.chest_width_cm == params["chest_width_cm"]
        # nipple y is the construction breast_y; imf sits one radius below
        np.testing.assert_allclose(lm.nipple_left[1], params["breast_y_cm"])
        np.testing.assert_allclose(lm.imf_left[1],
                                   params["breast_y_cm"] - params["breast_radius_cm"])
        np.testing.assert_allclose(lm.nipple_left[0], params["breast_x_cm"])
        np.testing.assert_allclose(lm.nipple_right[0], -params["breast_x_cm"])
        # provider round-trips from mesh metadata alone
        lm2 = provider.locate(mesh)
        np.testing.assert_allclose(lm2.nipple_left, lm.nipple_left)
        assert lm2.chest_width_cm == lm.chest_width_cm


def test_seed_reproducibility():
    a = BodySampler(seed=42, resolution=4).sample_n(3)
    b = BodySampler(seed=42, resolution=4).sample_n(3)
    for (ma, la, pa), (mb, lb, pb) in zip(a, b):
        assert pa == pb
        np.testing.assert_array_equal(ma.vertices, mb.vertices)
        np.testing.assert_array_equal(la.nipple_left, lb.nipple_left)


def test_distinct_seeds_differ():
    a = BodySampler(seed=1, resolution=4).sample()
    b = BodySampler(seed=2, resolution=4).sample()
    assert a[2] != b[2]
