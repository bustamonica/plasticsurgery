"""PairDataset — loads before/after image pairs + geometry conditioning (SPEC M1.4).

Consumes the manifest.jsonl layout produced by ``morphengine.datafactory``
(SPEC M1.3). Each item returns:

    before : (3,H,W) float32 in [-1,1]
    after  : (3,H,W) float32 in [-1,1]
    cond   : (6,H,W) float32 in [0,1] =
             [depth_before, depth_after,
              normal_after_x, normal_after_y, normal_after_z, mask_before]
    prompt : str (from the manifest row)

Depths are normalized PER PAIR by the 1st–99th percentile of the body's valid
(non-NaN) depth range computed across BOTH depth maps of the pair; NaN /
background pixels are 0 after normalization.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

try:  # keep this module importable in torch-free environments
    from torch.utils.data import Dataset as _TorchDataset
except ImportError:  # pragma: no cover - exercised only without torch
    _TorchDataset = object


def _center_crop_square(img: Image.Image) -> Image.Image:
    """Center-crop a PIL image to a square (shortest side)."""
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    return img.crop((left, top, left + s, top + s))


def _load_image_resized(path: Path, image_size: int) -> np.ndarray:
    """Load an RGB image, center-crop + bilinear resize -> (H,W,3) float32 [0,1]."""
    img = Image.open(path).convert("RGB")
    if img.size != (image_size, image_size):
        img = _center_crop_square(img).resize(
            (image_size, image_size), Image.BILINEAR
        )
    return np.asarray(img, dtype=np.float32) / 255.0


def _nearest_resize(arr: np.ndarray, size: int) -> np.ndarray:
    """Index-based nearest-neighbor resize to (size, size) for npy channels.

    v0 simplification: nearest sampling is acceptable for depth/normal/mask
    conditioning maps (avoids inventing values at edges / across NaN bg);
    replace with proper interpolation (and NaN-aware filtering for depth)
    in v1.
    """
    if arr.shape[0] == size and arr.shape[1] == size:
        return arr
    src_h, src_w = arr.shape[0], arr.shape[1]
    ys = np.minimum((np.arange(size) * src_h) // size, src_h - 1)
    xs = np.minimum((np.arange(size) * src_w) // size, src_w - 1)
    if arr.ndim == 2:
        return arr[ys][:, xs]
    return arr[ys][:, xs, :]


class PairDataset(_TorchDataset):
    """torch.utils.data.Dataset over a datafactory manifest (SPEC M1.4).

    Subclasses ``torch.utils.data.Dataset`` when torch is installed; without
    torch the module still imports (the base falls back to ``object``) but
    ``__getitem__`` will raise on its lazy ``import torch``.

    Parameters
    ----------
    manifest : path to manifest.jsonl (one JSON object per pair; rows carry a
        ``files`` dict with paths relative to the manifest's directory).
    image_size : square output resolution (center-crop + resize).
    """

    def __init__(self, manifest: str | Path, image_size: int = 256):
        self.manifest_path = Path(manifest)
        self.root = self.manifest_path.parent
        self.image_size = int(image_size)
        self.rows: list[dict] = []
        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self.rows.append(json.loads(line))
        if not self.rows:
            raise ValueError(f"empty manifest: {self.manifest_path}")

    def __len__(self) -> int:
        return len(self.rows)

    def _load_pair(self, row: dict) -> dict:
        files = row["files"]
        size = self.image_size

        before = _load_image_resized(self.root / files["before"], size)
        after = _load_image_resized(self.root / files["after"], size)

        depth_before = _nearest_resize(
            np.load(self.root / files["depth_before"]).astype(np.float32), size
        )
        depth_after = _nearest_resize(
            np.load(self.root / files["depth_after"]).astype(np.float32), size
        )
        normal_after = _nearest_resize(
            np.load(self.root / files["normal_after"]).astype(np.float32), size
        )
        mask_before = _nearest_resize(
            np.load(self.root / files["mask_before"]).astype(np.float32), size
        )

        # --- per-pair depth normalization ----------------------------------
        # Body range = 1st–99th percentile of valid (non-NaN) depths across
        # BOTH depth maps of the pair. Camera-space z: nearer is smaller, so
        # invert after scaling so foreground (near) -> ~1, bg -> 0.
        valid = np.concatenate(
            [
                depth_before[np.isfinite(depth_before)],
                depth_after[np.isfinite(depth_after)],
            ]
        )
        if valid.size >= 2:
            lo, hi = np.percentile(valid, [1.0, 99.0])
        else:  # degenerate pair (no body pixels); keep zeros
            lo, hi = 0.0, 1.0
        span = max(float(hi - lo), 1e-6)

        def _norm_depth(d: np.ndarray) -> np.ndarray:
            out = np.zeros_like(d, dtype=np.float32)
            m = np.isfinite(d)
            out[m] = 1.0 - np.clip((d[m] - lo) / span, 0.0, 1.0)
            return out

        depth_before_n = _norm_depth(depth_before)
        depth_after_n = _norm_depth(depth_after)

        # Normals are unit vectors in [-1,1] (bg = 0) -> remap to [0,1];
        # bg pixels are then forced to 0 (same "NaN/bg -> 0" rule as depths,
        # bg identified by the NaN mask of depth_after).
        normal_after_n = np.clip(normal_after * 0.5 + 0.5, 0.0, 1.0).astype(
            np.float32
        )
        normal_after_n[~np.isfinite(depth_after)] = 0.0
        # Mask bool -> {0,1} float32.
        mask_before_f = (mask_before > 0.5).astype(np.float32)

        cond = np.concatenate(
            [
                depth_before_n[..., None],
                depth_after_n[..., None],
                normal_after_n,
                mask_before_f[..., None],
            ],
            axis=-1,
        )  # (H,W,6)

        return {
            # HWC -> CHW; images to [-1,1]
            "before": (before * 2.0 - 1.0).transpose(2, 0, 1).astype(np.float32),
            "after": (after * 2.0 - 1.0).transpose(2, 0, 1).astype(np.float32),
            "cond": cond.transpose(2, 0, 1).astype(np.float32),
            "prompt": str(row.get("prompt", "")),
        }

    def __getitem__(self, i: int) -> dict:
        import torch

        item = self._load_pair(self.rows[int(i) % len(self.rows)])
        return {
            "before": torch.from_numpy(item["before"]),
            "after": torch.from_numpy(item["after"]),
            "cond": torch.from_numpy(item["cond"]),
            "prompt": item["prompt"],
        }
