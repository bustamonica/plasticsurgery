#!/usr/bin/env python3
"""Side-by-side comparison sheets: ONE body, varied implant params.

Demonstrates the morph engine's control semantics: with everything else held
constant, sweep (a) volume, (b) profile class at fixed volume, (c) placement.

  python3 scripts/compare_sweep.py --out sweep.png [--seed 3] [--resolution 6]
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from morphengine.datafactory.bodies import BodySampler
from morphengine.datafactory.render import SoftwareRenderer, front_camera
from morphengine.implants.db import ImplantDB
from morphengine.implants.schema import Placement
from morphengine.morph.engine import MorphEngine

STRIPS = [
    ("size sweep — Mentor MemoryGel High Profile, submuscular",
     [("mentor-memorygel-250-hp", "submuscular", "250 cc"),
      ("mentor-memorygel-350-hp", "submuscular", "350 cc"),
      ("mentor-memorygel-450-hp", "submuscular", "450 cc"),
      ("mentor-memorygel-550-hp", "submuscular", "550 cc")]),
    ("profile sweep — Mentor MemoryGel ~350 cc, submuscular",
     [("mentor-memorygel-340-mod", "submuscular", "ModClassic 340"),
      ("mentor-memorygel-350-modplus", "submuscular", "ModPlus 350"),
      ("mentor-memorygel-350-hp", "submuscular", "HighProfile 350"),
      ("mentor-memorygel-350-uhp", "submuscular", "UltraHigh 350")]),
    ("placement sweep — Mentor MemoryGel HP 350 cc",
     [("mentor-memorygel-350-hp", "subglandular", "subglandular"),
      ("mentor-memorygel-350-hp", "submuscular", "submuscular"),
      ("mentor-memorygel-350-hp", "dual-plane", "dual-plane")]),
]


def load_font(size: int):
    for name in ("DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--resolution", type=int, default=6)
    ap.add_argument("--cell", type=int, default=256)
    args = ap.parse_args()

    db = ImplantDB.from_json()
    engine = MorphEngine()
    mesh, lm, _ = BodySampler(seed=args.seed, resolution=args.resolution).sample()
    cam = front_camera(mesh.bounds, image_size=args.cell)
    renderer = SoftwareRenderer(cam)
    before_img = Image.fromarray(renderer.render(mesh).rgb)

    font = load_font(16)
    small = load_font(14)
    rows_imgs: list[tuple[str, list[tuple[Image.Image, str]]]] = []
    for title, configs in STRIPS:
        cells: list[tuple[Image.Image, str]] = []
        for sku_id, placement, label in configs:
            params = db.to_params(sku_id, Placement(placement))
            res = engine.morph(mesh, lm, params)
            img = Image.fromarray(renderer.render(res.mesh).rgb)
            achieved = res.achieved_volume_cc
            cells.append((img, f"{label}\n{achieved['left']:.0f} cc achieved"))
        rows_imgs.append((title, cells))

    pad, label_h, head_h = 6, 44, 30
    ncols = 1 + max(len(c) for _, c in rows_imgs)
    W = ncols * args.cell + (ncols + 1) * pad
    H = sum(head_h + args.cell + label_h + pad for _ in rows_imgs) + pad
    sheet = Image.new("RGB", (W, H), (250, 248, 245))
    d = ImageDraw.Draw(sheet)

    y = pad
    for title, cells in rows_imgs:
        d.text((pad, y + 6), title, fill=(40, 35, 30), font=font)
        y += head_h
        x = pad
        sheet.paste(before_img, (x, y))
        d.text((x, y + args.cell + 2), "before", fill=(90, 82, 74), font=small)
        x += args.cell + pad
        for img, label in cells:
            sheet.paste(img, (x, y))
            for j, line in enumerate(label.split("\n")):
                d.text((x, y + args.cell + 2 + j * 16), line,
                       fill=(90, 82, 74), font=small)
            x += args.cell + pad
        y += args.cell + label_h + pad

    sheet.save(args.out)
    print(f"wrote {args.out} — {W}x{H}")


if __name__ == "__main__":
    main()
