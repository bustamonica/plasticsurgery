#!/usr/bin/env python3
"""Build the training dataset from de-identified pairs.

Generates an instruction caption per pair from its metadata, using the same
vocabulary as the website's prompt builder (lib/prompt.ts) so the wording the
model is trained on matches the wording it will receive at inference time.

The caption is what makes an axis controllable, so it states all three the
product exposes: the exact volume in cc, the implant profile, and the view.
None of them is ever defaulted - an unrecorded profile drops its clause rather
than claiming a moderate one, because a caption is a training label and a
guessed label teaches the guess.

Source: either the flat `data/clean` tree deidentify.py writes, or the finished
corpus tree emit_corpus.py writes (<corpus>/<clinic>/<pair-id>/). Prefer the
corpus tree for a real run - it is the only one with the retirements in
retired_pairs.json actually applied.

Every image is re-encoded on the way out, so nothing bound for a training pod
carries source metadata (see write_stripped).

Output (ai-toolkit paired-editing layout + a tool-agnostic manifest):
  <out>/train/target/<pair_id>.jpg   after image (what the model should produce)
  <out>/train/target/<pair_id>.txt   instruction caption
  <out>/train/control/<pair_id>.jpg  before image (conditioning input)
  <out>/val/...                      same layout
  <out>/manifest.jsonl               {pair_id, split, control, target, instruction, meta}
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import cv2

from deidentify import encode_stripped

# Keep in sync with lib/implants.ts and lib/prompt.ts.
SHAPE_LANGUAGE = {
    "round": "round implants giving even fullness and visible roundness in the upper breast",
    "teardrop": (
        "anatomical teardrop implants giving a gently sloped upper breast and fuller "
        "lower pole, a natural-looking result"
    ),
}

# Profile is OMITTED, never defaulted, when the clinic did not record it. 61% of
# the corpus carries no profile; asserting "a balanced profile" on those pairs
# taught the model that an unrecorded outcome was a moderate one, which is the
# opposite of teaching the projection axis. Omission means profile wording is
# only ever seen alongside a pair that genuinely carries that profile - the same
# rule `brand` has always followed. Keep in sync with PROFILES in lib/implants.ts.
PROFILE_LANGUAGE = {
    "moderate": "a moderate profile with a wide base and gentle forward projection",
    "moderate-plus": "a balanced moderate-plus profile",
    "high": "a high profile with noticeable forward projection and a rounder look",
    "extra-high": "an extra-high profile with maximum forward projection",
}

# The `view` field in dataset_schema.json. Without this the model is never told
# which projection it is looking at, so it cannot be asked for one at inference.
# Laterality follows the corpus convention (AGENTS.md): 'oblique-left' /
# 'side-left' means the subject's LEFT side faces the camera.
# Keep in sync with CUSTOM_MODEL_VIEW_LANGUAGE in lib/prompt.ts.
VIEW_LANGUAGE = {
    "front": "The photograph is a front view.",
    "oblique-left": (
        "The photograph is an oblique three-quarter view with the subject's "
        "left side toward the camera."
    ),
    "oblique-right": (
        "The photograph is an oblique three-quarter view with the subject's "
        "right side toward the camera."
    ),
    "side-left": (
        "The photograph is a side profile with the subject's left side toward the camera."
    ),
    "side-right": (
        "The photograph is a side profile with the subject's right side toward the camera."
    ),
}

BRAND_LANGUAGE = {
    "mentor": "soft cohesive silicone gel implants with a natural feel",
    "natrelle": "cohesive silicone gel implants with pronounced, shape-holding upper fullness",
    "motiva": "modern ergonomic silicone implants that settle into a soft natural teardrop when upright",
    "sientra": "high-strength cohesive silicone gel implants",
}

# The `clothing` field in dataset_schema.json. Captions must reflect it: the
# model trains on clinical photos that are often nude, while consumers upload
# clothed photos, so the instruction has to say what to preserve and what to
# render. Keep in sync with CUSTOM_MODEL_CLOTHING_LANGUAGE in lib/prompt.ts.
CLOTHING_LANGUAGE = {
    "nude": (
        "The subject is photographed nude from the waist up; render realistic "
        "natural skin and anatomy in the chest area."
    ),
    "bra": (
        "The subject is wearing a bra; adjust only the breast size and shape "
        "under the existing bra and keep the bra itself unchanged."
    ),
    "top": (
        "The subject is wearing a top; adjust only the breast size and shape "
        "under the existing top and keep the top itself unchanged."
    ),
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
    # The literal figure, never bucketed or rounded: 10 cc granularity at
    # inference depends on the model having seen the exact number in training.
    cc = meta["volume_cc"]
    shape = SHAPE_LANGUAGE.get(meta.get("shape", ""), "implants")
    view = VIEW_LANGUAGE.get(meta.get("view", ""))
    profile = PROFILE_LANGUAGE.get(meta.get("profile", ""))
    brand = BRAND_LANGUAGE.get(meta.get("brand", ""))
    clothing = meta.get("clothing")
    preserve = "identity, pose, skin tone, lighting and background"
    if clothing != "nude":
        # No clothing to preserve in a nude photo; for clothed (or unknown)
        # pairs, explicitly pin the clothing.
        preserve = "identity, pose, skin tone, clothing, lighting and background"
    parts = []
    if view:
        parts.append(view)
    parts += [
        f"Edit this photo to simulate the outcome of breast augmentation surgery "
        f"with {cc} cc {shape}"
        + (f", using {profile}" if profile else "")
        + (f", in the style of {brand}" if brand else "")
        + f". The change should read as {size_language(cc)}.",
        f"Keep the person's {preserve} exactly the same.",
    ]
    if clothing in CLOTHING_LANGUAGE:
        parts.append(CLOTHING_LANGUAGE[clothing])
    return " ".join(parts)


def find_pair_folders(src: Path) -> "tuple[list[Path], list[str]]":
    """Collect pair folders from either source layout, and say what was skipped.

    Two trees feed this stage. `data/clean` (deidentify.py's output) is flat:
    <src>/<pair-id>/. The finished corpus is partitioned by clinic:
    <corpus>/<clinic>/<pair-id>/. Only the corpus tree has the retirements
    applied, so it is the honest source for a real run.

    Top-level entries starting with '_' or '.' are NOT clinics and are skipped:
    `clinic-corpus/_staging/` is a pre-emit leftover that both duplicates
    finished pairs and still holds retired ones, so walking it would silently
    re-admit pairs a captain ruling removed.
    """
    pair_folders: list[Path] = []
    notes: list[str] = []
    for entry in sorted(src.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(("_", ".")):
            held = len(list(entry.glob("*/meta.json"))) + (entry / "meta.json").exists()
            notes.append(f"skipped non-clinic directory {entry.name}/ ({held} pair folder(s))")
            continue
        if (entry / "meta.json").exists():
            pair_folders.append(entry)
            continue
        nested = sorted(p.parent for p in entry.glob("*/meta.json"))
        if nested:
            pair_folders.extend(nested)
    return pair_folders, notes


def load_pair(folder: Path) -> "tuple[dict, Path, Path] | None":
    """Read a pair, or return None (with a reason on stdout) if it cannot train.

    Corpus images are not uniformly .jpg - the tree mixes .jpg, .jpeg, .png and
    .webp - so the extension is discovered, never assumed.
    """
    meta = json.loads((folder / "meta.json").read_text())
    if meta.get("volume_cc") is None:
        print(f"  skip {folder.name}: no volume_cc, so no caption can state the size")
        return None
    befores = sorted(folder.glob("before.*"))
    afters = sorted(folder.glob("after.*"))
    if not befores or not afters:
        print(f"  skip {folder.name}: missing a before/after image")
        return None
    return meta, befores[0], afters[0]


def write_stripped(src: Path, dest: Path) -> None:
    """Write `src` to `dest` as JPEG carrying no metadata from the source file.

    Re-encode rather than copy: this is the last stage before the images leave
    the machine for a training pod, and a byte copy carries whatever the source
    held. That is not theoretical - 14 images in the finished corpus carry
    Photoshop tags and capture timestamps, so a corpus-sourced build that copied
    bytes would ship them. `cv2.imread` hands `encode_stripped` a bare pixel
    array, so the strip works by construction (see deidentify.py). It also
    normalises the corpus's mixed .jpg/.jpeg/.png/.webp into the single
    extension the trainer config can assume.
    """
    image = cv2.imread(str(src))
    if image is None:
        raise ValueError(f"could not decode {src}")
    dest.write_bytes(encode_stripped(image))


def case_id(meta: dict) -> str:
    """The patient a pair belongs to: the pair id minus its view suffix.

    One patient publishes up to five views. Splitting per pair would put the
    same patient in train and val, and the val sample grid - the only evidence
    the run produces - would then be scored on a patient the model memorised.
    """
    pair_id = meta["pair_id"]
    suffix = f"-{meta.get('view', '')}"
    return pair_id[: -len(suffix)] if suffix != "-" and pair_id.endswith(suffix) else pair_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    candidates, notes = find_pair_folders(args.clean)
    for note in notes:
        print(note)
    if not candidates:
        print(f"No pairs under {args.clean} — run deidentify.py or emit_corpus.py first")
        return 1

    pairs = []
    seen: dict[str, Path] = {}
    for folder in candidates:
        loaded = load_pair(folder)
        if loaded is None:
            continue
        meta, before, after = loaded
        pair_id = meta["pair_id"]
        if pair_id in seen:
            print(f"Duplicate pair_id {pair_id}: {seen[pair_id]} and {folder}")
            return 1
        seen[pair_id] = folder
        pairs.append((meta, before, after))
    if not pairs:
        print(f"No usable pairs under {args.clean}")
        return 1
    print(f"{len(pairs)} usable of {len(candidates)} pair folder(s) under {args.clean}")

    # Split by patient, not by pair, so no patient spans train and val.
    cases = sorted({case_id(meta) for meta, _, _ in pairs})
    random.Random(args.seed).shuffle(cases)
    val_case_count = max(1, int(len(cases) * args.val_fraction)) if len(cases) > 1 else 0
    val_cases = set(cases[:val_case_count])
    pairs.sort(key=lambda p: p[0]["pair_id"])

    if args.out.exists():
        shutil.rmtree(args.out)
    manifest_path = args.out / "manifest.jsonl"
    args.out.mkdir(parents=True)

    counts = {"train": 0, "val": 0}
    with manifest_path.open("w") as manifest:
        for meta, before, after in pairs:
            split = "val" if case_id(meta) in val_cases else "train"
            counts[split] += 1
            caption = build_caption(meta)
            pair_id = meta["pair_id"]

            target_dir = args.out / split / "target"
            control_dir = args.out / split / "control"
            target_dir.mkdir(parents=True, exist_ok=True)
            control_dir.mkdir(parents=True, exist_ok=True)

            # Re-encode rather than copy: this is the last stage before the
            # images leave the machine for a training pod, and a byte copy
            # carries whatever metadata the source held. It is not theoretical -
            # 14 finished-corpus images carry Photoshop tags and capture
            # timestamps, so a corpus-sourced build that copied bytes would ship
            # them. Re-encoding also normalises the corpus's mixed .jpg/.jpeg/
            # .png/.webp into one extension the trainer config can assume.
            write_stripped(after, target_dir / f"{pair_id}.jpg")
            write_stripped(before, control_dir / f"{pair_id}.jpg")
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

    print(
        f"Dataset built: {counts['train']} train / {counts['val']} val "
        f"({len(cases) - val_case_count}/{val_case_count} patients) -> {args.out}"
    )
    if counts["train"] < 500:
        print(
            f"NOTE: {counts['train']} training pairs is below the ~500 minimum where "
            "fine-tunes start to generalize — treat runs as smoke tests until there is more data."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
