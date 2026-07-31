# Painter v0 — GPU Runbook (SPEC M1.5)

The painter learns the **before → after** edit from synthetic pairs produced by
`morphengine.datafactory`:

- **input**  = before RGB (3ch) + 6-channel geometry conditioning
  `[depth_before, depth_after, normal_after_x/y/z, mask_before]`
- **target** = after RGB

Two modes (`TrainConfig.model`):

| mode        | purpose                        | hardware        | deps                        |
|-------------|--------------------------------|-----------------|-----------------------------|
| `tiny`      | CPU smoke test / CI            | any CPU         | torch only (no diffusers)   |
| `sdxl-lora` | production weights (Stage-1)   | 1x A100 40GB    | torch + diffusers + peft …  |

---

## 0. Colab quickstart (free T4)

The fastest way to run the sdxl-lora path with zero local setup:

1. Open **`notebooks/painter_colab.ipynb`** in Google Colab
   (*File → Open notebook → GitHub → `bustamonica/plasticsurgery`*, or
   upload the file) and switch to a GPU runtime
   (*Runtime → Change runtime type → T4 GPU*).
2. Run the cells top to bottom: clone the repo → install the `[painter]`
   extra → upload `m1_dataset_demo.zip` (or generate 300 pairs on the fly)
   → load `configs/painter_v0.yaml` with T4-sized overrides → train via
   `morphengine.painter.train.train(cfg, manifest)` → sample a 2×4
   cond→output grid → download the checkpoint zip.

**Expected runtime on a free T4:** the notebook defaults
(256 px, batch 4, 2000 steps) take roughly **30–60 min** end to end,
including SDXL download (~7 GB) and install. The full
`configs/painter_v0.yaml` (512 px, batch 8, 20k steps) does **not** fit a
T4 — it needs the A100/4090 boxes in §1 (or Colab Pro/Pro+ A100).

**Free-tier credit/GPU notes:**

- Free Colab GPUs are T4-class (16 GB), not guaranteed, and sessions cap
  at ~12 h (often much less); idle sessions disconnect.
- `train.py` currently checkpoints **only at the end** of training
  (`save_every` in the yaml is not implemented), so keep `steps` small
  enough to finish inside one session — the notebook defaults do.
- 256 px / batch 4 / bf16 peaks around 10–12 GB VRAM on T4; 512 px /
  batch 8 will OOM.
- Known gaps in the sdxl-lora path, also flagged in the notebook: zero
  prompt-embedding placeholders (model is unconditional), `grad_clip`
  / `precision` yaml keys not read, and a suspected hard freeze of the
  extended `conv_in` (installed before `get_peft_model`, so peft freezes
  the new conditioning channels at zero-init — reported; fix pending in
  `painter/train.py::_train_sdxl_lora`).

---

## 1. Recommended box

- **Full config (`configs/painter_v0.yaml`, 512px, batch 8, bf16):**
  1x **A100 40GB** (or H100). Peak VRAM ≈ 30–36 GB
  (frozen SDXL VAE+UNet in bf16, LoRA adapters + cond encoder trainable).
- **Reduced config** on 1x **RTX 4090 24GB**: batch 4 + gradient
  accumulation 2, `image_size: 512`, `enable_gradient_checkpointing()` on the
  UNet (one line in `painter/train.py::_train_sdxl_lora`). Peak ≈ 20–22 GB.
- CPU-only box: only `model: tiny` is viable; it exists for smoke tests, not
  for quality.

## 2. Setup (exact commands)

```bash
# fresh box, python >= 3.10
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
# CUDA wheel matching the box (cu121 example; check pytorch.org for yours):
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install diffusers peft accelerate transformers
pip install -e .            # this repo (morphengine)
```

## 3. Generate the dataset (20k–50k pairs)

```bash
# on the GPU box or any CPU worker; deterministic given --seed
python scripts/generate_dataset.py \
    --out data/painter_v0 \
    --n 30000 \
    --size 256 \
    --seed 0
```

This writes `data/painter_v0/manifest.jsonl` + `images/` + `cond/` per the
SPEC M1.3 layout. 30k pairs at 256px ≈ 20–40 GB on disk; rendering is
CPU-bound (~2–6 pairs/s/core — parallelize with multiple seeds/shards and
concatenate manifests). Then point `data_manifest` in
`configs/painter_v0.yaml` at the manifest path.

## 4. Launch training (sdxl-lora)

