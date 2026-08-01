"""run_morph: MorphRequest -> MorphResponse.

Orchestrates body provisioning, the morph engine, and rendering. Route-level
HTTP semantics (404 unknown SKU, 422 guardrail gate) live in
``service.app.main`` — this function raises plain exceptions the route maps:

- ``KeyError``   -> 404 (unknown sku_id)
- ``ValueError`` -> 422 (placement unavailable for the SKU / bad body_params)
"""

from __future__ import annotations

import base64
import io

from PIL import Image

from morphengine.datafactory.render import (
    SoftwareRenderer,
    front_camera,
    oblique_camera,
)
from morphengine.implants.db import ImplantDB
from morphengine.morph.engine import MorphEngine

from .bodies import BodyProvider
from .models import EngineOut, GuardrailsOut, MorphRequest, MorphResponse

_CAMERAS = {"front": front_camera, "oblique": oblique_camera}


def _png_b64(rgb) -> str:
    """Encode an HxWx3 uint8 array as a base64 PNG string."""
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def run_morph(
    req: MorphRequest,
    db: ImplantDB,
    body_provider: BodyProvider,
    engine: MorphEngine | None = None,
    painter=None,
) -> MorphResponse:
    """Execute one morph job and render before/after PNGs.

    Explicit ``req.body_params`` wins over ``req.seed`` for body selection.
    One camera (fit to the BEFORE mesh bounds) is used for both renders so
    the two images are directly comparable.

    ``req.painter=True`` replaces the geometric after-render with the trained
    painter's photoreal output (conditioned on the after-state geometry).
    Raises ``RuntimeError`` when no painter is configured — the route maps it
    to 503.
    """
    engine = engine or MorphEngine()

    sku = db.get(req.sku_id)  # KeyError -> route maps to 404
    params = db.to_params(sku.sku_id, req.placement)  # ValueError -> 422

    if req.body_params is not None:
        from_params = getattr(body_provider, "from_params", None)
        if from_params is None:
            raise ValueError(
                "configured BodyProvider does not accept explicit body_params")
        mesh, landmarks, body_params = from_params(req.body_params)
    else:
        mesh, landmarks, body_params = body_provider.sample(req.seed)

    result = engine.morph(mesh, landmarks, params)

    camera = _CAMERAS[req.camera](mesh.bounds, image_size=req.image_size)
    renderer = SoftwareRenderer(camera)
    before = renderer.render(mesh)
    after = renderer.render(result.mesh)

    if req.painter:
        if painter is None:
            raise RuntimeError(
                "painter requested but no painter checkpoint is configured "
                "(create_app(painter=...))")
        after_rgb = painter.paint_geometry(
            before.rgb.astype("float32") / 255.0,
            before.depth, after.depth, after.normal, before.mask,
            steps=req.painter_steps, seed=req.seed)
        after_rgb = (after_rgb.clip(0, 1) * 255).astype("uint8")
    else:
        after_rgb = after.rgb

    guardrails = result.guardrails
    return MorphResponse(
        before_png_b64=_png_b64(before.rgb),
        after_png_b64=_png_b64(after_rgb),
        engine=EngineOut(
            achieved_volume_cc={k: float(v)
                                for k, v in result.achieved_volume_cc.items()},
            measurements=result.measurements,
            guardrails=GuardrailsOut(
                ok=bool(guardrails.ok),
                clamped=bool(guardrails.clamped),
                warnings=list(guardrails.warnings),
            ),
        ),
        sku=sku.model_dump(mode="json"),
        body_params=body_params,
    )
