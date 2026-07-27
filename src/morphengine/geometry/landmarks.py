"""Chest landmark contracts (SPEC §2.1). Body-model-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import numpy as np
import trimesh

Side = Literal["left", "right"]


@dataclass(frozen=True)
class BreastSide:
    """Convenience view of one breast's landmarks (SPEC §2.1)."""

    nipple: np.ndarray
    imf: np.ndarray
    lateral: np.ndarray
    medial: np.ndarray


@dataclass(frozen=True)
class ChestLandmarks:
    """Anatomic landmarks of the chest region. Units: cm. +x patient-left,
    +y up, +z anterior."""

    nipple_left: np.ndarray
    nipple_right: np.ndarray
    imf_left: np.ndarray
    imf_right: np.ndarray
    lateral_left: np.ndarray
    lateral_right: np.ndarray
    medial_left: np.ndarray
    medial_right: np.ndarray
    clavicle_mid: np.ndarray
    sternum_mid: np.ndarray
    chest_width_cm: float

    def breast_half(self, side: Side) -> BreastSide:
        if side == "left":
            return BreastSide(self.nipple_left, self.imf_left,
                              self.lateral_left, self.medial_left)
        if side == "right":
            return BreastSide(self.nipple_right, self.imf_right,
                              self.lateral_right, self.medial_right)
        raise ValueError(f"side must be 'left' or 'right', got {side!r}")


class LandmarkProvider(ABC):
    """Plug-in point: one implementation per body model (fixture, Anny, MHR...)."""

    @abstractmethod
    def locate(self, mesh: trimesh.Trimesh) -> ChestLandmarks:
        """Return chest landmarks for `mesh`. Raises ValueError if it cannot."""
