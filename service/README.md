# morphengine service (Track B)

FastAPI wrapper around the morph engine — the API contract the future
website calls.

## Run

```bash
pip install -e .[service]
uvicorn service.app.main:app --reload
```

Then open http://127.0.0.1:8000/ for a minimal smoke-test UI (SKU dropdown +
placement radio + before/after render), or use the API directly:

```bash
curl localhost:8000/health
# {"status":"ok","skus":581}

curl 'localhost:8000/implants?brand=Mentor&max_base_width_cm=12'
# [ImplantSummary, ...] — all 581 SKUs unfiltered

curl -X POST localhost:8000/morph \
  -H 'Content-Type: application/json' \
  -d '{"sku_id": "mentor-memorygel-250-hp", "placement": "submuscular", "seed": 0}'
# MorphResponse: {before_png_b64, after_png_b64, engine, sku, body_params}
```

## Endpoints

| Method | Path        | Notes |
|--------|-------------|-------|
| GET    | `/health`   | `{"status": "ok", "skus": <count>}` |
| GET    | `/implants` | All SKUs; filters `?brand=`, `?profile_class=`, `?max_base_width_cm=` |
| POST   | `/morph`    | `MorphRequest` → `MorphResponse`; 404 unknown SKU; **422 when guardrails gate** (`clamped or not ok`), warnings in `detail` |
| GET    | `/`         | Dependency-free vanilla-JS smoke-test form (not the product UI) |

## BodyProvider seam (for Track A)

The service never builds bodies itself — it asks a `BodyProvider`
(`service/app/bodies.py`):

```python
class BodyProvider(Protocol):
    def sample(self, seed: int) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]: ...
```

`FixtureBodyProvider` (synthetic torsos, zero external assets) is the
default. Track A's Anny-backed provider plugs in with **no route changes**:

```python
from service.app.main import create_app
app = create_app(body_provider=AnnyBodyProvider(...))
```

If the provider also implements `from_params(body_params)`, explicit
`body_params` in a `MorphRequest` win over `seed`.

## Tests

```bash
python3 -m pytest service/tests -q
```
