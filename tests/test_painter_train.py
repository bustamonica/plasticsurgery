"""Painter training tests (SPEC M1.6) — tiny UNet on 4 synthetic pairs.

Self-contained: reuses the dataset synthesizer from test_painter_data.
"""

import json
import math
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from morphengine.painter.train import TrainConfig, build_tiny_unet, train
from test_painter_data import _synth_dataset


def test_tiny_unet_forward_shape():
    model = build_tiny_unet(in_ch=9, out_ch=3, base=32)
    n_params = sum(p.numel() for p in model.parameters())
    assert 0.5e6 < n_params < 2.0e6  # ~1M params
    x = torch.randn(2, 9, 64, 64)
    with torch.no_grad():
        y = model(x)
    assert y.shape == (2, 3, 64, 64)
    # odd sizes round-trip through the 2 down/up levels
    y2 = model(torch.randn(1, 9, 65, 63))
    assert y2.shape == (1, 3, 65, 63)


def test_train_tiny_smoke(tmp_path):
    _synth_dataset(tmp_path, n_pairs=4)
    cfg = TrainConfig(
        model="tiny",
        image_size=64,
        steps=3,
        batch_size=2,
        out_dir=str(tmp_path / "run"),
        seed=0,
    )
    result = train(cfg, tmp_path / "manifest.jsonl")
    assert result["steps"] == 3
    assert math.isfinite(result["final_loss"])
    ckpt = Path(result["ckpt"])
    assert ckpt.exists() and ckpt.name == "checkpoint_last.pt"
    blob = torch.load(ckpt, map_location="cpu", weights_only=False)
    assert "model_state" in blob and "config" in blob
    assert blob["config"]["model"] == "tiny"
    assert blob["config"]["steps"] == 3
    assert (tmp_path / "run" / "config.json").exists()
    cfg_json = json.loads((tmp_path / "run" / "config.json").read_text())
    assert cfg_json["batch_size"] == 2


def test_train_deterministic(tmp_path):
    _synth_dataset(tmp_path, n_pairs=4)
    losses = []
    for run in ("a", "b"):
        cfg = TrainConfig(
            model="tiny",
            image_size=64,
            steps=3,
            batch_size=2,
            out_dir=str(tmp_path / f"run_{run}"),
            seed=7,
        )
        losses.append(train(cfg, tmp_path / "manifest.jsonl")["final_loss"])
    assert losses[0] == losses[1]


def test_train_unknown_model_raises(tmp_path):
    cfg = TrainConfig(model="nope", out_dir=str(tmp_path / "run"))
    with pytest.raises(ValueError, match="unknown model"):
        train(cfg, tmp_path / "manifest.jsonl")
