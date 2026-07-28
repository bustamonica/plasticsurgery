"""Synthetic body sampling for the data factory (SPEC §M1.1).

Draws randomized but fully reproducible torso fixtures via
``geometry.fixtures.synthetic_torso`` and returns the mesh, its analytic
landmarks, and the exact construction kwargs (for the manifest).
"""

from __future__ import annotations

import numpy as np
import trimesh

from ..geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
from ..geometry.landmarks import ChestLandmarks

# Uniform draw ranges (SPEC §M1.1), cm.
RANGES = {
    "chest_width_cm": (30.0, 42.0),
    "breast_radius_cm": (4.5, 7.0),
    "breast_projection_cm": (2.0, 4.5),
    "breast_x_cm": (5.5, 8.5),
    "breast_y_cm": (1.0, 3.5),
    "torso_depth_cm": (17.0, 24.0),
}

HEIGHT_CM = 50.0


class BodySampler:
    """Seeded sampler of synthetic torso bodies (SPEC §M1.1)."""

    def __init__(self, seed: int = 0, resolution: int = 5):
        self.seed = int(seed)
        self.resolution = int(resolution)
        self._rng = np.random.default_rng(self.seed)
        self._provider = FixtureLandmarkProvider()

    def sample(self) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        """Draw one body: (mesh, landmarks, body_params).

        ``body_params`` are the exact ``synthetic_torso`` kwargs used,
        JSON-serializable, suitable for the dataset manifest.
        """
        u = self._rng.uniform
        body_params = {
            "chest_width_cm": float(u(*RANGES["chest_width_cm"])),
            "breast_radius_cm": float(u(*RANGES["breast_radius_cm"])),
            "breast_projection_cm": float(u(*RANGES["breast_projection_cm"])),
            "breast_x_cm": float(u(*RANGES["breast_x_cm"])),
            "breast_y_cm": float(u(*RANGES["breast_y_cm"])),
            "torso_depth_cm": float(u(*RANGES["torso_depth_cm"])),
            "height_cm": HEIGHT_CM,
            "resolution": self.resolution,
        }
        mesh = synthetic_torso(**body_params)
        landmarks = self._provider.locate(mesh)
        return mesh, landmarks, body_params

    def sample_n(self, n: int) -> list[tuple[trimesh.Trimesh, ChestLandmarks, dict]]:
        """Draw ``n`` bodies in sequence (order is deterministic)."""
        return [self.sample() for _ in range(int(n))]
