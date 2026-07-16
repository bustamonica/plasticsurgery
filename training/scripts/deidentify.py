#!/usr/bin/env python3
"""De-identify staged photo pairs before they can be used for training.

For every image: detect faces and blur them beyond recognition. Optionally
hard-crop the top of the frame instead (--crop-top), which is stronger when
photos consistently include the head.

Safety default: an image where NO face is detected is REJECTED unless
--allow-no-face is passed. Many clinical photos are already cropped below the
chin — audit a sample first, then rerun with the flag. Detection is a helper,
not a guarantee: visually audit every output batch.
"""

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

BLUR_MARGIN = 0.35  # expand detected face boxes by this fraction on each side


def detect_faces(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    if not hasattr(cv2, "CascadeClassifier"):
        sys.exit(
            "This OpenCV build has no CascadeClassifier (OpenCV 5 removed it). "
            "Install the pinned version: pip install -r training/requirements.txt"
        )
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    return [tuple(int(v) for v in box) for box in faces]


def blur_regions(image: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    for x, y, bw, bh in boxes:
        mx, my = int(bw * BLUR_MARGIN), int(bh * BLUR_MARGIN)
        x0, y0 = max(0, x - mx), max(0, y - my)
        x1, y1 = min(w, x + bw + mx), min(h, y + bh + my)
        roi = out[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        # Pixelate + gaussian: robust against deblurring, no residual features.
        small = cv2.resize(roi, (8, 8), interpolation=cv2.INTER_LINEAR)
        pixelated = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        out[y0:y1, x0:x1] = cv2.GaussianBlur(pixelated, (31, 31), 0)
    return out


def crop_top(image: np.ndarray, fraction: float) -> np.ndarray:
    h = image.shape[0]
    return image[int(h * fraction):, :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("clean", type=Path)
    parser.add_argument(
        "--allow-no-face",
        action="store_true",
        help="Accept images with no detected face (for pre-cropped clinical photos)",
    )
    parser.add_argument(
        "--crop-top",
        type=float,
        default=0.0,
        metavar="FRACTION",
        help="Also hard-crop this fraction off the top of every image (e.g. 0.2)",
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

            faces = detect_faces(image)
            if faces:
                image = blur_regions(image, faces)
            elif not args.allow_no_face and args.crop_top == 0:
                print(
                    f"REJECT {folder.name}: no face detected in {stem}.jpg — "
                    "verify it is already de-identified, then rerun with --allow-no-face"
                )
                pair_ok = False
                break

            cv2.imwrite(str(out_dir / f"{stem}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])

        if not pair_ok:
            shutil.rmtree(out_dir)
            rejected += 1
            continue

        shutil.copyfile(folder / "meta.json", out_dir / "meta.json")
        accepted += 1

    print(f"\nDe-identification complete: {accepted} accepted, {rejected} rejected -> {args.clean}")
    print("Now VISUALLY AUDIT every image in the output before building the dataset.")
    return 0 if accepted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
