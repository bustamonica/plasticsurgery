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
    ) -> Path:
        from PIL import Image

        folder = tmp_path / "raw" / clinic / pair_id
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, before_rgb).save(folder / "before.jpg", "JPEG")
        Image.new("RGB", size, after_rgb).save(folder / "after.jpg", "JPEG")
        import json

        (folder / "meta.json").write_text(json.dumps(meta))
        return folder

    return _make


@pytest.fixture()
def valid_meta() -> dict:
    return {
        "pair_id": "clinic01-0001",
        "shape": "round",
        "volume_cc": 350,
        "view": "front",
        "consent_ref": "clinic01-agreement-2026-05",
    }
