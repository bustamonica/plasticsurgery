"""Tests for painter inference (M4).

The heavy SDXL graph is exercised with injected stubs (no model download);
the critical invariant under test is that inference conditioning EXACTLY
matches the training-time format via the shared build_cond.
"""

import json

import numpy as np
import pytest

from morphengine.painter.dataset import PairDataset, build_cond
from morphengine.painter.inference import PainterInference

torch = pytest.importorskip("torch", reason="torch not installed")


class TestBuildCond:
    def _maps(self, H=32, seed=0):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[:H, :H]
        inside = (xx - H / 2) ** 2 + (yy - H / 2) ** 2 < (H / 3) ** 2
        depth_b = np.where(inside, 100.0 - yy, np.nan).astype(np.float32)
        depth_a = np.where(inside, 95.0 - yy, np.nan).astype(np.float32)
        normal = np.stack([np.zeros((H, H)), np.zeros((H, H)),
                           np.where(inside, 1.0, 0.0)], axis=-1).astype(np.float32)
        mask = inside.astype(np.float32)
        return depth_b, depth_a, normal, mask

    def test_channel_layout_and_bg_rules(self):
        d_b, d_a, n, m = self._maps()
        cond = build_cond(d_b, d_a, n, m)
        assert cond.shape == (32, 32, 6)
        # bg is zero in every channel
        bg = ~np.isfinite(d_a)
        assert np.allclose(cond[bg], 0.0)
        # depth inverted: foreground ~1 near the closest valid pixel
        fg = np.isfinite(d_a)
        assert cond[..., 1][fg].max() == pytest.approx(1.0, abs=0.2)
        # normal z=1 -> channel 4 ≈ 1 on body
        assert cond[..., 4][m > 0.5].mean() == pytest.approx(1.0, abs=0.01)
        # mask channel is {0,1}
        assert set(np.unique(cond[..., 5])) <= {0.0, 1.0}

    def test_matches_pairdataset_training_cond(self, tmp_path):
        """Same geometry through files+PairDataset must give identical cond."""
        d_b, d_a, n, m = self._maps()
        (tmp_path / "images").mkdir()
        from PIL import Image
        for name, arr in (("b.png", np.full((32, 32, 3), 128, np.uint8)),
                          ("a.png", np.full((32, 32, 3), 200, np.uint8))):
            Image.fromarray(arr).save(tmp_path / "images" / name)
        for name, arr in (("db.npy", d_b), ("da.npy", d_a),
                          ("na.npy", n), ("mb.npy", m)):
            np.save(tmp_path / "images" / name, arr)
        row = {"pair_id": "p0", "prompt": "",
               "files": {"before": "images/b.png", "after": "images/a.png",
                         "depth_before": "images/db.npy",
                         "depth_after": "images/da.npy",
                         "normal_after": "images/na.npy",
                         "mask_before": "images/mb.npy"}}
        with open(tmp_path / "manifest.jsonl", "w") as fh:
            fh.write(json.dumps(row) + "\n")
        item = PairDataset(tmp_path / "manifest.jsonl", image_size=32)._load_pair(row)
        expected = build_cond(d_b, d_a, n, m).transpose(2, 0, 1)
        assert np.allclose(item["cond"], expected)


class _StubVAE:
    class config: scaling_factor = 1.0

    def decode(self, lat):
        b = lat.shape[0]
        return type("O", (), {"sample": torch.full((b, 3, 32, 32), 0.5)})()


class _StubUNet:
    class config: cross_attention_dim = 8

    def __init__(self):
        self.calls = 0

    def __call__(self, x, t, hidden, added_cond_kwargs=None):
        self.calls += 1
        assert x.shape[1] == 8           # noisy(4) + cond_latent(4)
        return type("O", (), {"sample": torch.zeros_like(x[:, :4])})()


class _StubCondEncoder:
    def __init__(self):
        self.seen_ch = None

    def __call__(self, x):
        self.seen_ch = x.shape[1]
        return torch.zeros(x.shape[0], 4, 4, 4)


class _StubSched:
    def set_timesteps(self, steps, device=None):
        self.timesteps = list(range(steps))

    def step(self, eps, t, lat):
        return type("O", (), {"prev_sample": lat})()


class TestPaintLoop:
    def _painter(self):
        self.enc = _StubCondEncoder()
        self.unet = _StubUNet()
        return PainterInference.from_parts(
            _StubVAE(), self.unet, self.enc, _StubSched(),
            device=torch.device("cpu"), dtype=torch.float32, image_size=32)

    def test_paint_runs_loop_and_ranges(self):
        p = self._painter()
        before = np.zeros((3, 32, 32), np.float32)
        cond = np.zeros((6, 32, 32), np.float32)
        out = p.paint(before, cond, steps=7, seed=0)
        assert out.shape == (32, 32, 3)
        assert out.min() >= 0.0 and out.max() <= 1.0
        assert self.enc.seen_ch == 9                # before(3)+cond(6)
        assert self.unet.calls == 7                 # one unet call per step
        # stub vae decodes constant 0.5 -> [0,1] maps to 0.75
        assert out.mean() == pytest.approx(0.75, abs=1e-6)

    def test_paint_geometry_end_to_end_prep(self):
        p = self._painter()
        rgb = np.full((64, 48, 3), 0.5, np.float32)
        H = 32
        d_b = np.where(np.mgrid[:H, :H][0] < 16, 10.0, np.nan).astype(np.float32)
        d_a = d_b - 1.0
        n = np.zeros((H, H, 3), np.float32); n[..., 2] = 1.0
        m = np.isfinite(d_b).astype(np.float32)
        out = p.paint_geometry(rgb, d_b, d_a, n, m, steps=3, seed=1)
        assert out.shape == (32, 32, 3)
        assert self.enc.seen_ch == 9
