# Notebooks

**`painter_colab.ipynb`** — one-click Google Colab trainer for the morphengine
painter (diffusion) path: it clones this repo, installs the `[painter]` extra,
loads a dataset (demo-zip upload or a small on-the-fly
`scripts/generate_dataset.py` run), resolves `configs/painter_v0.yaml` into the
repo's `TrainConfig` with T4-sized overrides, trains SDXL + LoRA via
`morphengine.painter.train.train`, samples a 2×4 conditioning→output grid, and
downloads a zip of the checkpoints. Prerequisites: a Google account and a free
Colab GPU runtime (T4; *Runtime → Change runtime type*). Expected outputs:
`unet_lora/` peft adapter weights, `cond_encoder.pt`, `checkpoint_last.pt`,
and `config.json`, plus a demo-run loss curve in the cell logs — see
`src/morphengine/painter/README.md` §0 for runtime/credit notes and known gaps
in the current training path.

**Demo datasets** (upload one in the notebook's data cell):
- `m1_dataset_demo.zip` — synthetic-fixture bodies (M1, fast to regenerate,
  zero external assets).
- `m2_dataset_demo.zip` — **real Anny bodies** (factory v2, recommended): 23
  pairs across 4 brands / 4 profile classes / both cameras, 190–625 cc, drawn
  from seeded phenotypes (`AnnyBodySampler`), every pair guardrail-passed with
  achieved volume within ±3 % of the SKU. Regenerate or enlarge with
  `DatasetFactory(...).generate(n, seed=…, body_sampler=AnnyBodySampler(seed=…))`.
