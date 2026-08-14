#!/usr/bin/env python3
"""De-identify staged photo pairs before they can be used for training.

For every image: detect faces and blur them beyond recognition. Optionally
hard-crop the top of the frame instead (--crop-top), which is stronger when
photos consistently include the head.

Safety default: an image where NO face is detected is REJECTED unless
--allow-no-face is passed. Many clinical photos are already cropped below the
chin — audit a sample first, then rerun with the flag. Detection is a helper,
not a guarantee: visually audit every output batch.

This stage is the ONLY one that can create censorship-like damage, and it runs
AFTER ingest.py's censorship gate, so nothing used to re-check its own output.
It does now: a pair whose blur lands on the body instead of a face is rejected
here. That is not hypothetical - the Haar cascade false-positived on a torso in
drmiroshnik case88/case112, pixelating a breast in photographs the clinic
publishes clean, and both pairs reached the corpus silently (report
`ba-viz-emit-backlog` section 7). --allow-no-face makes this MORE likely, not
less: you pass it precisely for headless photos, where every detection is by
definition a false positive.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

from censorship import detect_censorship

BLUR_MARGIN = 0.35  # expand detected face boxes by this fraction on each side
JPEG_QUALITY = 95
# A legitimate blur target is a head, which sits at the top of the frame - these
# galleries crop at or below the chin, which is why --allow-no-face exists at
# all. A blur whose vertical centre falls below this band is not covering a
# face. Measured: the drmiroshnik false positives centred at 0.71 and 0.67 of
# frame height; a real head blur centres in the top tenth.
HEAD_BAND_FRACTION = 0.30


def encode_as_written(image: np.ndarray, quality: int = JPEG_QUALITY) -> tuple[bytes, np.ndarray]:
    """(bytes to write, the image those bytes decode to).

    The censorship detector reads a texture field, and JPEG quantisation moves
    it: drmiroshnik case71's pixelated chest measures CLEAN as an in-memory
    array and censored after a quality-95 round trip. Checking the array would
    therefore have passed a pair whose FILE is damaged, so the check has to run
    on exactly the bytes that land in the corpus.
    """
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("cv2.imencode failed")
    data = buf.tobytes()
    return data, cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def blur_is_on_the_body(before: np.ndarray, after: np.ndarray) -> list[str]:
    """Censorship marks this stage introduced over the body rather than a face.

    ingest.py already ran `detect_censorship`, but it ran it on the UNBLURRED
    image; re-running it on the output is what turns a face-detector false
    positive into a rejection instead of a corpus entry.

    Two conditions, because a pixelated FACE is also a 'texture-free patch of
    skin' and must stay allowed:
      1. the blurred region's centre lies below the head band, and
      2. it introduces a censorship mark the input did not already have.
    """
    changed = np.any(before != after, axis=2)
    if not changed.any():
        return []
    rows = np.where(changed.any(axis=1))[0]
    if float(rows.mean()) < HEAD_BAND_FRACTION * before.shape[0]:
        return []  # sitting over the head: the intended target
    was = {mark.split(" at ")[0] for mark in detect_censorship(before)}
    return [mark for mark in detect_censorship(after)
            if mark.split(" at ")[0] not in was]


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
            if not faces and not args.allow_no_face and args.crop_top == 0:
                print(
                    f"REJECT {folder.name}: no face detected in {stem}.jpg — "
                    "verify it is already de-identified, then rerun with --allow-no-face"
                )
                pair_ok = False
                break

            data, written = encode_as_written(
                blur_regions(image, faces) if faces else image)
            introduced = blur_is_on_the_body(image, written) if faces else []
            if introduced:
                print(
                    f"REJECT {folder.name}: blurring {stem}.jpg put censorship over "
                    "the body, not a face - " + "; ".join(introduced)
                    + ". The face detector fired on the torso; do not train on this"
                )
                pair_ok = False
                break

            (out_dir / f"{stem}.jpg").write_bytes(data)

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
