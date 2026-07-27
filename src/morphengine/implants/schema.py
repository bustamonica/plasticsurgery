"""Implant data models — contract frozen by SPEC.md §2.7. Do not modify."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Shape(str, Enum):
    ROUND = "round"
    ANATOMICAL = "anatomical"


class Placement(str, Enum):
    SUBGLANDULAR = "subglandular"
    SUBMUSCULAR = "submuscular"
    DUAL_PLANE = "dual-plane"


class ImplantSKU(BaseModel):
    sku_id: str
    brand: str
    product_line: str
    volume_cc: float = Field(gt=0)
    base_width_cm: float = Field(gt=0)
    projection_cm: float = Field(gt=0)
    shape: Shape
    profile_class: str
    placement_options: list[Placement]
    values_status: Literal["illustrative_placeholder", "verified"]
    source: str


class ImplantParams(BaseModel):
    """Geometric bundle consumed by MorphEngine."""

    volume_cc: float = Field(gt=0)
    base_width_cm: float = Field(gt=0)
    projection_cm: float = Field(gt=0)
    shape: Shape
    placement: Placement
