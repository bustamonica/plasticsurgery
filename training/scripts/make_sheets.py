#!/usr/bin/env python3
"""Build a local page the captain can judge by eye, not by metric.

Runs on the Mac AFTER the pod is gone, over the downloaded probe images, so it
costs no GPU time and needs no network.

Three sheets, each holding ONE patient's photo with exactly one thing varied,
and the no-LoRA baseline directly beneath every row - because the question is
not "does this look plausible" but "does it MOVE with the axis, and does the
untrained base model already do that anyway".

  volume   200/300/400/500/600 cc
  view     front / oblique / side, each labelled
  profile  moderate / moderate-plus / high / extra-high / clause omitted,
           at a fixed 350 cc and on a LATERAL view, because projection at
           constant volume is the only place profile is visible at all

Output is a LOCAL file. These are consented clinic photographs of patients;
they do not go to any hosted service.

Usage:
    python3 make_sheets.py work/evalout captain-sheets
    open captain-sheets/index.html
"""
from __future__ import annotations

import argparse
import base64
import html
import re
from collections import defaultdict
from pathlib import Path

THUMB_W = 300


def parse(name: str):
    """`cc-volume_cc=350__ablavsky-108-front__s1.jpg` -> (probe, axis, value, pair)."""
    m = re.match(r"([a-z_]+)(?:-([a-z_]+)=([^_]+))?__(.+?)__s(\d+)\.jpg$", name)
    if not m:
        m2 = re.match(r"([a-z_]+)__(.+?)__(omitted|s\d+)\.jpg$", name)
        if not m2:
            return None
        return {"probe": m2.group(1), "axis": None, "value": "omitted",
                "pair": m2.group(2)}
    return {"probe": m.group(1), "axis": m.group(2), "value": m.group(3),
            "pair": m.group(4)}


def img_tag(path: Path, w=THUMB_W) -> str:
    if not path or not path.exists():
        return f'<div class="missing" style="width:{w}px">not generated</div>'
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f'<img src="data:image/jpeg;base64,{b64}" width="{w}">'


def collect(root: Path):
    """{(probe, pair): {value: path}} for one arm (lora or baseline)."""
    out = defaultdict(dict)
    if not root.exists():
        return out
    for p in sorted(root.glob("*.jpg")):
        info = parse(p.name)
        if info:
            out[(info["probe"], info["pair"])][info["value"]] = p
    return out


def sheet(title, blurb, rows, order_key=None):
    """rows: list of (row_label, {value: path}) - one row per arm."""
    values = []
    for _, byval in rows:
        for v in byval:
            if v not in values:
                values.append(v)
    if order_key:
        values.sort(key=order_key)
    head = "".join(f"<th>{html.escape(str(v))}</th>" for v in values)
    body = ""
    for label, byval in rows:
        cells = "".join(f"<td>{img_tag(byval.get(v))}</td>" for v in values)
        body += f"<tr><th class='rowlab'>{html.escape(label)}</th>{cells}</tr>"
    return f"""<section><h2>{html.escape(title)}</h2><p class="blurb">{blurb}</p>
<div class="scroll"><table><tr><th></th>{head}</tr>{body}</table></div></section>"""


def num_or_inf(v):
    try:
        return (0, float(v))
    except ValueError:
        return (1, 0.0)


