"""API tests for the morphengine service (Track B)."""

from __future__ import annotations

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from morphengine.implants.db import ImplantDB
from service.app.main import app

# Small images keep the suite fast; the render path is identical.
IMAGE_SIZE = 64


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def db() -> ImplantDB:
    return ImplantDB.from_json()


def _pick_sku(db: ImplantDB):
    """Preferred demo SKU, else any SKU from the DB."""
    return db._by_id.get("mentor-memorygel-250-hp") or next(
        iter(db._by_id.values()))


def _decode_png(b64: str) -> Image.Image:
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img.load()
    return img


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["skus"] == 581


def test_implants_count(client):
    r = client.get("/implants")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 581
    expected = {"sku_id", "brand", "product_line", "profile_class",
                "volume_cc", "base_width_cm", "projection_cm", "shape"}
    assert expected <= set(data[0])


def test_implants_filters(client, db):
    brand = db._by_id["mentor-memorygel-250-hp"].brand if (
        "mentor-memorygel-250-hp" in db._by_id) else next(
            iter(db._by_id.values())).brand
    r = client.get("/implants", params={"brand": brand})
    assert r.status_code == 200
    by_brand = r.json()
    assert 0 < len(by_brand) < 581
    assert all(s["brand"] == brand for s in by_brand)

    r = client.get("/implants", params={"max_base_width_cm": 10.0})
    assert r.status_code == 200
    narrow = r.json()
    assert 0 < len(narrow) < 581
    assert all(s["base_width_cm"] <= 10.0 for s in narrow)

    r = client.get("/implants", params={"brand": "no-such-brand"})
    assert r.status_code == 200
    assert r.json() == []


def test_morph_ok(client, db):
    sku = _pick_sku(db)
    r = client.post("/morph", json={
        "sku_id": sku.sku_id,
        "placement": sku.placement_options[0].value,
        "seed": 0,
        "image_size": IMAGE_SIZE,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    for key in ("before_png_b64", "after_png_b64"):
        img = _decode_png(body[key])
        assert img.size == (IMAGE_SIZE, IMAGE_SIZE)
        assert img.tobytes()  # non-empty pixel data
    assert body["before_png_b64"] != body["after_png_b64"]

    engine = body["engine"]
    assert set(engine["achieved_volume_cc"]) == {"left", "right"}
    assert engine["guardrails"]["ok"] is True
    assert engine["guardrails"]["clamped"] is False
    assert engine["achieved_volume_cc"]["left"] == pytest.approx(
        sku.volume_cc, abs=2.0)

    assert body["sku"]["sku_id"] == sku.sku_id
    assert body["body_params"]  # construction params echoed back


def test_morph_unknown_sku_404(client):
    r = client.post("/morph", json={
        "sku_id": "definitely-not-a-sku",
        "placement": "submuscular",
        "image_size": IMAGE_SIZE,
    })
    assert r.status_code == 404


def test_morph_guardrail_gate_422(client, db):
    """Largest-base-width SKU vs a narrow chest (30 cm) must be gated."""
    big = max(db._by_id.values(), key=lambda s: s.base_width_cm)
    r = client.post("/morph", json={
        "sku_id": big.sku_id,
        "placement": big.placement_options[0].value,
        "body_params": {"chest_width_cm": 30.0, "resolution": 5},
        "image_size": IMAGE_SIZE,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["clamped"] is True
    assert detail["warnings"]


def test_index_smoke_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<select" in r.text and "/morph" in r.text


class TestAnnyServiceProvider:
    """End-to-end on a REAL body (plan.md Track A → Track B seam, post-GATE 1).

    Skipped unless the optional anny stack is installed; when it runs it is
    the full path: HTTP request -> Anny body -> morph -> PNG pair.
    """

    @pytest.fixture(scope="class")
    def anny_client(self):
        pytest.importorskip("anny", reason="anny not installed")
        from service.app.bodies import AnnyServiceBodyProvider
        from service.app.main import create_app
        return TestClient(create_app(body_provider=AnnyServiceBodyProvider()))

    def test_morph_real_body_200(self, anny_client, db):
        sku = _pick_sku(db)
        r = anny_client.post("/morph", json={
            "sku_id": sku.sku_id,
            "placement": "submuscular",
            "seed": 11,
            "image_size": IMAGE_SIZE,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["body_params"]["provider"] == "anny"
        before = _decode_png(data["before_png_b64"])
        after = _decode_png(data["after_png_b64"])
        assert before.size == after.size == (IMAGE_SIZE, IMAGE_SIZE)
        # morph actually changed the silhouette
        assert before.tobytes() != after.tobytes()

    def test_from_params_roundtrip(self, anny_client, db):
        """body_params echoed by one call must rebuild the same body."""
        sku = _pick_sku(db)
        r1 = anny_client.post("/morph", json={
            "sku_id": sku.sku_id, "placement": "submuscular",
            "seed": 5, "image_size": IMAGE_SIZE,
        })
        assert r1.status_code == 200, r1.text
        body_params = r1.json()["body_params"]
        r2 = anny_client.post("/morph", json={
            "sku_id": sku.sku_id, "placement": "submuscular",
            "body_params": body_params, "image_size": IMAGE_SIZE,
        })
        assert r2.status_code == 200, r2.text
        assert r2.json()["body_params"] == body_params
        # same body + same sku -> identical before render
        assert r1.json()["before_png_b64"] == r2.json()["before_png_b64"]


class TestPainterEndpoint:
    """POST /morph with painter=true (M4 service seam)."""

    @staticmethod
    def _make_stub():
        import numpy as np

        class _StubPainter:
            def __init__(self):
                self.calls = 0

            def paint_geometry(self, before_rgb, depth_before, depth_after,
                               normal_after, mask_before, steps=30, seed=0):
                self.calls += 1
                h, w = before_rgb.shape[:2]
                out = np.zeros((h, w, 3), np.float32)
                out[..., 0] = 0.9                    # unmistakable red tint
                return out

        return _StubPainter()

    def test_painter_true_uses_painter_output(self, db):
        from service.app.main import create_app
        stub = self._make_stub()
        client = TestClient(create_app(painter=stub))
        sku = _pick_sku(db)
        r = client.post("/morph", json={
            "sku_id": sku.sku_id, "placement": "submuscular",
            "seed": 3, "image_size": IMAGE_SIZE,
            "painter": True, "painter_steps": 5,
        })
        assert r.status_code == 200, r.text
        after = _decode_png(r.json()["after_png_b64"])
        px = after.convert("RGB").getpixel((IMAGE_SIZE // 2, IMAGE_SIZE // 2))
        assert px[0] > 200           # stub's red tint reached the PNG
        assert stub.calls == 1

    def test_painter_true_without_ckpt_503(self, client, db):
        sku = _pick_sku(db)
        r = client.post("/morph", json={
            "sku_id": sku.sku_id, "placement": "submuscular",
            "image_size": IMAGE_SIZE, "painter": True,
        })
        assert r.status_code == 503, r.text
