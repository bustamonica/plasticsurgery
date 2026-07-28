# Quickstart — testing morphengine (M0 + M1)

Everything below runs locally on macOS/Linux with Python ≥3.10. No GPU,
no accounts, no external assets needed for steps 1–7.

```bash
unzip morphengine-m1.zip && cd morphengine
python3 -m venv .venv && source .venv/bin/activate
```

## 1. Prove the build — full test suite (~2 min)

```bash
pip install -e ".[dev]"
pytest tests/ -q          # 84 passed, 8 skipped (painter tests need torch)
```

With torch installed (CPU wheel is fine):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pytest tests/ -q          # 92 passed
```

(Or one-shot: `bash run_tests_full.sh` — installs everything and runs all 92.)

## 2. Morph any real implant (the engine)

```bash
python3 -m morphengine.cli --list | head -20     # browse the 581-SKU catalog
python3 -m morphengine.cli --sku mentor-memorygel-350-hp \
    --placement submuscular --export-obj out/
```

Prints before/after measurements (achieved volume must read within ±2 cc of
rated) and writes `out/*_before.obj` / `*_after.obj` — open them in Blender,
MeshLab, or Windows 3D Viewer and compare silhouettes.

Try a guardrail case — the engine refuses/clamps nonsense instead of
producing garbage:

```bash
python3 -m morphengine.cli --sku sientra-opus-600-mod --placement dual-plane
```

## 3. The size-menu demo (product UX in one image)

```bash
python3 scripts/compare_sweep.py --out sweep.png
```

One body, three controlled strips: 250→550 cc sweep, profile sweep at
~350 cc (Moderate Classic → Ultra High), placement sweep (subglandular /
submuscular / dual-plane). Labels show cc-exact achieved volumes.

## 4. Generate a synthetic dataset

```bash
python3 scripts/generate_dataset.py --n 50 --seed 0 --size 256 --out ds_demo
python3 scripts/contact_sheet.py --manifest ds_demo/manifest.jsonl --out sheet.png
```

`ds_demo/` = before/after PNGs + depth/normal/mask conditioning `.npy` +
`manifest.jsonl` (full implant + engine provenance per pair). A ready-made
24-pair set is also available as `m1_dataset_demo.zip`.

## 5. Painter smoke train (CPU, ~2 min)

```bash
python3 - << 'PY'
from morphengine.painter.train import TrainConfig, train
cfg = TrainConfig(model="tiny", image_size=128, steps=100, batch_size=4,
                  out_dir="smoke_run")
print(train(cfg, "ds_demo/manifest.jsonl"))   # loss ~1.4 -> ~0.05
PY
```

## 6. Real painter training (GPU box)

`pip install -e ".[painter]"` on a CUDA box, generate 20–50k pairs with
`--resolution 6`, then follow the runbook:
**`src/morphengine/painter/README.md`** (box specs, launch command,
`configs/painter_v0.yaml`, eval + M2 export).

## What to expect / known v0 artifacts

- Achieved volumes within ±2 cc / ±1.5% of rated; base width within ±5%.
- Renders are stylized (ellipsoid fixture torso) — geometry bootstrap, not
  photorealism; that arrives with the trained painter + real-body models.
- Dome-rim crease visible on high-profile large implants (v1 skirted dome).
- Implant dimensions are verified manufacturer catalog values
  (`values_status: "verified"`, per-record citations).