```bash
python - <<'PY'
import json, yaml
from morphengine.painter.train import TrainConfig, train

cfg_dict = yaml.safe_load(open("configs/painter_v0.yaml"))
cfg = TrainConfig(
    model="sdxl-lora",
    image_size=cfg_dict["image_size"],       # 512
    lr=cfg_dict["lr"],                       # 1e-4
    batch_size=cfg_dict["batch_size"],       # 8
    steps=cfg_dict["steps"],                 # 20000
    lora_rank=cfg_dict["lora_rank"],
    lora_alpha=cfg_dict["lora_alpha"],
    base_model=cfg_dict["base_model"],
    out_dir=cfg_dict["out_dir"],
    seed=cfg_dict["seed"],
)
result = train(cfg, cfg_dict["data_manifest"])
print(json.dumps(result, indent=2))
PY
```

(Or wrap the above in a 5-line `scripts/train_painter.py` if preferred; the
config keys in `configs/painter_v0.yaml` map 1:1 onto `TrainConfig`.)

What the sdxl-lora path does (`painter/train.py::_train_sdxl_lora`):

1. Loads SDXL VAE (frozen) + UNet from `base_model` in **bf16**.
2. **Input-conv extension:** a small learned `cond_encoder` CNN maps
   `(before 3ch + cond 6ch)` at pixel res down to 4 channels on the VAE latent
   grid (H/8 × W/8); these are channel-concatenated with the 4-channel noisy
   latent. The UNet `conv_in` is extended 4→8 input channels — original 4
   channel weights copied verbatim, new channels zero-init, so step 0 matches
   pretrained behavior.
3. **LoRA** (`peft.LoraConfig`, rank 16 / alpha 16→32 per config) injected on
   `to_q/to_k/to_v/to_out.0` of every attention block; only LoRA weights, the
   extended `conv_in`, and `cond_encoder` are trainable (~15–30M params).
4. Loss = MSE on predicted noise (standard DDPM epsilon objective);
   AdamW(lr) + **cosine** schedule (eta_min = 1% of peak) over `steps`.
5. **EMA** (decay 0.999) of trainable weights, swapped in before export.

**Status:** this path is a well-structured skeleton — config plumbing, LoRA
injection, and the channel-concat input-conv extension are implemented; the
prompt text-encoder embeds are currently zero placeholders (unconditional)
and are the first thing to wire up on the GPU box (encode
`batch["prompt"]` with the two SDXL text encoders, feed
`encoder_hidden_states` + `added_cond_kwargs`).

## 5. VRAM / time ballparks

| config | VRAM | throughput | 20k steps |
|---|---|---|---|
| A100 40GB, 512px, b8, bf16 | ~30–36 GB | ~4–6 it/s | **~1–1.5 h** |
| RTX 4090, 512px, b4×accum2, grad-ckpt | ~20–22 GB | ~2–3 it/s | ~2.5–3.5 h |
| A100 40GB, 256px, b16 | ~24 GB | ~10 it/s | ~35 min (debug) |

Dataset generation: ~30k pairs ≈ 4–8 h on 16 CPU cores.

## 6. Evaluating checkpoints

```python
import torch
from diffusers import StableDiffusionXLPipeline, UNet2DConditionModel
from peft import PeftModel

unet = UNet2DConditionModel.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0", subfolder="unet",
    torch_dtype=torch.bfloat16)
unet = PeftModel.from_pretrained(unet, "painter_runs/sdxl-lora-v0/unet_lora")
# rebuild cond_encoder from painter_runs/sdxl-lora-v0/cond_encoder.pt and
# repeat the conv_in extension (8ch) before inference — same code as train.
```

Eval protocol: hold out the last 500 manifest rows (or a second
`--seed 1` factory run) as a val set; per checkpoint compute (a) pixel
L1/PSNR vs the ground-truth after render, (b) mask-region L1 only (the
surgical edit area), (c) a handful of side-by-side grids per implant profile
class. Pick the checkpoint with the best masked-L1 — full-frame metrics are
dominated by unchanged background.

## 7. Exporting weights for M2 inference

After `train()` returns:

```
painter_runs/sdxl-lora-v0/
├── unet_lora/            # peft adapter (safetensors) — EMA weights
├── cond_encoder.pt       # state_dict of the conditioning CNN
├── checkpoint_last.pt    # full unet state_dict (EMA) + config
└── config.json           # TrainConfig used
```

M2 inference loads: frozen SDXL VAE+UNet → apply `unet_lora` adapter via
`peft` → re-apply the documented 8-channel `conv_in` extension → load
`cond_encoder.pt`. All four artifacts + this README's §4 description are
sufficient to reconstruct the exact inference graph; `config.json` pins the
base model and LoRA hyperparameters.
