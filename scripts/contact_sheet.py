#!/usr/bin/env python3
"""Assemble a contact sheet from a generated dataset: rows of before|after
pairs with implant metadata labels. Usage:
  python3 scripts/contact_sheet.py --manifest dataset/manifest.jsonl \
      --out sheet.png [--rows 12] [--cell 256]
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int):
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", type=int, default=12, help="pairs per sheet")
    ap.add_argument("--cell", type=int, default=256)
    args = ap.parse_args()

    mpath = Path(args.manifest)
    root = mpath.parent
    rows = [json.loads(l) for l in mpath.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: (r["volume_cc"], r["profile_class"]))
    rows = rows[: args.rows]
    font = load_font(15)
    small = load_font(13)

    label_h, pad, header_h = 46, 8, 40
    W = 2 * args.cell + 3 * pad
    H = header_h + (args.cell + label_h + pad) * len(rows) + pad
    sheet = Image.new("RGB", (W, H), (250, 248, 245))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 10), f"morphengine M1 demo dataset — {len(rows)} synthetic pairs "
                      f"(before | after)", fill=(40, 35, 30), font=font)

    y = header_h
    for r in rows:
        before = Image.open(root / r["files"]["before"]).convert("RGB").resize((args.cell, args.cell))
        after = Image.open(root / r["files"]["after"]).convert("RGB").resize((args.cell, args.cell))
        sheet.paste(before, (pad, y))
        sheet.paste(after, (2 * pad + args.cell, y))
        line1 = (f"{r['brand']} {r['product_line']} {r['volume_cc']:g} cc "
                 f"{r.get('profile_label') or r['profile_class']} ({r['shape']})")
        line2 = (f"{r['placement']} · base {r['base_width_cm']} cm · "
                 f"proj {r['projection_cm']} cm · {r['camera_kind']} view")
        d.text((pad, y + args.cell + 4), line1, fill=(40, 35, 30), font=small)
        d.text((pad, y + args.cell + 22), line2, fill=(90, 82, 74), font=small)
        y += args.cell + label_h + pad

    sheet.save(args.out)
    print(f"wrote {args.out} — {len(rows)} pairs, {W}x{H}")


if __name__ == "__main__":
    main()
