"""Pydantic request/response models for the morph service API.

This module is the API contract the future website calls (plan.md Track B).
It re-exports the engine's ``Placement`` enum so request validation and the
implant DB share one source of truth.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from morphengine.implants.schema import Placement

__all__ = [
    "Placement",
    "MorphRequest",
    "MorphResponse",
    "ImplantSummary",
    "GuardrailsOut",
    "EngineOut",
    "SideMeasurements",
]


class MorphRequest(BaseModel):
    """One morph job: pick an implant, a placement, and a body.

    Body selection: explicit ``body_params`` (``synthetic_torso`` kwargs)
    wins over ``seed`` (draws from the seeded BodySampler).
    """

    sku_id: str
    placement: Placement
    seed: int = 0
    body_params: dict | None = None
    camera: Literal["front", "oblique"] = "front"
    image_size: int = Field(default=256, ge=32, le=1024)
    painter: bool = False        # True: photoreal after via the trained painter
    painter_steps: int = Field(default=30, ge=1, le=150)


class SideMeasurements(BaseModel):
    projection_cm: float
    base_width_cm: float


class GuardrailsOut(BaseModel):
    ok: bool
    clamped: bool
    warnings: list[str]


class EngineOut(BaseModel):
    achieved_volume_cc: dict[str, float]  # {"left": cc, "right": cc}
    measurements: dict[str, SideMeasurements]
    guardrails: GuardrailsOut


class MorphResponse(BaseModel):
    """Before/after render pair plus everything the client needs to label it."""

    before_png_b64: str
    after_png_b64: str
    engine: EngineOut
    sku: dict
    body_params: dict


class ImplantSummary(BaseModel):
    """Compact SKU card for listing/selection UIs."""

    sku_id: str
    brand: str
    product_line: str
    profile_class: str
    volume_cc: float
    base_width_cm: float
    projection_cm: float
    shape: str
