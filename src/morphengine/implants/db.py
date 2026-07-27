"""Implant database — maps implant product SKUs to geometric parameters.

Implements the SPEC.md §2.8 contract. Starter data ships in
``morphengine/implants/data/implants.json`` (SPEC §2.9).
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .schema import ImplantParams, ImplantSKU, Placement, Shape

_DATA_PACKAGE = "morphengine.implants.data"
_DATA_RESOURCE = "implants.json"


class ImplantDB:
    """In-memory implant SKU database (SPEC §2.8)."""

    def __init__(self, skus: list[ImplantSKU]):
        self._skus: list[ImplantSKU] = list(skus)
        self._by_id: dict[str, ImplantSKU] = {}
        for sku in self._skus:
            if sku.sku_id in self._by_id:
                raise ValueError(f"duplicate sku_id in implant database: {sku.sku_id!r}")
            self._by_id[sku.sku_id] = sku

    @classmethod
    def from_json(cls, path: str | Path | None = None) -> "ImplantDB":
        """Load an ImplantDB from a JSON file.

        ``path=None`` loads the bundled ``implants/data/implants.json`` via
        ``importlib.resources``. The JSON document is an array of records
        conforming to :class:`ImplantSKU`.
        """
        if path is None:
            text = (
                resources.files(_DATA_PACKAGE)
                .joinpath(_DATA_RESOURCE)
                .read_text(encoding="utf-8")
            )
        else:
            text = Path(path).read_text(encoding="utf-8")
        raw = json.loads(text)
        records = raw["skus"] if isinstance(raw, dict) else raw
        return cls([ImplantSKU.model_validate(record) for record in records])

    def get(self, sku_id: str) -> ImplantSKU:
        """Return the SKU with the given id, or raise KeyError with a clear message."""
        try:
            return self._by_id[sku_id]
        except KeyError:
            raise KeyError(
                f"unknown sku_id {sku_id!r}; database holds {len(self._by_id)} SKUs "
                f"(e.g. {', '.join(sorted(self._by_id)[:3])}, ...)"
            ) from None

    def find(
        self,
        *,
        brand: str | None = None,
        shape: Shape | str | None = None,
        profile_class: str | None = None,
        min_cc: float | None = None,
        max_cc: float | None = None,
        placement: Placement | str | None = None,
    ) -> list[ImplantSKU]:
        """Return all SKUs matching every given filter (AND semantics).

        ``shape``/``placement`` accept enum members or their string values.
        ``placement`` matches SKUs that offer that placement option.
        No filters → all SKUs, in database order.
        """
        shape_norm = Shape(shape) if shape is not None else None
        placement_norm = Placement(placement) if placement is not None else None

        def matches(sku: ImplantSKU) -> bool:
            if brand is not None and sku.brand != brand:
                return False
            if shape_norm is not None and sku.shape is not shape_norm:
                return False
            if profile_class is not None and sku.profile_class != profile_class:
                return False
            if min_cc is not None and sku.volume_cc < min_cc:
                return False
            if max_cc is not None and sku.volume_cc > max_cc:
                return False
            if placement_norm is not None and placement_norm not in sku.placement_options:
                return False
            return True

        return [sku for sku in self._skus if matches(sku)]

    def to_params(self, sku_id: str, placement: Placement | str) -> ImplantParams:
        """Map a SKU + chosen placement to the ImplantParams consumed by MorphEngine.

        Raises ValueError if ``placement`` is not among the SKU's placement options
        (or is not a valid Placement value at all).
        """
        sku = self.get(sku_id)
        placement = Placement(placement)
        if placement not in sku.placement_options:
            options = ", ".join(p.value for p in sku.placement_options)
            raise ValueError(
                f"placement {placement.value!r} not available for SKU {sku.sku_id!r}; "
                f"options: {options}"
            )
        return ImplantParams(
            volume_cc=sku.volume_cc,
            base_width_cm=sku.base_width_cm,
            projection_cm=sku.projection_cm,
            shape=sku.shape,
            placement=placement,
        )
