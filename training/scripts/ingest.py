#!/usr/bin/env python3
"""Validate raw before/after pairs from clinics and stage them for processing.

Input layout:  <raw>/<clinic>/<pair_id>/{before,after}.(jpg|jpeg|png|webp) + meta.json
Output layout: <staging>/<pair_id>/{before,after}.jpg + meta.json

- Enforces required metadata (including consent_ref — no consent, no ingest).
- Re-encodes every image to clean JPEG, which strips EXIF/GPS/maker notes.
- Rejects tiny images and exact-duplicate pairs (SHA-256 of pixel data).
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

MIN_DIMENSION = 512
REQUIRED_FIELDS = ["pair_id", "shape", "volume_cc", "view", "consent_ref"]
VALID_SHAPES = {"round", "teardrop"}
VALID_VIEWS = {"front", "oblique-left", "oblique-right", "side-left", "side-right"}
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]


def find_image(folder: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTENSIONS:
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def validate_meta(meta: dict, folder: Path) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if not meta.get(field):
            errors.append(f"missing required field '{field}'")
    if meta.get("shape") and meta["shape"] not in VALID_SHAPES:
        errors.append(f"invalid shape '{meta['shape']}'")
    if meta.get("view") and meta["view"] not in VALID_VIEWS:
        errors.append(f"invalid view '{meta['view']}'")
    volume = meta.get("volume_cc")
    if volume is not None and not (isinstance(volume, int) and 100 <= volume <= 1000):
        errors.append(f"volume_cc must be an integer between 100 and 1000, got {volume!r}")
    if meta.get("pair_id") and meta["pair_id"] != folder.name:
        errors.append(f"pair_id '{meta['pair_id']}' does not match folder name '{folder.name}'")
    return errors


def pixel_hash(img: Image.Image) -> str:
    return hashlib.sha256(img.tobytes()).hexdigest()


def reencode(src: Path, dest: Path) -> tuple[str, tuple[int, int]]:
    """Re-save as plain JPEG: drops EXIF/GPS/ICC extras. Returns (hash, size)."""
    with Image.open(src) as img:
        rgb = img.convert("RGB")
        rgb.save(dest, "JPEG", quality=95)
        return pixel_hash(rgb), rgb.size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="Root of raw clinic deliveries")
    parser.add_argument("staging", type=Path, help="Output staging directory")
    args = parser.parse_args()

    args.staging.mkdir(parents=True, exist_ok=True)
    seen_hashes: dict[str, str] = {}
    accepted, rejected = 0, 0

    pair_folders = sorted(p.parent for p in args.raw.glob("*/*/meta.json"))
    if not pair_folders:
        print(f"No pairs found under {args.raw} (expected <clinic>/<pair_id>/meta.json)")
        return 1

    for folder in pair_folders:
        label = f"{folder.parent.name}/{folder.name}"
        try:
            meta = json.loads((folder / "meta.json").read_text())
        except json.JSONDecodeError as e:
            print(f"REJECT {label}: meta.json is not valid JSON ({e})")
            rejected += 1
            continue

        errors = validate_meta(meta, folder)
        before, after = find_image(folder, "before"), find_image(folder, "after")
        if not before:
            errors.append("no before.(jpg|png|webp) image")
        if not after:
            errors.append("no after.(jpg|png|webp) image")

        if errors:
            print(f"REJECT {label}: " + "; ".join(errors))
            rejected += 1
            continue

        out_dir = args.staging / meta["pair_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        pair_ok = True
        for stem, src in (("before", before), ("after", after)):
            digest, size = reencode(src, out_dir / f"{stem}.jpg")
            if min(size) < MIN_DIMENSION:
                print(f"REJECT {label}: {stem} image too small ({size[0]}x{size[1]})")
                pair_ok = False
                break
            if digest in seen_hashes:
                print(f"REJECT {label}: {stem} is a duplicate of {seen_hashes[digest]}")
                pair_ok = False
                break
            seen_hashes[digest] = f"{label}/{stem}"

        if not pair_ok:
            shutil.rmtree(out_dir)
            rejected += 1
            continue

        shutil.copyfile(folder / "meta.json", out_dir / "meta.json")
        accepted += 1

    print(f"\nIngest complete: {accepted} accepted, {rejected} rejected -> {args.staging}")
    return 0 if accepted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
