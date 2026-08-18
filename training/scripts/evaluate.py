#!/usr/bin/env python3
"""Measure whether the trained LoRA actually controls view, cc and profile.

Runs ON THE POD, on the same lease as training, immediately after the final
checkpoint - re-renting later would pay the setup and the ~57GB model download
a second time.

The design is in eval-plan.md and is pre-registered: the predictions were
written down before any result existed. The question here is never "does this
look good", it is "does changing ONE clause of the instruction move the output
in the predicted direction by more than the model's own seed noise". So every
probe holds the control image and the seed fixed and varies exactly one thing.

Two controls come first, because without them nothing below is falsifiable:
  * the seed noise floor (same caption, different seeds) - an effect smaller
    than this is not an effect
  * the base model with the LoRA disabled - if stock Qwen-Image-Edit already
    separates 300cc from 600cc, the fine-tune added nothing on that axis

Writes results.json plus contact-sheet grids.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# build_caption must come from the repo, so the captions this probes with are
# byte-identical to the ones the model was trained on. Works from the repo or
# from the pod copy at /workspace/repo.
for _p in (str(Path(__file__).resolve().parent), "/workspace/repo/training/scripts"):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --- response metrics -----------------------------------------------------
#
# Both metrics are computed against the CONTROL image, not the ground-truth
# after photo, because the probes below generate at cc/profile values the
# patient never had - there is no ground truth to compare against. What is
# being measured is the size and location of the edit, not its correctness.

SKIN_Y_MIN, SKIN_CR, SKIN_CB = 50, (130, 185), (75, 132)


def body_mask(bgr: np.ndarray) -> np.ndarray:
    """Body silhouette against the studio backdrop, same rule as censorship.py."""
    import cv2

    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycrcb[:, :, 0], ycrcb[:, :, 1], ycrcb[:, :, 2]
    skin = ((y > SKIN_Y_MIN) & (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1])
            & (cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1])).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(skin, 8)
    if n <= 1:
        return skin.astype(bool)
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (lab == biggest)


def edit_magnitude(control: np.ndarray, out: np.ndarray) -> dict:
    """How much changed, and where.

    `chest` is the response metric for the volume and profile axes.
    `outside` is the identity check - a model that "responds" by repainting the
    whole frame is unstable, not controllable, and that has to be visible in
    the numbers rather than discovered by eye.
    """
    import cv2

    if out.shape != control.shape:
        out = cv2.resize(out, (control.shape[1], control.shape[0]))
    d = np.abs(control.astype(np.float32) - out.astype(np.float32)).mean(axis=2)
    h = d.shape[0]
    chest = slice(int(h * 0.20), int(h * 0.62))
    below = slice(int(h * 0.75), h)
    return {
        "chest": float(d[chest].sum() / 1000.0),
        "outside": float(d[below].mean()),
        "delta_centroid_y": float((d.sum(axis=1) * np.arange(h)).sum() / max(d.sum(), 1e-6) / h),
    }


def projection(bgr: np.ndarray) -> float:
    """Max anterior silhouette extent in the chest band, as a fraction of width.

    The direct physical quantity both cc and profile act on, and the only one
    that separates 'moderate' from 'high' at a fixed volume. Meaningful on
    lateral views only - on a front view the two look nearly identical, which
    is why the profile probes run on side/oblique images.
    """
    m = body_mask(bgr)
    h, w = m.shape
    band = m[int(h * 0.20):int(h * 0.62)]
    if not band.any():
        return float("nan")
    widths = [(row.nonzero()[0].max() - row.nonzero()[0].min()) for row in band if row.any()]
    return float(max(widths) / w) if widths else float("nan")


# --- generation -----------------------------------------------------------

class Generator:
    def __init__(self, lora: Path | None, base="Qwen/Qwen-Image-Edit-2509"):
        import torch
        from diffusers import QwenImageEditPlusPipeline

        self.torch = torch
        self.pipe = QwenImageEditPlusPipeline.from_pretrained(
            base, torch_dtype=torch.bfloat16).to("cuda")
        self.pipe.set_progress_bar_config(disable=True)
        self.lora = lora
        if lora:
            self.pipe.load_lora_weights(str(lora))
            print(f"loaded LoRA {lora.name}", flush=True)
        else:
            print("BASELINE: no LoRA loaded", flush=True)

    def __call__(self, control_path: Path, prompt: str, seed: int) -> np.ndarray:
        import cv2
        from PIL import Image

        img = Image.open(control_path).convert("RGB")
        gen = self.torch.Generator(device="cuda").manual_seed(seed)
        out = self.pipe(image=[img], prompt=prompt, generator=gen,
                        num_inference_steps=25, true_cfg_scale=3.0).images[0]
        return cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)


# --- caption construction, reusing the trained wording exactly -------------

def caption(meta: dict) -> str:
    from build_dataset import build_caption
    return build_caption(meta)


def fidelity(control: np.ndarray, produced: np.ndarray, truth: np.ndarray) -> dict:
    """How close the generated 'after' is to the REAL 'after' for this pair.

    Only meaningful when the caption is the pair's own, so this is the
    checkpoint-selection metric and not a probe metric: the probes deliberately
    ask for cc/profile values the patient never had, where no truth exists.

    Reported against the do-nothing baseline (`copy_err`, the error you get by
    returning the control unedited). Any checkpoint scoring worse than that has
    learned to make things worse, which a raw error number would hide.
    """
    import cv2

    h, w = control.shape[:2]
    if produced.shape[:2] != (h, w):
        produced = cv2.resize(produced, (w, h))
    if truth.shape[:2] != (h, w):
        truth = cv2.resize(truth, (w, h))
    band = slice(int(h * 0.20), int(h * 0.62))
    err = float(np.abs(produced[band].astype(np.float32) - truth[band].astype(np.float32)).mean())
    copy_err = float(np.abs(control[band].astype(np.float32) - truth[band].astype(np.float32)).mean())
    outside = slice(int(h * 0.75), h)
    identity = float(np.abs(produced[outside].astype(np.float32)
                            - control[outside].astype(np.float32)).mean())
    return {"chest_err": err, "copy_err": copy_err,
            "beats_copy": err < copy_err, "identity_drift": identity}


def select_checkpoint(dataset: Path, out: Path, ckpts: "list[Path]", n: int = 6) -> dict:
    """Score each checkpoint on held-out val pairs and pick the best.

    Condition 5 of the run brief: report WHICH checkpoint and why, rather than
    assuming the last one. At 5.5 epochs over 1902 pairs the last save is not
    obviously the best, which is why the config keeps all 13.
    """
    import cv2

    rows = [json.loads(l) for l in (dataset / "manifest.jsonl").open()]
    val = [r for r in rows if r["split"] == "val"][:n]
    scored = []
    for ck in ckpts:
        gen = Generator(ck)
        recs = []
        for r in val:
            ctrl = cv2.imread(str(dataset / r["control"]))
            truth = cv2.imread(str(dataset / r["target"]))
            img = gen(dataset / r["control"], r["instruction"], seed=1)
            recs.append(fidelity(ctrl, img, truth))
        mean_err = float(np.mean([x["chest_err"] for x in recs]))
        scored.append({"checkpoint": str(ck), "mean_chest_err": mean_err,
                       "beats_copy": sum(x["beats_copy"] for x in recs), "n": len(recs),
                       "mean_identity_drift": float(np.mean([x["identity_drift"] for x in recs])),
                       "mean_copy_err": float(np.mean([x["copy_err"] for x in recs]))})
        print(f"  {ck.name}: chest_err={mean_err:.3f} "
              f"(copy baseline {scored[-1]['mean_copy_err']:.3f}), "
              f"beats-copy {scored[-1]['beats_copy']}/{len(recs)}", flush=True)
        del gen
    best = min(scored, key=lambda s: s["mean_chest_err"])
    (out / "checkpoint_selection.json").write_text(
        json.dumps({"candidates": scored, "selected": best}, indent=2))
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--lora", type=Path)
    ap.add_argument("--baseline", action="store_true",
                    help="run the no-LoRA control instead")
    ap.add_argument("--select", nargs="+", type=Path,
                    help="score these checkpoints on val and print the best")
    ap.add_argument("--subjects", type=int, default=12)
    args = ap.parse_args()
    if args.select:
        args.out.mkdir(parents=True, exist_ok=True)
        best = select_checkpoint(args.dataset, args.out, args.select)
        print("SELECTED:", json.dumps(best))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)

    import cv2

    rows = [json.loads(l) for l in (args.dataset / "manifest.jsonl").open()]
    val = [r for r in rows if r["split"] == "val"]
    fronts = [r for r in val if r["meta"]["view"] == "front"][: args.subjects]
    laterals = [r for r in val if r["meta"]["view"] != "front"][:8]
    print(f"val {len(val)}: {len(fronts)} front subjects, {len(laterals)} lateral", flush=True)

    gen = Generator(None if args.baseline else args.lora)
    results = {"lora": None if args.baseline else str(args.lora), "probes": {}}

    def control_of(row):
        return args.dataset / row["control"]

    def run(tag, row, meta_over, seed):
        meta = dict(row["meta"], **meta_over)
        # filename carries the axis value so make_sheets.py can group by it
        for k in ("volume_cc", "profile", "view"):
            if k in meta_over:
                tag = f"{tag}-{k}={meta_over[k]}"
        p = caption(meta)
        img = gen(control_of(row), p, seed)
        ctrl = cv2.imread(str(control_of(row)))
        rec = edit_magnitude(ctrl, img)
        rec.update(projection=projection(img),
                   projection_control=projection(ctrl),
                   pair_id=row["pair_id"], prompt=p, seed=seed)
        cv2.imwrite(str(args.out / f"{tag}__{row['pair_id']}__s{seed}.jpg"), img)
        return rec

    # C-0a  noise floor: same caption, three seeds. Everything else is measured
    # against this, so it runs first and is never trimmed.
    results["probes"]["noise_floor"] = [
        run("noise", r, {}, seed) for r in fronts[:6] for seed in (1, 2, 3)
    ]

    # CC1  monotonicity across the corpus range
    results["probes"]["cc_sweep"] = [
        run("cc", r, {"volume_cc": cc}, 1)
        for r in fronts for cc in (200, 250, 300, 350, 400, 450, 500, 550, 600)
    ]

    # CC2  the 10cc claim, inside the only densely-populated band (250-389)
    results["probes"]["cc_granularity"] = [
        run("gran", r, {"volume_cc": cc}, seed)
        for r in fronts[:6] for cc in (340, 350, 360, 370, 380, 390, 400)
        for seed in (1, 2, 3)
    ]

    # CC4  outside the corpus range 140-700 (lib/implants.ts allows up to 800)
    results["probes"]["cc_range"] = [
        run("range", r, {"volume_cc": cc}, 1)
        for r in fronts[:4] for cc in (100, 140, 700, 800, 900)
    ]

    # P1/P2/P3  profile, on laterals only - the only views where projection
    # separates moderate from high at a fixed volume.
    prof_probe = []
    for r in laterals:
        for prof in ("moderate", "moderate-plus", "high", "extra-high"):
            prof_probe.append(run("prof", r, {"volume_cc": 350, "profile": prof}, 1))
        m = dict(r["meta"]); m.pop("profile", None)
        p = caption(dict(m, volume_cc=350))
        img = gen(control_of(r), p, 1)
        ctrl = cv2.imread(str(control_of(r)))
        rec = edit_magnitude(ctrl, img)
        rec.update(projection=projection(img), pair_id=r["pair_id"],
                   prompt=p, seed=1, profile="OMITTED")
        cv2.imwrite(str(args.out / f"prof__{r['pair_id']}__omitted.jpg"), img)
        prof_probe.append(rec)
    results["probes"]["profile"] = prof_probe

    # V1  is the view clause inert? same image, same seed, clause on vs off
    view_probe = []
    for r in (fronts[:6] + laterals[:6]):
        view_probe.append(run("view_on", r, {}, 1))
        m = dict(r["meta"]); m.pop("view", None)
        p = caption(m)
        img = gen(control_of(r), p, 1)
        ctrl = cv2.imread(str(control_of(r)))
        rec = edit_magnitude(ctrl, img)
        rec.update(projection=projection(img), pair_id=r["pair_id"],
                   prompt=p, seed=1, view="OMITTED")
        cv2.imwrite(str(args.out / f"view_off__{r['pair_id']}.jpg"), img)
        view_probe.append(rec)
    results["probes"]["view_ablation"] = view_probe

    # V3  negative control: a FRONT image told it is a side profile must not
    # rotate the patient. Pose preservation has to win.
    results["probes"]["view_mismatch"] = [
        run("mismatch", r, {"view": "side-left"}, 1) for r in fronts[:6]
    ]

    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    print(f"wrote {args.out/'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
