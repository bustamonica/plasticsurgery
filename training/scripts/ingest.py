#!/usr/bin/env python3
"""Validate raw before/after pairs from clinics and stage them for processing.

Input layout:  <raw>/<clinic>/<pair_id>/{before,after}.(jpg|jpeg|png|webp) + meta.json
Output layout: <staging>/<pair_id>/{before,after}.jpg + meta.json

- Enforces required metadata (including consent_ref — no consent, no ingest).
- Re-encodes every image to clean JPEG, which strips EXIF/GPS/maker notes.
- Rejects tiny images and exact-duplicate pairs (SHA-256 of pixel data).
- Rejects censored or annotated photos (see censorship.py).
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from censorship import detect_censorship

# Deliberately below the 512px ideal: much of the drkolker gallery is published
# at 418x418, and rejecting those would cost ~75% of a consented corpus. The
# tradeoff is that 418px pairs must be upscaled ~2.5x to the 1024px training
# resolution, which softens detail; accepted knowingly (captain ruling, PR #2).
MIN_DIMENSION = 400
REQUIRED_FIELDS = ["pair_id", "shape", "volume_cc", "view", "consent_ref"]
VALID_SHAPES = {"round", "teardrop", "unknown"}
VALID_VIEWS = {"front", "oblique-left", "oblique-right", "side-left", "side-right"}
# `clothing` is optional in dataset_schema.json but mandatory here. build_caption()
# treats an absent value as clothed and tells the model to preserve clothing that
# is not in a nude photograph; 407 pairs on disk are affected
# (`ba-viz-clinic-ask-24/report.md` section 4.3). Absent is not a safe default, so
# ingest refuses to guess.
VALID_CLOTHING = {"nude", "bra", "top"}
# Optional chart/frame fields (schema extension, 2026-08). Nothing here reaches
# build_caption() - they exist for curation, stratification and evaluation - but
# a typo is still worth catching at the door.
VALID_PLACEMENTS = {"submuscular", "subglandular", "subfascial", "dual-plane", "unknown"}
VALID_INCISIONS = {"inframammary", "periareolar", "transaxillary", "unknown"}
NUMERIC_RANGES = {
    "height_cm": (120, 220),
    "weight_kg": (30, 250),
    "chest_width_cm": (20, 60),
    "months_post_op": (0, None),
}
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
    if meta.get("clothing") is None:
        errors.append(
            "missing 'clothing' - it is what the subject wears in BOTH photos; set it to "
            f"one of {sorted(VALID_CLOTHING)} from the photograph itself. Ingest will not "
            "guess: build_caption() reads an absent value as clothed and instructs the "
            "model to preserve clothing that is not in the picture"
        )
    elif meta["clothing"] not in VALID_CLOTHING:
        errors.append(f"invalid clothing '{meta['clothing']}'")
    if meta.get("placement") and meta["placement"] not in VALID_PLACEMENTS:
        errors.append(f"invalid placement '{meta['placement']}'")
    if meta.get("incision") and meta["incision"] not in VALID_INCISIONS:
        errors.append(f"invalid incision '{meta['incision']}'")
    for field, (low, high) in NUMERIC_RANGES.items():
        value = meta.get(field)
        if value is None:
            continue
        bad_type = isinstance(value, bool) or not isinstance(value, (int, float))
        if bad_type or value < low or (high is not None and value > high):
            bound = f"between {low} and {high}" if high is not None else f"{low} or greater"
            errors.append(f"{field} must be a number {bound}, got {value!r}")
    volume = meta.get("volume_cc")
    if volume is not None and not (isinstance(volume, int) and 100 <= volume <= 1000):
        errors.append(f"volume_cc must be an integer between 100 and 1000, got {volume!r}")
    if meta.get("pair_id") and meta["pair_id"] != folder.name:
        errors.append(f"pair_id '{meta['pair_id']}' does not match folder name '{folder.name}'")
    return errors


def pixel_hash(img: Image.Image) -> str:
    return hashlib.sha256(img.tobytes()).hexdigest()


def reencode(src: Path, dest: Path) -> tuple[str, tuple[int, int], np.ndarray]:
    """Re-save as plain JPEG: drops EXIF/GPS/ICC extras.

    Returns (hash, size, pixels) where pixels is a BGR array, the layout the
    OpenCV-based checks downstream expect.
    """
    with Image.open(src) as img:
        rgb = img.convert("RGB")
        rgb.save(dest, "JPEG", quality=95)
        bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
        return pixel_hash(rgb), rgb.size, bgr


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
            digest, size, pixels = reencode(src, out_dir / f"{stem}.jpg")
            if min(size) < MIN_DIMENSION:
                print(f"REJECT {label}: {stem} image too small ({size[0]}x{size[1]})")
                pair_ok = False
                break
            if digest in seen_hashes:
                print(f"REJECT {label}: {stem} is a duplicate of {seen_hashes[digest]}")
                pair_ok = False
                break
            marks = detect_censorship(pixels)
            if marks:
                print(
                    f"REJECT {label}: {stem} is censored or annotated - "
                    + "; ".join(marks)
                    + ". Ask the clinic for the unmarked chart original; do not crop around it"
                )
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
