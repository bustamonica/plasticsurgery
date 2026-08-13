import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def make_pair(tmp_path):
    """Factory: write a raw-layout pair folder (clinic/pair_id) with real JPEGs."""

    def _make(
        pair_id: str,
        meta: dict,
        clinic: str = "clinic01",
        size: tuple[int, int] = (768, 1024),
        before_rgb: tuple[int, int, int] = (200, 180, 170),
        after_rgb: tuple[int, int, int] = (190, 170, 160),
        images: tuple = None,
    ) -> Path:
        """`images` overrides the flat fills with (before, after) BGR arrays."""
        from PIL import Image

        folder = tmp_path / "raw" / clinic / pair_id
        folder.mkdir(parents=True, exist_ok=True)
        if images is None:
            Image.new("RGB", size, before_rgb).save(folder / "before.jpg", "JPEG")
            Image.new("RGB", size, after_rgb).save(folder / "after.jpg", "JPEG")
        else:
            for stem, bgr in zip(("before", "after"), images):
                Image.fromarray(bgr[:, :, ::-1]).save(folder / f"{stem}.jpg", "JPEG", quality=95)
        import json

        (folder / "meta.json").write_text(json.dumps(meta))
        return folder

    return _make


@pytest.fixture()
def valid_meta() -> dict:
    # `clothing` is optional in dataset_schema.json but mandatory at ingest: an
    # absent value makes build_caption() instruct the model to preserve clothing
    # that is not in a nude photograph.
    return {
        "pair_id": "clinic01-0001",
        "shape": "round",
        "volume_cc": 350,
        "view": "front",
        "consent_ref": "clinic01-agreement-2026-05",
        "clothing": "nude",
    }


# --- Synthetic torso photos for the censorship detector --------------------
# Real patient imagery is never committed. These stand in for a clinical frame:
# a skin-toned torso on a dark studio backdrop, with enough sensor-like noise
# that "this patch has no texture" is a meaningful statement about it.

SKIN_BGR = (150, 175, 205)
BACKDROP_BGR = (28, 28, 30)


def make_torso(size: tuple[int, int] = (768, 1024), seed: int = 7):
    """A clean synthetic clinical photo as a BGR array (width, height)."""
    import numpy as np

    w, h = size
    rng = np.random.default_rng(seed)
    image = np.full((h, w, 3), BACKDROP_BGR, dtype=np.uint8)
    import cv2

    cv2.ellipse(image, (w // 2, h // 2), (int(w * 0.33), int(h * 0.42)),
                0, 0, 360, SKIN_BGR, -1)
    # Skin texture: without it every patch is "texture-free" and the blur rule
    # has no baseline to measure against.
    noise = rng.normal(0, 7, (h, w, 3))
    shading = np.linspace(-18, 18, w)[None, :, None]
    return np.clip(image.astype(np.float32) + noise + shading, 0, 255).astype(np.uint8)


@pytest.fixture()
def torso():
    return make_torso()


@pytest.fixture()
def chest_box():
    """(x0, y0, x1, y1) of the chest region inside make_torso()'s silhouette."""

    def _box(image):
        h, w = image.shape[:2]
        return int(w * 0.30), int(h * 0.33), int(w * 0.70), int(h * 0.47)

    return _box
