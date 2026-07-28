"""PairDataset tests (SPEC M1.6) — synthesizes a 4-pair dataset in tmp_path.

Self-contained: writes small PNGs + npy channels + manifest.jsonl per the
SPEC M1.3 layout (64x64). Does NOT depend on morphengine.datafactory.
"""

import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from PIL import Image

from morphengine.painter.dataset import PairDataset

SIZE = 64


def _synth_dataset(root, n_pairs: int = 4) -> list[dict]:
    """Write n_pairs of (before/after PNG + cond npy + manifest row)."""
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "cond").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n_pairs):
        pair_id = f"{i:05d}_test-sku-350-hp_submuscular_front"
        volume_cc = 250 + 50 * i

        before = rng.integers(0, 256, (SIZE, SIZE, 3), dtype=np.uint8)
        after = np.clip(before.astype(int) + (i + 1) * 3, 0, 255).astype(np.uint8)
        Image.fromarray(before).save(root / "images" / f"{pair_id}_before.png")
        Image.fromarray(after).save(root / "images" / f"{pair_id}_after.png")

        # Body = central disk; depth in cm, bg NaN; mask bool; normals unit.
        yy, xx = np.mgrid[0:SIZE, 0:SIZE]
        disk = (yy - SIZE // 2) ** 2 + (xx - SIZE // 2) ** 2 < (SIZE // 3) ** 2
        depth_before = np.full((SIZE, SIZE), np.nan, dtype=np.float32)
        depth_before[disk] = 100.0 + 5.0 * np.sin(yy[disk] / 8.0)
        depth_after = np.full((SIZE, SIZE), np.nan, dtype=np.float32)
        depth_after[disk] = depth_before[disk] - 2.0  # protrudes toward camera

        normal_after = np.zeros((SIZE, SIZE, 3), dtype=np.float32)
        normal_after[..., 0] = np.where(disk, xx / SIZE * 2 - 1, 0.0)
        normal_after[..., 1] = np.where(disk, yy / SIZE * 2 - 1, 0.0)
        normal_after[..., 2] = np.where(disk, 0.8, 0.0)

        mask_before = disk

        np.save(root / "cond" / f"{pair_id}_depth_before.npy", depth_before)
        np.save(root / "cond" / f"{pair_id}_depth_after.npy", depth_after)
        np.save(root / "cond" / f"{pair_id}_normal_after.npy", normal_after)
        np.save(root / "cond" / f"{pair_id}_mask_before.npy", mask_before)

        rows.append(
            {
                "pair_id": pair_id,
                "sku_id": "test-sku-350-hp",
                "volume_cc": volume_cc,
                "placement": "submuscular",
                "camera_kind": "front",
                "image_size": SIZE,
                "files": {
                    "before": f"images/{pair_id}_before.png",
                    "after": f"images/{pair_id}_after.png",
                    "depth_before": f"cond/{pair_id}_depth_before.npy",
                    "depth_after": f"cond/{pair_id}_depth_after.npy",
                    "normal_after": f"cond/{pair_id}_normal_after.npy",
                    "mask_before": f"cond/{pair_id}_mask_before.npy",
                },
                "prompt": (
                    "photorealistic breast augmentation result, "
                    f"{volume_cc} cc High Profile round implant, "
                    "submuscular placement, front view, same person, "
                    "natural skin tone"
                ),
                "seed": 0,
            }
        )
    with open(root / "manifest.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return rows


def test_len(tmp_path):
    rows = _synth_dataset(tmp_path)
    ds = PairDataset(tmp_path / "manifest.jsonl", image_size=SIZE)
    assert len(ds) == len(rows) == 4


def test_tensor_shapes_and_ranges(tmp_path):
    _synth_dataset(tmp_path)
    ds = PairDataset(tmp_path / "manifest.jsonl", image_size=SIZE)
    item = ds[0]
    assert set(item) >= {"before", "after", "cond", "prompt"}
    assert item["before"].shape == (3, SIZE, SIZE)
    assert item["after"].shape == (3, SIZE, SIZE)
    assert item["cond"].shape == (6, SIZE, SIZE)
    for key in ("before", "after", "cond"):
        assert item[key].dtype == torch.float32
    assert item["before"].min() >= -1.0 and item["before"].max() <= 1.0
    assert item["after"].min() >= -1.0 and item["after"].max() <= 1.0
    assert item["cond"].min() >= 0.0 and item["cond"].max() <= 1.0
    # cond channel semantics: bg (outside disk) is 0 everywhere.
    mask_ch = item["cond"][5]
    assert set(torch.unique(mask_ch).tolist()) == {0.0, 1.0}
    bg = mask_ch == 0
    assert torch.all(item["cond"][:, bg] == 0.0)
    # depths have real dynamic range on the body (normalized, not constant).
    assert item["cond"][0][~bg].max() > item["cond"][0][~bg].min()
    # normals remapped from [-1,1] into [0,1]: body z-normal 0.8 -> 0.9.
    assert torch.isclose(
        item["cond"][4][~bg].mean(), torch.tensor(0.9), atol=1e-5
    )


def test_resize_to_smaller_image_size(tmp_path):
    _synth_dataset(tmp_path)
    ds = PairDataset(tmp_path / "manifest.jsonl", image_size=32)
    item = ds[1]
    assert item["before"].shape == (3, 32, 32)
    assert item["cond"].shape == (6, 32, 32)
    assert item["cond"].min() >= 0.0 and item["cond"].max() <= 1.0


def test_prompt_contains_volume(tmp_path):
    rows = _synth_dataset(tmp_path)
    ds = PairDataset(tmp_path / "manifest.jsonl", image_size=SIZE)
    for i, row in enumerate(rows):
        assert str(int(row["volume_cc"])) in ds[i]["prompt"]
