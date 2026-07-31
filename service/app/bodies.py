"""Body provisioning seam (plan.md Track A / Track B boundary).

``BodyProvider`` is the minimal interface the service needs to obtain a body
for a morph job: a watertight mesh, its chest landmarks, and the JSON-
serializable construction parameters (echoed back to the client).

Track B ships ``FixtureBodyProvider`` (synthetic torso fixtures only — zero
external assets, per SPEC §0). Track A (later) plugs a real body-model
provider (e.g. Anny/MHR) in by implementing the same Protocol and passing it
to ``service.app.main.create_app(body_provider=...)`` — no route or runner
changes required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import trimesh

from morphengine.datafactory.bodies import BodySampler
from morphengine.geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
from morphengine.geometry.landmarks import ChestLandmarks

__all__ = ["BodyProvider", "FixtureBodyProvider"]


@runtime_checkable
class BodyProvider(Protocol):
    """Anything that can produce a (mesh, landmarks, body_params) triple.

    Contract:
    - ``mesh`` is a watertight ``trimesh.Trimesh`` in centimeters (SPEC §0
      axes: x mediolateral, y up, z anterior).
    - ``landmarks`` is a ``ChestLandmarks`` consistent with that mesh.
    - ``body_params`` is a JSON-serializable dict describing the body, safe
      to echo to API clients.
    - ``sample`` must be deterministic for a given ``seed``.
    """

    def sample(self, seed: int) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        ...


class FixtureBodyProvider:
    """BodyProvider backed by the synthetic BodySampler (SPEC §M1.1).

    ``resolution`` controls icosphere subdivisions of the fixture mesh
    (default 6 ≈ 40k verts; matches BodySampler's service-grade default).
    """

    def __init__(self, resolution: int = 6):
        self.resolution = int(resolution)

    def sample(self, seed: int) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        return BodySampler(seed=seed, resolution=self.resolution).sample()

    def from_params(self, body_params: dict) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        """Build the exact fixture body described by ``body_params``.

        Used when a request carries explicit ``body_params`` (they win over
        the seed). Missing keys fall back to ``synthetic_torso`` defaults.
        """
        params = dict(body_params)
        mesh = synthetic_torso(**params)
        landmarks = FixtureLandmarkProvider().locate(mesh)
        return mesh, landmarks, params
