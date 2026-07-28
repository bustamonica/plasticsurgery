"""Dataset factory: engine + renderer → synthetic before/after pairs (SPEC §M1.3).

Each emitted pair is a rendered before/after image set plus geometry
conditioning maps and one manifest row. The morph engine's guardrails act as
a data-cleanliness gate: pairs whose morph clamps or warns are skipped and
resampled — a clamped pair is never emitted.

The default renderer is ``morphengine.datafactory.render.SoftwareRenderer``.
It is imported lazily (SPEC §M1.2 is implemented by a sibling module) so a
test double with the same ``Renderer(camera).render(mesh) -> RenderResult``
contract can be injected via ``renderer_cls``.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from ..implants.db import ImplantDB
from ..implants.schema import ImplantSKU, Placement, Shape
from ..morph.engine import MorphEngine
from .bodies import BodySampler

log = logging.getLogger(__name__)

# Weighted sampling tables (SPEC §M1.3).
_VOLUME_BANDS = [(0.80, lambda v: 200.0 <= v <= 500.0),
                 (0.15, lambda v: 500.0 < v <= 700.0),
                 (0.05, lambda v: v < 200.0 or v > 700.0)]
_PROFILE_WEIGHTS = {"moderate": 25.0, "moderate plus": 25.0,
                    "high": 35.0, "ultra high": 15.0}
_PLACEMENT_WEIGHTS = {Placement.SUBMUSCULAR: 55.0,
                      Placement.DUAL_PLANE: 30.0,
                      Placement.SUBGLANDULAR: 15.0}
_SHAPE_WEIGHTS = {Shape.ROUND: 80.0, Shape.ANATOMICAL: 20.0}
_CAMERA_WEIGHTS = {"front": 60.0, "oblique": 40.0}

MAX_ATTEMPTS_PER_PAIR = 5

PROMPT_TEMPLATE = ("photorealistic breast augmentation result, {volume_cc} cc "
                   "{profile_label} {shape} implant, {placement} placement, "
                   "{camera_kind} view, same person, natural skin tone")


def _weighted_choice(rng: np.random.Generator, options: list, weights: list[float]):
    w = np.asarray(weights, dtype=np.float64)
    w = w / w.sum()
    return options[int(rng.choice(len(options), p=w))]


class DatasetFactory:
    """Generate synthetic before/after conditioning pairs (SPEC §M1.3).

    ``renderer_cls=None`` lazily resolves to the SPEC §M1.2
    ``SoftwareRenderer``; inject any class with the same
    ``__init__(camera)`` / ``.render(mesh) -> RenderResult`` contract.
    """

    def __init__(self, out_dir: str | Path, db: ImplantDB | None = None,
                 engine: MorphEngine | None = None, renderer_cls=None,
                 resolution: int = 5):
        self.out_dir = Path(out_dir)
        self.db = db if db is not None else ImplantDB.from_json()
        self.engine = engine if engine is not None else MorphEngine()
        if renderer_cls is None:
            from .render import SoftwareRenderer  # SPEC §M1.2 (sibling module)
            renderer_cls = SoftwareRenderer
        self.renderer_cls = renderer_cls
        self.resolution = int(resolution)
        self._skus = self.db.find()  # all SKUs, deterministic DB order
        # per-run state (set by generate(); sensible defaults for direct
        # make_pair calls)
        self._image_size = 256
        self._seed = 0
        self._next_index = 0
        self.report_: dict = {}

    # -- rendering ----------------------------------------------------------

    def _camera(self, mesh, camera_kind: str, image_size: int):
        """Build the SPEC §M1.2 camera for this view (lazy sibling import)."""
        from .render import front_camera, oblique_camera
        bbox = mesh.bounds
        if camera_kind == "front":
            return front_camera(bbox, image_size=image_size)
        if camera_kind == "oblique":
            return oblique_camera(bbox, image_size=image_size)
        raise ValueError(f"camera_kind must be 'front' or 'oblique', got {camera_kind!r}")

    # -- pair pipeline ------------------------------------------------------

    def make_pair(self, mesh, lm, body_params: dict, sku: ImplantSKU,
                  placement: Placement, camera_kind: str) -> dict | None:
        """Morph + render both states of one pair; return its manifest row.

        Returns ``None`` when the engine's guardrails gate the pair
        (clamped parameters or any guardrail warning — e.g. implant/chest
        mismatch — means ``ok`` is False). Gated pairs are skipped by the
        caller and never written: no clamped pair is ever emitted.
        """
        placement = Placement(placement)
        params = self.db.to_params(sku.sku_id, placement)
        result = self.engine.morph(mesh, lm, params)
        guard = result.guardrails
        if guard.clamped or not guard.ok:
            log.info("skip pair sku=%s placement=%s: clamped=%s warnings=%s",
                     sku.sku_id, placement.value, guard.clamped, guard.warnings)
            return None

        size = int(self._image_size)
        camera = self._camera(mesh, camera_kind, size)
        renderer = self.renderer_cls(camera)
        before = renderer.render(mesh)
        after = renderer.render(result.mesh)

        pair_id = (f"{self._next_index:05d}_{sku.sku_id}_"
                   f"{placement.value}_{camera_kind}")
        files = self._write_pair(pair_id, before, after)
        self._next_index += 1

        profile_label = sku.profile_label or sku.profile_class
        prompt = PROMPT_TEMPLATE.format(
            volume_cc=f"{sku.volume_cc:g}", profile_label=profile_label,
            shape=sku.shape.value, placement=placement.value,
            camera_kind=camera_kind)

        return {
            "pair_id": pair_id,
            "sku_id": sku.sku_id,
            "brand": sku.brand,
            "product_line": sku.product_line,
            "profile_class": sku.profile_class,
            "profile_label": sku.profile_label,
            "volume_cc": float(sku.volume_cc),
            "base_width_cm": float(sku.base_width_cm),
            "projection_cm": float(sku.projection_cm),
            "shape": sku.shape.value,
            "placement": placement.value,
            "camera_kind": camera_kind,
            "image_size": size,
            "body_params": body_params,
            "engine": {
                "achieved_volume_cc": {
                    "left": float(result.achieved_volume_cc["left"]),
                    "right": float(result.achieved_volume_cc["right"]),
                },
                "ok": bool(guard.ok),
                "warnings": list(guard.warnings),
            },
            "files": files,
            "prompt": prompt,
            "seed": int(self._seed),
        }

    def _write_pair(self, pair_id: str, before, after) -> dict:
        """Write PNG/npy channels for one pair; return out_dir-relative paths."""
        images = self.out_dir / "images"
        cond = self.out_dir / "cond"
        images.mkdir(parents=True, exist_ok=True)
        cond.mkdir(parents=True, exist_ok=True)

        rel = {}
        for tag, res in (("before", before), ("after", after)):
            Image.fromarray(np.asarray(res.rgb, dtype=np.uint8)).save(
                images / f"{pair_id}_{tag}.png")
            rel[tag] = f"images/{pair_id}_{tag}.png"
        np.save(cond / f"{pair_id}_depth_before.npy",
                np.asarray(before.depth, dtype=np.float32))
        np.save(cond / f"{pair_id}_depth_after.npy",
                np.asarray(after.depth, dtype=np.float32))
        np.save(cond / f"{pair_id}_normal_after.npy",
                np.asarray(after.normal, dtype=np.float32))
        np.save(cond / f"{pair_id}_mask_before.npy",
                np.asarray(before.mask, dtype=bool))
        rel.update({
            "depth_before": f"cond/{pair_id}_depth_before.npy",
            "depth_after": f"cond/{pair_id}_depth_after.npy",
            "normal_after": f"cond/{pair_id}_normal_after.npy",
            "mask_before": f"cond/{pair_id}_mask_before.npy",
        })
        return rel

    # -- weighted sampling ---------------------------------------------------

    def _draw_candidate(self, rng: np.random.Generator):
        """Draw (sku, placement, camera_kind) per the SPEC §M1.3 weights."""
        # 1. volume band: 200-500 cc 80% / 500-700 15% / else 5%
        band = int(rng.choice(3, p=[w for w, _ in _VOLUME_BANDS]))
        in_band = _VOLUME_BANDS[band][1]
        cands = [s for s in self._skus if in_band(s.volume_cc)]

        # 2. profile, weighted AMONG the profiles available at this volume
        avail = sorted({s.profile_class for s in cands})
        weighted = [p for p in avail if p in _PROFILE_WEIGHTS]
        if weighted:
            profile = _weighted_choice(rng, weighted,
                                       [_PROFILE_WEIGHTS[p] for p in weighted])
        else:  # only unweighted classes (e.g. "low") available at this volume
            profile = avail[int(rng.integers(len(avail)))]
        prof_cands = [s for s in cands if s.profile_class == profile]

        # 3. shape round 80 / anatomical 20
        shape = _weighted_choice(rng, list(_SHAPE_WEIGHTS),
                                 list(_SHAPE_WEIGHTS.values()))
        shape_cands = [s for s in prof_cands if s.shape is shape]
        pool = shape_cands or prof_cands or cands  # relax, never empty

        # 4. the SKU itself, uniform within the surviving pool
        sku = pool[int(rng.integers(len(pool)))]

        # 5. placement 55/30/15 restricted to sku.placement_options, renormalized
        options = [p for p in sku.placement_options if p in _PLACEMENT_WEIGHTS]
        placement = _weighted_choice(rng, options,
                                     [_PLACEMENT_WEIGHTS[p] for p in options])

        # 6. camera front 60 / oblique 40
        camera_kind = _weighted_choice(rng, list(_CAMERA_WEIGHTS),
                                       list(_CAMERA_WEIGHTS.values()))
        return sku, placement, camera_kind

    # -- full run -------------------------------------------------------------

    def generate(self, n_pairs: int, seed: int = 0,
                 image_size: int = 256) -> list[dict]:
        """Full run: seeded bodies + weighted sampling; writes images and an
        atomically-replaced ``manifest.jsonl``; returns the manifest rows.

        Every random draw comes from one seeded numpy Generator (plus the
        BodySampler's own generator, seeded identically), so two runs with
        the same seed produce byte-identical manifests. Guardrail-gated
        pairs are skipped and resampled (max 5 attempts per pair); any
        remaining shortfall is counted in ``self.report_``.
        """
        n_pairs = int(n_pairs)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._seed = int(seed)
        self._image_size = int(image_size)
        self._next_index = 0

        rng = np.random.default_rng(self._seed)
        sampler = BodySampler(seed=self._seed, resolution=self.resolution)

        rows: list[dict] = []
        skips = 0
        while len(rows) < n_pairs:
            row = None
            for _ in range(MAX_ATTEMPTS_PER_PAIR):
                mesh, lm, body_params = sampler.sample()
                sku, placement, camera_kind = self._draw_candidate(rng)
                row = self.make_pair(mesh, lm, body_params, sku, placement,
                                     camera_kind)
                if row is not None:
                    break
                skips += 1
            if row is None:
                log.warning("pair %d: %d attempts all gated; shortfall",
                            len(rows), MAX_ATTEMPTS_PER_PAIR)
                break  # avoid unbounded loops if the DB/config gates everything
            rows.append(row)

        manifest = self.out_dir / "manifest.jsonl"
        tmp = self.out_dir / "manifest.jsonl.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        os.replace(tmp, manifest)  # atomic publish

        self.report_ = {
            "requested": n_pairs,
            "written": len(rows),
            "skips": skips,
            "shortfall": n_pairs - len(rows),
            "by_brand": dict(sorted(Counter(r["brand"] for r in rows).items())),
        }
        return rows
