"""Paint N factory pairs with a trained painter checkpoint and save a grid.

GPU recommended (SDXL fp32 on CPU needs ~14 GB RAM and is very slow); on
Colab run this in the training runtime where the base model is cached.

Usage:
  python scripts/paint_demo.py \
      --ckpt painter_ckpt \
      --manifest /path/to/manifest.jsonl \
      --n 4 --steps 30 --out paint_grid.png
"""
from __future__ import annotations

import argparse

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", required=True, help="export dir (config.json, unet_lora/, cond_encoder.pt)")
    ap.add_argument("--manifest", required=True, help="datafactory manifest.jsonl")
    ap.add_argument("--n", type=int, default=4, help="pairs to paint")
    ap.add_argument("--steps", type=int, default=30, help="DDPM steps per paint")
    ap.add_argument("--out", default="paint_grid.png")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from morphengine.painter.dataset import PairDataset
    from morphengine.painter.inference import PainterInference

    painter = PainterInference.from_ckpt(args.ckpt)
    ds = PairDataset(args.manifest, image_size=painter.image_size)
    n = min(args.n, len(ds))
    idxs = np.linspace(0, len(ds) - 1, n).astype(int)

    fig, axes = plt.subplots(2, n, figsize=(4 * n, 8))
    for j, i in enumerate(idxs):
        item = ds[int(i)]
        out = painter.paint(item["before"].numpy(), item["cond"].numpy(),
                            steps=args.steps, seed=int(i))
        axes[0, j].imshow(item["before"].numpy().transpose(1, 2, 0) * 0.5 + 0.5)
        axes[0, j].set_title(f"before #{i}")
        axes[1, j].imshow(out)
        axes[1, j].set_title("painter output")
        print(f"painted pair #{i}", flush=True)
    for ax in axes.ravel():
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(args.out, dpi=110)
    print("saved", args.out)


if __name__ == "__main__":
    main()
