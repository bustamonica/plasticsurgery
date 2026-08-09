#!/usr/bin/env python3
"""Generate a synthetic before/after corpus for pipeline smoke tests.

These pairs exist ONLY to exercise ingest.py -> deidentify.py ->
build_dataset.py (and a Phase B training smoke run) before any real consented
clinic data exists. The "after" images are cheap programmatic warps of the
"before" images - they teach the model nothing medically meaningful and must
never be mixed into a real training corpus.

Output layout matches what ingest.py expects:
  <out>/<clinic>/<pair_id>/{before.jpg, after.jpg, meta.json}

Every meta.json is valid against dataset_schema.json (consent_ref is the
literal string "synthetic-no-real-patient", which satisfies the schema
without pretending to be a real consent artifact).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SHAPES = ["round", "teardrop"]
PROFILES = ["moderate", "moderate-plus", "high", "extra-high"]
BRANDS = ["mentor", "natrelle", "motiva", "sientra"]
VIEWS = ["front", "oblique-left", "oblique-right", "side-left", "side-right"]
CLOTHING = ["nude", "bra", "top"]
SYNTHETIC_CONSENT_REF = "synthetic-no-real-patient"

WIDTH, HEIGHT = 768, 1024


def draw_torso(rng: random.Random, volume_cc: int, clothing: str) -> Image.Image:
    """Draw a crude headless torso with a chest region sized by volume_cc.

    Headless on purpose: deidentify.py must find no face (run it with
    --allow-no-face). Per-pair jitter keeps pixel hashes unique for the
    ingest dedup check.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)

    # Skin-ish background with per-pair tint jitter.
    base = tuple(rng.randint(180, 215) for _ in range(3))
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=tuple(c + 20 for c in base))

    # Torso: a rounded trapezoid centered with jitter.
    cx = WIDTH // 2 + rng.randint(-40, 40)
    top = 120 + rng.randint(-30, 30)
    shoulder_w = rng.randint(430, 520)
    waist_w = rng.randint(300, 360)
    draw.polygon(
        [
            (cx - shoulder_w // 2, top),
            (cx + shoulder_w // 2, top),
            (cx + waist_w // 2, HEIGHT),
            (cx - waist_w // 2, HEIGHT),
        ],
        fill=base,
    )

    # Chest region: ellipse radius scales with volume (100-1000 cc).
    chest_y = top + rng.randint(220, 280)
    radius = 60 + int((volume_cc - 100) / 900 * 110)  # 60px @100cc .. 170px @1000cc
    gap = radius + rng.randint(15, 35)
    shade = tuple(max(0, c - 18) for c in base)
    for side in (-1, 1):
        draw.ellipse(
            [cx + side * gap - radius, chest_y - radius,
             cx + side * gap + radius, chest_y + radius],
            fill=shade,
        )

    if clothing == "bra":
        for side in (-1, 1):
            draw.pieslice(
                [cx + side * gap - radius, chest_y - radius,
                 cx + side * gap + radius, chest_y + radius],
                start=90, end=270, fill=(90, 90, 110),
            )
    elif clothing == "top":
        draw.rectangle(
            [cx - shoulder_w // 2, chest_y - radius - 40,
             cx + shoulder_w // 2, HEIGHT],
            fill=(90, 90, 110),
        )

    # Deterministic-per-pair noise so no two pairs share a pixel hash.
    noise = np.random.default_rng(rng.randint(0, 2**31)).integers(
        0, 8, (HEIGHT, WIDTH, 1), dtype=np.uint8
    )
    arr = np.asarray(img).astype(np.int16) + noise
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img.filter(ImageFilter.GaussianBlur(radius=1.5))


def warp_after(before: Image.Image, volume_cc: int) -> Image.Image:
    """Simulate the "after" photo: radial bulge around the chest, scaled by cc."""
    arr = np.asarray(before).astype(np.float32)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h * 0.32
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    strength = 0.05 + (volume_cc - 100) / 900 * 0.35
    sigma = 180.0
    fall = np.exp(-(dist**2) / (2 * sigma**2))
    # Sample slightly inward (expand outward): remap source coords.
    src_x = cx + (xx - cx) * (1 - strength * fall)
    src_y = cy + (yy - cy) * (1 - strength * fall)
    src_x = np.clip(src_x, 0, w - 1).astype(np.int32)
    src_y = np.clip(src_y, 0, h - 1).astype(np.int32)
    warped = arr[src_y, src_x]
    return Image.fromarray(warped.astype(np.uint8))


def make_meta(rng: random.Random, pair_id: str, volume_cc: int, clothing: str) -> dict:
    return {
        "pair_id": pair_id,
        "brand": rng.choice(BRANDS),
        "shape": rng.choice(SHAPES),
        "profile": rng.choice(PROFILES),
        "volume_cc": volume_cc,
        "months_post_op": rng.choice([3, 6, 12]),
        "view": rng.choice(VIEWS),
        "clothing": clothing,
        "consent_ref": SYNTHETIC_CONSENT_REF,
        "notes": "Synthetic smoke-test pair; not a real patient.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="Raw-layout output root (feed to ingest.py)")
    parser.add_argument("--count", type=int, default=50, help="Number of pairs to generate")
    parser.add_argument("--clinic", default="synthetic", help="Clinic folder name")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    for i in range(args.count):
        pair_id = f"synth-{i:04d}"
        volume_cc = rng.randrange(100, 1001, 25)
        clothing = CLOTHING[i % len(CLOTHING)]  # balanced clothing coverage
        pair_dir = args.out / args.clinic / pair_id
        pair_dir.mkdir(parents=True, exist_ok=True)

        before = draw_torso(rng, volume_cc, clothing)
        after = warp_after(before, volume_cc)
        before.save(pair_dir / "before.jpg", "JPEG", quality=92)
        after.save(pair_dir / "after.jpg", "JPEG", quality=92)
        (pair_dir / "meta.json").write_text(json.dumps(make_meta(rng, pair_id, volume_cc, clothing), indent=2))

    print(f"Generated {args.count} synthetic pairs under {args.out / args.clinic}")
    print("Next: ingest.py <out> data/staging, then deidentify.py --allow-no-face (images are headless).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
