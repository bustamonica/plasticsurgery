#!/usr/bin/env python3
"""Build the training dataset from de-identified pairs.

Generates an instruction caption per pair from its metadata, using the same
vocabulary as the website's prompt builder (lib/prompt.ts) so the wording the
model is trained on matches the wording it will receive at inference time.

Output (ai-toolkit paired-editing layout + a tool-agnostic manifest):
  <out>/train/target/<pair_id>.jpg   after image (what the model should produce)
  <out>/train/target/<pair_id>.txt   instruction caption
  <out>/train/control/<pair_id>.jpg  before image (conditioning input)
  <out>/val/...                      same layout
  <out>/manifest.jsonl               {pair_id, split, control, target, instruction, meta}
"""

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

# Keep in sync with lib/implants.ts and lib/prompt.ts.
SHAPE_LANGUAGE = {
    "round": "round implants giving even fullness and visible roundness in the upper breast",
    "teardrop": (
        "anatomical teardrop implants giving a gently sloped upper breast and fuller "
        "lower pole, a natural-looking result"
    ),
}

PROFILE_LANGUAGE = {
    "moderate": "a moderate profile with a wide base and gentle forward projection",
    "moderate-plus": "a balanced moderate-plus profile",
    "high": "a high profile with noticeable forward projection and a rounder look",
    "extra-high": "an extra-high profile with maximum forward projection",
}

BRAND_LANGUAGE = {
    "mentor": "soft cohesive silicone gel implants with a natural feel",
    "natrelle": "cohesive silicone gel implants with pronounced, shape-holding upper fullness",
    "motiva": "modern ergonomic silicone implants that settle into a soft natural teardrop when upright",
    "sientra": "high-strength cohesive silicone gel implants",
}


def size_language(cc: int) -> str:
    if cc < 250:
        return "a subtle increase of roughly half to one cup size"
    if cc < 400:
        return "a natural-looking increase of roughly one to one and a half cup sizes"
    if cc < 550:
        return "a clearly noticeable increase of roughly one and a half to two cup sizes"
    if cc < 700:
        return "a full increase of roughly two to two and a half cup sizes"
    return "a dramatic increase of roughly two and a half or more cup sizes"


def build_caption(meta: dict) -> str:
    cc = meta["volume_cc"]
    shape = SHAPE_LANGUAGE.get(meta.get("shape", ""), "implants")
    profile = PROFILE_LANGUAGE.get(meta.get("profile", ""), "a balanced profile")
    brand = BRAND_LANGUAGE.get(meta.get("brand", ""))
    parts = [
        f"Edit this photo to simulate the outcome of breast augmentation surgery "
        f"with {cc} cc {shape}, using {profile}"
        + (f", in the style of {brand}" if brand else "")
        + f". The change should read as {size_language(cc)}.",
        "Keep the person's identity, pose, skin tone, clothing, lighting and background "
        "exactly the same.",
    ]
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    pair_folders = sorted(p.parent for p in args.clean.glob("*/meta.json"))
    if not pair_folders:
        print(f"No clean pairs under {args.clean} — run deidentify.py first")
        return 1

    random.Random(args.seed).shuffle(pair_folders)
    val_count = max(1, int(len(pair_folders) * args.val_fraction)) if len(pair_folders) > 1 else 0

    if args.out.exists():
        shutil.rmtree(args.out)
    manifest_path = args.out / "manifest.jsonl"
    args.out.mkdir(parents=True)

    with manifest_path.open("w") as manifest:
        for i, folder in enumerate(pair_folders):
            split = "val" if i < val_count else "train"
            meta = json.loads((folder / "meta.json").read_text())
            caption = build_caption(meta)
            pair_id = meta["pair_id"]

            target_dir = args.out / split / "target"
            control_dir = args.out / split / "control"
            target_dir.mkdir(parents=True, exist_ok=True)
            control_dir.mkdir(parents=True, exist_ok=True)

            shutil.copyfile(folder / "after.jpg", target_dir / f"{pair_id}.jpg")
            shutil.copyfile(folder / "before.jpg", control_dir / f"{pair_id}.jpg")
            (target_dir / f"{pair_id}.txt").write_text(caption + "\n")

            manifest.write(
                json.dumps(
                    {
                        "pair_id": pair_id,
                        "split": split,
                        "control": f"{split}/control/{pair_id}.jpg",
                        "target": f"{split}/target/{pair_id}.jpg",
                        "instruction": caption,
                        "meta": meta,
                    }
                )
                + "\n"
            )

    train_count = len(pair_folders) - val_count
    print(f"Dataset built: {train_count} train / {val_count} val -> {args.out}")
    if train_count < 500:
        print(
            f"NOTE: {train_count} training pairs is below the ~500 minimum where "
            "fine-tunes start to generalize — treat runs as smoke tests until there is more data."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
