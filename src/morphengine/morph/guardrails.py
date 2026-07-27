"""Implant↔chest compatibility checks (SPEC §2.5)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..geometry.landmarks import ChestLandmarks
from ..implants.schema import ImplantParams


@dataclass
class GuardrailResult:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    clamped: bool = False
    clamped_params: "ImplantParams | None" = None


def breast_base_radius_cm(lm: ChestLandmarks) -> float:
    """Mean existing breast base radius from landmarks (both sides)."""
    radii = []
    for side in ("left", "right"):
        half = lm.breast_half(side)
        radii.append(max(float(np.linalg.norm(half.lateral - half.nipple)),
                         float(np.linalg.norm(half.medial - half.nipple))))
    return float(np.mean(radii))


def check_compatibility(lm: ChestLandmarks, params: ImplantParams) -> GuardrailResult:
    """SPEC §2.5 rules:
    - base width > 0.90 × hemithorax width → clamp + warn
    - projection > 1.4 × existing base radius → warn
    - volume outside [100, 1000] cc → warn
    ok = no warnings and not clamped.
    """
    warnings: list[str] = []
    clamped = False
    clamped_params = None

    hemi_bound = 0.90 * lm.chest_width_cm / 2.0
    bw = params.base_width_cm
    if bw > hemi_bound:
        clamped = True
        warnings.append(
            f"implant base width {bw:.2f} cm exceeds 90% of hemithorax "
            f"({hemi_bound:.2f} cm) — clamped"
        )
        clamped_params = params.model_copy(update={"base_width_cm": hemi_bound})

    base_radius = breast_base_radius_cm(lm)
    if params.projection_cm > 1.4 * base_radius:
        warnings.append(
            f"projection {params.projection_cm:.2f} cm is large relative to "
            f"existing base radius {base_radius:.2f} cm"
        )

    if not (100.0 <= params.volume_cc <= 1000.0):
        warnings.append(f"volume {params.volume_cc:.0f} cc outside [100, 1000]")

    return GuardrailResult(ok=not warnings and not clamped,
                           warnings=warnings, clamped=clamped,
                           clamped_params=clamped_params)
