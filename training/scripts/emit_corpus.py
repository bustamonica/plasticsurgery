#!/usr/bin/env python3
"""Carry staged pairs through to the finished corpus tree.

Input layout:  <staging>/<pair_id>/{before,after}.jpg + meta.json
Output layout: <corpus>/<clinic>/<pair_id>/{before,after}.jpg + meta.json

This is the stage between `ingest.py` and `build_dataset.py`. It is what makes a
pair *count*: a clinic whose pairs never reach `<corpus>/<clinic>/<pair_id>/` is
invisible to every corpus walk, dataset build and audit, however much material
sits in its `staging/` tree. Three clinics - drdanielbarrett, drkolker and
sanantonio - held 745 pairs in staging and nothing in the finished tree, purely
because this step had no script and was done by hand or not at all.

**It performs no de-identification and re-encodes nothing.** Staged images were
already re-encoded by `ingest.py`, which is where EXIF/GPS is stripped; a second
generation of JPEG loss would buy nothing. What this stage does instead is
*verify* that property and refuse to emit an image that still carries EXIF,
which would mean the staging tree did not come from `ingest.py`. There is no
face detection here and none may be added: the consented clinics guarantee no
faces appear in what they publish and the captain holds that assurance (ruling
2026-08-14). See `deidentify.py`'s docstring for what that stage cost when it
existed.

Every pair is admitted or refused for one recorded reason, and `--report` writes
the full enumeration - one row per staged pair, emitted or not. Nothing under
`staging/` is read destructively, moved or deleted: this is a carry-through.

## Retirements

`retired_pairs.json` (beside `dataset_schema.json`) enumerates pair ids that
must never reach the finished tree - currently the 176 laterality labels the
captain retired on 2026-08-15, plus one pair withheld as contested. It is an
enumeration and not a rule on purpose: a rule like "every sanantonio lateral" is
a live query over the staging tree, and a later re-annotation would silently
widen or narrow a ruling the captain made over a fixed set of pairs.

Retiring is not deleting. The staged pair stays where it is, and `--quarantine`
records a copy under the corpus quarantine convention
(`<quarantine>/<reason>/<clinic>/<pair_id>/`) so the images and their consent
metadata survive with the reason attached.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from censorship import detect_censorship
from ingest import MIN_DIMENSION, validate_meta

DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "retired_pairs.json"

# Quarantine subdirectory per retirement class, matching the existing tree at
# ~/firstmate/data/ba-viz-emit-backlog/quarantine/ (deidentify-blur-damage/,
# view-mislabel/).
QUARANTINE_DIRS = {
    "retired-laterality": "retired-laterality",
    "withheld-contested": "withheld-contested",
}


def load_registry(path: Path) -> dict[str, dict[str, str]]:
    """Map clinic -> {pair_id: reason} for every pair the registry withholds."""
    data = json.loads(path.read_text())
    withheld: dict[str, dict[str, str]] = {}
    for reason, section in (
        ("retired-laterality", data.get("retired_laterality", {})),
        ("withheld-contested", data.get("withheld_contested", {})),
    ):
        for clinic, pairs in section.get("pairs", {}).items():
            for pair_id in pairs:
                withheld.setdefault(clinic, {})[pair_id] = reason
    return withheld


def quarantined_ids(quarantine: Path | None) -> dict[str, str]:
    """Map pair_id -> quarantine reason for everything already in the tree.

    The corpus quarantine is <quarantine>/<reason>/<clinic>/<pair_id>/. A pair
    held there has been withdrawn from training by an earlier ruling and must
    not be re-emitted by this stage on its own initiative.
    """
    if quarantine is None or not quarantine.exists():
        return {}
    held = {}
    for pair_dir in quarantine.glob("*/*/*"):
        if pair_dir.is_dir():
            held[pair_dir.name] = pair_dir.parent.parent.name
    return held


def read_image(path: Path) -> tuple[str, tuple[int, int], np.ndarray, bool]:
    """(pixel hash, size, BGR pixels, carries_exif) for a staged image.

    The array is BGR because that is the layout `censorship.py` expects. The
    file is not rewritten - this stage only reads.
    """
    with Image.open(path) as img:
        carries_exif = bool(img.getexif())
        rgb = img.convert("RGB")
        bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
        return hashlib.sha256(rgb.tobytes()).hexdigest(), rgb.size, bgr, carries_exif


def check_pair(
    folder: Path,
    clinic: str,
    withheld: dict[str, dict[str, str]],
    quarantine_held: dict[str, str],
    seen_hashes: dict[str, str],
) -> tuple[str, str]:
    """Decide one staged pair. Returns (disposition, detail).

    Disposition is "emit", or the reason it is not emitted. Ordered so that a
    ruling is reported as a ruling: a retired pair reads as retired even if it
    would also have failed a technical gate.
    """
    pair_id = folder.name

    reason = withheld.get(clinic, {}).get(pair_id)
    if reason:
        return reason, "listed in retired_pairs.json"

    if pair_id in quarantine_held:
        return "quarantined", f"held in quarantine as {quarantine_held[pair_id]}"

    try:
        meta = json.loads((folder / "meta.json").read_text())
    except FileNotFoundError:
        return "invalid-meta", "no meta.json"
    except json.JSONDecodeError as e:
        return "invalid-meta", f"meta.json is not valid JSON ({e})"

    errors = validate_meta(meta, folder)
    if errors:
        return "invalid-meta", "; ".join(errors)

    pending: dict[str, str] = {}
    for stem in ("before", "after"):
        src = folder / f"{stem}.jpg"
        if not src.exists():
            return "missing-image", f"no {stem}.jpg"
        digest, size, pixels, carries_exif = read_image(src)
        if min(size) < MIN_DIMENSION:
            return "too-small", f"{stem} is {size[0]}x{size[1]}, floor is {MIN_DIMENSION}"
        if carries_exif:
            return "carries-exif", (
                f"{stem} still carries EXIF - this staging tree did not come "
                "from ingest.py; re-run ingest rather than stripping here"
            )
        if digest in seen_hashes:
            return "duplicate", f"{stem} is a duplicate of {seen_hashes[digest]}"
        marks = detect_censorship(pixels)
        if marks:
            return "censored", f"{stem}: " + "; ".join(marks)
        pending[digest] = f"{pair_id}/{stem}"

    seen_hashes.update(pending)
    return "emit", ""


def copy_pair(folder: Path, dest: Path) -> None:
    """Copy a pair verbatim. Never overwrites: an existing destination aborts."""
    if dest.exists():
        raise FileExistsError(
            f"{dest} already exists - refusing to overwrite a finished corpus pair"
        )
    dest.mkdir(parents=True)
    for name in ("before.jpg", "after.jpg", "meta.json"):
        shutil.copyfile(folder / name, dest / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path, help="Clinic's staging directory")
    parser.add_argument("corpus", type=Path, help="Root of the finished corpus tree")
    parser.add_argument("--clinic", required=True, help="Clinic name (the corpus subdirectory)")
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="retired_pairs.json (default: beside dataset_schema.json)",
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=None,
        help="Corpus quarantine tree: pairs already held there are not emitted, "
        "and pairs retired by this run are copied into it",
    )
    parser.add_argument("--report", type=Path, default=None, help="Write a per-pair CSV here")
    parser.add_argument(
        "--dry-run", action="store_true", help="Decide every pair but write nothing"
    )
    args = parser.parse_args(argv)

    withheld = load_registry(args.registry)
    quarantine_held = quarantined_ids(args.quarantine)
    pair_folders = sorted(p.parent for p in args.staging.glob("*/meta.json"))
    if not pair_folders:
        print(f"No staged pairs under {args.staging} - run ingest.py first")
        return 1

    seen_hashes: dict[str, str] = {}
    rows, counts = [], {}
    for folder in pair_folders:
        disposition, detail = check_pair(
            folder, args.clinic, withheld, quarantine_held, seen_hashes
        )
        counts[disposition] = counts.get(disposition, 0) + 1
        rows.append({"pair_id": folder.name, "disposition": disposition, "detail": detail})

        if disposition == "emit":
            if not args.dry_run:
                copy_pair(folder, args.corpus / args.clinic / folder.name)
            continue

        print(f"HOLD {folder.name}: {disposition} - {detail}")
        sub = QUARANTINE_DIRS.get(disposition)
        if sub and args.quarantine and not args.dry_run:
            dest = args.quarantine / sub / args.clinic / folder.name
            if not dest.exists():
                copy_pair(folder, dest)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["pair_id", "disposition", "detail"])
            writer.writeheader()
            writer.writerows(rows)

    emitted = counts.get("emit", 0)
    print(f"\n{args.clinic}: {len(pair_folders)} staged")
    for disposition, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {disposition}")
    where = "would emit" if args.dry_run else f"-> {args.corpus / args.clinic}"
    print(f"{emitted} emitted ({where})")
    return 0 if emitted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
