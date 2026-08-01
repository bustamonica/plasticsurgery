"""FastAPI service wrapping the morphengine morph engine (plan.md Track B).

API contract for the future website:
- GET  /health    -> {"status": "ok", "skus": <count>}
- GET  /implants  -> list[ImplantSummary] (?brand=&profile_class=&max_base_width_cm=)
- POST /morph     -> MorphResponse (before/after PNGs + engine + sku + body_params)
- GET  /          -> minimal smoke-test HTML form (vanilla JS, no deps)

The body source is pluggable via ``create_app(body_provider=...)`` — see
``service/app/bodies.py`` for the Track A (Anny) seam.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from morphengine.implants.db import ImplantDB

from .bodies import BodyProvider, FixtureBodyProvider
from .engine_runner import run_morph
from .models import ImplantSummary, MorphRequest, MorphResponse

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>morphengine — morph smoke test</title>
<style>
 body { font-family: sans-serif; margin: 2em; max-width: 900px; }
 label { display: block; margin: 0.6em 0 0.2em; }
 img { width: 384px; border: 1px solid #ccc; margin: 0.5em; }
 #result { margin-top: 1.5em; }
 #err { color: #a00; white-space: pre-wrap; }
</style>
</head>
<body>
<h1>morphengine smoke test</h1>
<form id="f">
  <label>Implant SKU</label>
  <select id="sku" name="sku"></select>
  <label>Placement</label>
  <input type="radio" name="placement" value="submuscular" checked> submuscular
  <input type="radio" name="placement" value="subglandular"> subglandular
  <input type="radio" name="placement" value="dual-plane"> dual-plane
  <br>
  <label>Body seed</label>
  <input id="seed" type="number" value="0" min="0">
  <label>Camera</label>
  <select id="camera"><option>front</option><option>oblique</option></select>
  <br><br>
  <button type="submit">Morph</button>
</form>
<div id="err"></div>
<div id="result"></div>
<script>
const sel = document.getElementById('sku');
fetch('/implants').then(r => r.json()).then(skus => {
  for (const s of skus) {
    const o = document.createElement('option');
    o.value = s.sku_id;
    o.textContent = `${s.brand} ${s.product_line} ${s.volume_cc}cc ${s.profile_class} (${s.sku_id})`;
    sel.appendChild(o);
  }
});
document.getElementById('f').addEventListener('submit', async e => {
  e.preventDefault();
  document.getElementById('err').textContent = '';
  document.getElementById('result').innerHTML = '<p>rendering…</p>';
  const fd = new FormData(e.target);
  const body = {
    sku_id: fd.get('sku'),
    placement: fd.get('placement'),
    seed: parseInt(document.getElementById('seed').value, 10),
    camera: document.getElementById('camera').value
  };
  const r = await fetch('/morph', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await r.json();
  if (!r.ok) {
    document.getElementById('result').innerHTML = '';
    document.getElementById('err').textContent =
      r.status + ' ' + JSON.stringify(data.detail, null, 2);
    return;
  }
  const g = data.engine.guardrails;
  document.getElementById('result').innerHTML =
    `<div><img src="data:image/png;base64,${data.before_png_b64}">` +
    `<img src="data:image/png;base64,${data.after_png_b64}"></div>` +
    `<p>before (left) / after (right) — achieved ` +
    `L ${data.engine.achieved_volume_cc.left.toFixed(1)} cc, ` +
    `R ${data.engine.achieved_volume_cc.right.toFixed(1)} cc` +
    (g.warnings.length ? ` — warnings: ${g.warnings.join('; ')}` : '') + `</p>`;
});
</script>
</body>
</html>
"""


def _to_summary(sku) -> ImplantSummary:
    return ImplantSummary(
        sku_id=sku.sku_id,
        brand=sku.brand,
        product_line=sku.product_line,
        profile_class=sku.profile_class,
        volume_cc=sku.volume_cc,
        base_width_cm=sku.base_width_cm,
        projection_cm=sku.projection_cm,
        shape=sku.shape.value,
    )


def create_app(body_provider: BodyProvider | None = None,
               db: ImplantDB | None = None,
               painter=None) -> FastAPI:
    """App factory. ``body_provider`` is the Track A seam: pass an Anny-backed
    provider later; defaults to the synthetic FixtureBodyProvider.

    ``painter`` is the M4 seam: an object with ``paint_geometry(...)`` (e.g.
    ``PainterInference.from_ckpt(dir)``) or a checkpoint-dir path — paths are
    lazy-loaded on first painter request (SDXL weights are heavy)."""
    app = FastAPI(title="morphengine service", version="0.1.0")
    app.state.db = db or ImplantDB.from_json()
    app.state.body_provider = body_provider or FixtureBodyProvider()
    app.state.painter = painter

    def _resolve_painter():
        p = app.state.painter
        if p is None or hasattr(p, "paint_geometry"):
            return p
        from morphengine.painter.inference import PainterInference
        app.state.painter = PainterInference.from_ckpt(p)
        return app.state.painter

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "skus": len(app.state.db._by_id)}

    @app.get("/implants", response_model=list[ImplantSummary])
    def implants(
        brand: str | None = Query(default=None),
        profile_class: str | None = Query(default=None),
        max_base_width_cm: float | None = Query(default=None),
    ) -> list[ImplantSummary]:
        db_: ImplantDB = app.state.db
        matches = db_.find(brand=brand, profile_class=profile_class)
        if max_base_width_cm is not None:
            matches = [s for s in matches
                       if s.base_width_cm <= max_base_width_cm]
        return [_to_summary(s) for s in matches]

    @app.post("/morph", response_model=MorphResponse)
    def morph(req: MorphRequest) -> MorphResponse:
        try:
            resp = run_morph(req, app.state.db, app.state.body_provider,
                             painter=_resolve_painter() if req.painter else None)
        except KeyError as exc:
            raise HTTPException(status_code=404,
                                detail=str(exc.args[0])) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        g = resp.engine.guardrails
        if g.clamped or not g.ok:
            raise HTTPException(status_code=422, detail={
                "message": "guardrails gate: implant/body pairing rejected",
                "clamped": g.clamped,
                "warnings": g.warnings,
            })
        return resp

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    return app


app = create_app()