PROFILE_ORDER = ["moderate", "moderate-plus", "high", "extra-high", "omitted"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evalout", type=Path, help="dir holding lora/ and baseline/")
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    lora = collect(args.evalout / "lora")
    base = collect(args.evalout / "baseline")
    sections = []

    def pick(probe):
        """The pair with the most values generated, so the sheet is fullest."""
        cands = [(k, v) for k, v in lora.items() if k[0] == probe]
        return max(cands, key=lambda kv: len(kv[1]), default=(None, None))

    key, byval = pick("cc")
    if key:
        sections.append(sheet(
            f"Volume - does the result grow with cc?  (patient {key[1]})",
            "Same photo, same random seed, only the cc figure in the instruction changes. "
            "Read left to right: the breasts should get larger, smoothly. The bottom row is "
            "the untrained base model given the identical instructions - if it moves just as "
            "much, the fine-tune added nothing on this axis.",
            [("trained LoRA", byval), ("base model, no LoRA", base.get(key, {}))],
            order_key=num_or_inf))

    key, byval = pick("prof")
    if key:
        merged = dict(byval)
        omit = lora.get(("prof", key[1]), {}).get("omitted")
        if omit:
            merged["omitted"] = omit
        sections.append(sheet(
            f"Projection / profile - at a FIXED 350 cc  (patient {key[1]})",
            "Volume is held constant; only the profile wording changes. Profile is forward "
            "projection, so this is shown on a side or oblique view - on a front view moderate "
            "and high look nearly identical. Expect moderate to project least and high most. "
            "'omitted' is the instruction with no profile clause at all. "
            "extra-high is expected to FAIL: the corpus holds 6 such pairs from 4 patients, "
            "only 2 of them lateral, so there is no signal to learn from.",
            [("trained LoRA", merged), ("base model, no LoRA", base.get(key, {}))],
            order_key=lambda v: (PROFILE_ORDER.index(v) if v in PROFILE_ORDER else 9, v)))

    view_rows = {}
    for (probe, pair), byv in lora.items():
        if probe == "view_on":
            view_rows[pair] = byv
    if view_rows:
        cells, bcells = {}, {}
        for pair, byv in sorted(view_rows.items()):
            v = pair.rsplit("-", 1)[-1] if "-" in pair else pair
            label = next(iter(byv.values()), None)
            cells[v] = label
            bmatch = base.get(("view_on", pair), {})
            bcells[v] = next(iter(bmatch.values()), None)
        sections.append(sheet(
            "View - is each projection rendered sensibly?",
            "Different patients, one per view, each told which view it is. The point is that "
            "the edit should look anatomically right FOR THAT ANGLE. Training support is very "
            "uneven: front 995 pairs, left-facing 592, right-facing 315 - so expect fronts to "
            "be the strongest and right-side views the weakest.",
            [("trained LoRA", cells), ("base model, no LoRA", bcells)]))

    css = """body{font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:32px;
background:#fbfaf9;color:#1a1a1a;max-width:100%}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 6px}
.blurb{color:#555;max-width:60em;margin:0 0 14px}
.scroll{overflow-x:auto;border:1px solid #e5e2df;border-radius:8px;background:#fff}
table{border-collapse:collapse}th,td{padding:6px;text-align:center;vertical-align:top}
th{font-size:13px;font-weight:600;color:#333;background:#f4f2f0;position:sticky;top:0}
.rowlab{background:#fff;text-align:right;white-space:nowrap;padding-right:12px;font-size:13px}
img{display:block;border-radius:4px}
.missing{color:#999;font-size:12px;padding:20px;border:1px dashed #ddd}
.note{background:#fff6e5;border-left:3px solid #e0a020;padding:12px 16px;border-radius:4px;
max-width:60em;margin:18px 0}"""

    page = f"""<!doctype html><meta charset="utf-8"><title>Augmentation LoRA - visual check</title>
<style>{css}</style>
<h1>Augmentation preview LoRA - see it for yourself</h1>
<p class="blurb">Every row below holds ONE patient photo with exactly one thing changed in the
instruction, at a fixed random seed. The lower row of each sheet is the untrained base model
given identical instructions, which is the comparison that matters: a difference only counts
if the trained model moves and the base model does not.</p>
<div class="note"><b>These are consented clinic photographs.</b> This page is a local file and
must not be uploaded, shared or published.</div>
{''.join(sections) if sections else '<p>No probe images found.</p>'}
"""
    index = args.out / "index.html"
    index.write_text(page)
    print(f"wrote {index}  ({index.stat().st_size / 1e6:.1f} MB, {len(sections)} sheets)")
    print(f"open it with:  open {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
