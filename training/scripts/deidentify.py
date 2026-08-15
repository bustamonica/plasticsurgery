#!/usr/bin/env python3
"""De-identify staged photo pairs before they can be used for training.

For every image: re-encode it, which strips EXIF, GPS and maker notes, and
optionally hard-crop the top of the frame (--crop-top).

**There is no face detection or blurring here.** The consented clinics have
contractually guaranteed that no faces appear in the material they publish, and
that guarantee is what this pipeline relies on (captain ruling, 2026-08-14). It
is stronger evidence than any check this code could run, so the corpus does not
run face detection at all.

That also removes a stage that only ever did harm here. Across every clinic
examined, not one Haar detection was a real face: the frontal-face cascade reads
a breast in profile, a shoulder, a hip or a patch of hair as a face, and the
blur that followed destroyed tissue in photographs the clinic publishes clean.
It cost 37 pairs at drkolker, 15 at austinweston, 12 at harrington, 9 at
drjeremyhunt, 8 at drmiroshnik and 5 at drdanielbarrett before it was removed
(`~/firstmate/data/ba-viz-emit-backlog/quarantine/MANIFEST.md`). Do not
reintroduce it, and in particular do not reintroduce a rule that decides by
where a blur sits in the frame: position cannot separate a face from a shoulder
(`ba-viz-emit-drdanielbarrett/report.md` section 5.2).

What survives is the part that still earns its place. Metadata is real and it
does reach the corpus: a scan of all 5656 corpus images found 36 carrying EXIF,
including `harrington-177-front`, whose two images carry the camera make and
model (Canon EOS Rebel T6) and capture timestamps from 2020. Stripping runs on
EVERY path through this stage - nothing here ever copies source bytes to the
output - so an image cannot leave carrying it.

Stripping happens twice by design: `ingest.py` re-encodes on the way in and this
stage re-encodes again. That redundancy is deliberate defence in depth for the
one privacy property the pipeline still enforces itself.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

JPEG_QUALITY = 95


def encode_stripped(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    """JPEG bytes for `image`, carrying no metadata from the source file.

    This is the de-identification the pipeline still performs itself, and it
    works by construction rather than by filtering: the caller hands in a bare
    pixel array (`cv2.imread` drops every ancillary chunk when it decodes), so
    there is nothing for `cv2.imencode` to carry forward. No path in this module
    writes source bytes, which is what makes "every path strips" true rather
    than merely intended.
    """
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("cv2.imencode failed")
    return buf.tobytes()


def crop_top(image: np.ndarray, fraction: float) -> np.ndarray:
    h = image.shape[0]
    return image[int(h * fraction):, :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("clean", type=Path)
    parser.add_argument(
        "--crop-top",
        type=float,
        default=0.0,
        metavar="FRACTION",
        help="Hard-crop this fraction off the top of every image (e.g. 0.2)",
    )
    args = parser.parse_args()

    args.clean.mkdir(parents=True, exist_ok=True)
    accepted, rejected = 0, 0

    pair_folders = sorted(p.parent for p in args.staging.glob("*/meta.json"))
    if not pair_folders:
        print(f"No staged pairs under {args.staging} — run ingest.py first")
        return 1

    for folder in pair_folders:
        out_dir = args.clean / folder.name
        out_dir.mkdir(parents=True, exist_ok=True)
        pair_ok = True

        for stem in ("before", "after"):
            src = folder / f"{stem}.jpg"
            image = cv2.imread(str(src))
            if image is None:
                print(f"REJECT {folder.name}: cannot read {stem}.jpg")
                pair_ok = False
                break

            if args.crop_top > 0:
                image = crop_top(image, args.crop_top)

            (out_dir / f"{stem}.jpg").write_bytes(encode_stripped(image))

        if not pair_ok:
            shutil.rmtree(out_dir)
            rejected += 1
            continue

        shutil.copyfile(folder / "meta.json", out_dir / "meta.json")
        accepted += 1

    print(f"\nDe-identification complete: {accepted} accepted, {rejected} rejected -> {args.clean}")
    print("EXIF/GPS stripped from every image written. No face detection was run: the "
          "clinics guarantee no faces are published (captain ruling 2026-08-14).")
    print("Now VISUALLY AUDIT every image in the output before building the dataset.")
    return 0 if accepted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
