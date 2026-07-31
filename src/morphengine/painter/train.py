"""Painter training pipeline (SPEC M1.5).

The painter learns the before→after edit: input = before RGB (3ch) concatenated
with 6-channel geometry conditioning (depth_before, depth_after,
normal_after_xyz, mask_before), target = after RGB.

Two modes:

- ``model="tiny"``      — pure-torch compact UNet (~1M params), CPU-capable
                          smoke loop. NO diffusers import anywhere in this path.
- ``model="sdxl-lora"`` — GPU path: SDXL base + LoRA adapters, conditioned by
                          channel-concatenating the geometry maps into the UNet
                          latent input (input-conv extension). Requires the
                          painter extra (torch diffusers peft accelerate
                          transformers); see painter/README.md runbook.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .dataset import PairDataset


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class TrainConfig:
    model: str  # "tiny" (CPU smoke) | "sdxl-lora" (GPU runbook)
    image_size: int = 256
    lr: float = 1e-4
    batch_size: int = 4
    steps: int = 100
    lora_rank: int = 16
    lora_alpha: int = 32
    base_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    out_dir: str = "painter_runs/run0"
    seed: int = 0


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# --------------------------------------------------------------------------- #
# Tiny UNet (pure torch; ~1M params at base=32)
# --------------------------------------------------------------------------- #
def _conv_block(in_ch: int, out_ch: int):
    import torch.nn as nn

    groups = min(8, out_ch)
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, 3, padding=1),
        nn.GroupNorm(groups, out_ch),
        nn.SiLU(),
        nn.Conv2d(out_ch, out_ch, 3, padding=1),
        nn.GroupNorm(groups, out_ch),
        nn.SiLU(),
    )


def build_tiny_unet(in_ch: int = 9, out_ch: int = 3, base: int = 32):
    """Compact UNet: concat(before 3ch, cond 6ch) -> after 3ch (~1M params).

    2 down levels + bottleneck, GroupNorm + SiLU everywhere, bilinear upsample
    with skip connections. Pure torch — no diffusers import.
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    b = base

    class _UNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc0 = _conv_block(in_ch, b)  # full res
            self.down1 = nn.Conv2d(b, b * 2, 3, stride=2, padding=1)
            self.enc1 = _conv_block(b * 2, b * 2)  # 1/2 res
            self.down2 = nn.Conv2d(b * 2, b * 4, 3, stride=2, padding=1)
            self.bottleneck = nn.Sequential(
                _conv_block(b * 4, b * 4), _conv_block(b * 4, b * 4)
            )  # 1/4 res
            self.up2 = nn.Conv2d(b * 4 + b * 2, b * 2, 3, padding=1)
            self.dec1 = _conv_block(b * 2, b * 2)
            self.up1 = nn.Conv2d(b * 2 + b, b, 3, padding=1)
            self.dec0 = _conv_block(b, b)
            self.head = nn.Conv2d(b, out_ch, 3, padding=1)

        def forward(self, x):
            e0 = self.enc0(x)  # (B,b,H,W)
            e1 = self.enc1(self.down1(e0))  # (B,2b,H/2,W/2)
            mid = self.bottleneck(self.down2(e1))  # (B,4b,H/4,W/4)
            u2 = F.interpolate(
                mid, size=e1.shape[-2:], mode="bilinear", align_corners=False
            )
            u2 = self.up2(torch.cat([u2, e1], dim=1))
            d1 = self.dec1(u2)  # (B,2b,H/2,W/2)
            u1 = F.interpolate(
                d1, size=e0.shape[-2:], mode="bilinear", align_corners=False
            )
            u1 = self.up1(torch.cat([u1, e0], dim=1))
            d0 = self.dec0(u1)  # (B,b,H,W)
            return self.head(d0)  # (B,out_ch,H,W)

    return _UNet()


# --------------------------------------------------------------------------- #
# Shared checkpoint helper
# --------------------------------------------------------------------------- #
def _save_ckpt(out_dir: str | Path, model_state: dict, cfg: TrainConfig) -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_path = out / "checkpoint_last.pt"
    import torch

    torch.save(
        {"model_state": model_state, "config": dataclasses.asdict(cfg)}, ckpt_path
    )
    with open(out / "config.json", "w", encoding="utf-8") as fh:
        json.dump(dataclasses.asdict(cfg), fh, indent=2)
    return str(ckpt_path)


# --------------------------------------------------------------------------- #
# Tiny mode: pure-torch CPU-capable training loop
# --------------------------------------------------------------------------- #
def _train_tiny(cfg: TrainConfig, manifest: str | Path) -> dict:
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader

    _seed_everything(cfg.seed)

    ds = PairDataset(manifest, image_size=cfg.image_size)
    generator = torch.Generator().manual_seed(cfg.seed)
    loader = DataLoader(
        ds, batch_size=cfg.batch_size, shuffle=True, generator=generator
    )

    model = build_tiny_unet(in_ch=9, out_ch=3, base=32)
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr)

    final_loss = math.nan
    step = 0
    while step < cfg.steps:
        for batch in loader:
            x = torch.cat([batch["before"], batch["cond"]], dim=1)  # (B,9,H,W)
            target = batch["after"]  # (B,3,H,W)
            pred = model(x)
            loss = F.l1_loss(pred, target) + 0.5 * F.mse_loss(pred, target)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            final_loss = float(loss.detach().item())
            step += 1
            if step % 10 == 0 or step == 1:
                print(f"[tiny] step {step}/{cfg.steps}  loss={final_loss:.6f}")
            if step >= cfg.steps:
                break

    ckpt = _save_ckpt(cfg.out_dir, model.state_dict(), cfg)
    return {"final_loss": final_loss, "steps": step, "ckpt": ckpt}


# --------------------------------------------------------------------------- #
# SDXL-LoRA mode (GPU; hardened on the GPU box — see painter/README.md)
# --------------------------------------------------------------------------- #
def _train_sdxl_lora(cfg: TrainConfig, manifest: str | Path) -> dict:
    try:
        import diffusers  # noqa: F401
        import peft  # noqa: F401
    except ImportError as exc:  # pragma: no cover - torch-free / CPU sandbox
        raise RuntimeError(
            "install the painter extra: "
            "pip install torch diffusers peft accelerate transformers"
        ) from exc

    import torch
    import torch.nn.functional as F
    from diffusers import AutoencoderKL, DDPMScheduler, UNet2DConditionModel
    from peft import LoraConfig, get_peft_model
    from torch.utils.data import DataLoader

    _seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32

    # --- frozen SDXL components --------------------------------------------
    # VAE encodes after/before RGB to latents; frozen.
    vae = AutoencoderKL.from_pretrained(
        cfg.base_model, subfolder="vae", torch_dtype=dtype
    ).to(device)
    vae.requires_grad_(False)

    # --- input-conv extension for geometry conditioning ---------------------
    # Strategy: encode `before` and the 6 cond channels with the SAME frozen
    # VAE is not possible for non-RGB maps, so instead we downsample the 6
    # cond maps with a small learned 'cond_encoder' CNN to the latent grid
    # (H/8 x W/8), and concatenate with the 4-channel noisy latent. The UNet's
    # conv_in is then extended from 4 -> 4 + cond_latent_ch input channels:
    # the original 4 channel weights are copied verbatim, the new channels are
    # zero-initialized so training starts from the pretrained behavior.
    cond_latent_ch = 4
    unet = UNet2DConditionModel.from_pretrained(
        cfg.base_model, subfolder="unet", torch_dtype=dtype
    )
    old_conv = unet.conv_in
    new_in = old_conv.in_channels + cond_latent_ch
    new_conv = torch.nn.Conv2d(
        new_in,
        old_conv.out_channels,
        kernel_size=old_conv.kernel_size,
        stride=old_conv.stride,
        padding=old_conv.padding,
        dtype=old_conv.weight.dtype,
    )
    with torch.no_grad():
        new_conv.weight.zero_()
        new_conv.weight[:, : old_conv.in_channels] = old_conv.weight
        new_conv.bias.copy_(old_conv.bias)
    unet.conv_in = new_conv
    unet.config.in_channels = new_in
    unet.to(device)

    # --- LoRA injection (attention + feed-forward projections) --------------
    lora_cfg = LoraConfig(
        r=cfg.lora_rank,
        lora_alpha=cfg.lora_alpha,
        init_lora_weights="gaussian",
        target_modules=["to_q", "to_k", "to_v", "to_out.0"],
        # conv_in carries the extended conditioning channels (zero-init).
        # peft freezes every non-LoRA param by default, which would keep the
        # new channels at exactly zero — geometry conditioning dead on arrival.
        # modules_to_save keeps conv_in fully trainable (and in the optimizer
        # and EMA, both of which filter on requires_grad).
        modules_to_save=["conv_in"],
    )
    unet = get_peft_model(unet, lora_cfg)
    unet.print_trainable_parameters()

    # Small learned CNN that maps (before 3ch + cond 6ch) at pixel resolution
    # to cond_latent_ch channels on the latent grid. Trained jointly.
    import torch.nn as nn

    cond_encoder = nn.Sequential(
        nn.Conv2d(9, 32, 3, stride=2, padding=1),
        nn.SiLU(),
        nn.Conv2d(32, 32, 3, stride=2, padding=1),
        nn.SiLU(),
        nn.Conv2d(32, cond_latent_ch, 3, stride=2, padding=1),
    ).to(device=device, dtype=dtype)

    # Text conditioning: SDXL pooled + sequence embeds from the manifest
    # prompt are precomputed by the text encoders on the GPU box. Skeleton
    # simplification: zero prompt embeds (unconditional) — replace with
    # CLIPTextModel/CLIPTextModelWithProjection encodes of batch["prompt"].
    unet.config.cross_attention_dim  # noqa: B018 - documents the dim to fill

    noise_scheduler = DDPMScheduler.from_pretrained(
        cfg.base_model, subfolder="scheduler"
    )

    ds = PairDataset(manifest, image_size=cfg.image_size)
    loader = DataLoader(ds, batch_size=cfg.batch_size, shuffle=True, drop_last=True)

    params = [p for p in unet.parameters() if p.requires_grad] + list(
        cond_encoder.parameters()
    )
    opt = torch.optim.AdamW(params, lr=cfg.lr)
    # Cosine schedule over cfg.steps.
    lr_sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=cfg.steps, eta_min=cfg.lr * 0.01
    )

    # EMA of trainable weights.
    ema = {
        k: v.detach().clone().float()
        for k, v in unet.state_dict().items()
        if v.requires_grad
    }
    ema_decay = 0.999

    final_loss = math.nan
    step = 0
    while step < cfg.steps:
        for batch in loader:
            before = batch["before"].to(device=device, dtype=dtype)
            after = batch["after"].to(device=device, dtype=dtype)
            cond = batch["cond"].to(device=device, dtype=dtype)

            with torch.no_grad():
                latents = vae.encode(after).latent_dist.sample()
                latents = latents * vae.config.scaling_factor
                noise = torch.randn_like(latents)
                ts = torch.randint(
                    0,
                    noise_scheduler.config.num_train_timesteps,
                    (latents.shape[0],),
                    device=device,
                )
                noisy = noise_scheduler.add_noise(latents, noise, ts)

            cond_lat = cond_encoder(torch.cat([before, cond], dim=1))
            model_in = torch.cat([noisy, cond_lat], dim=1)

            # Zero prompt embeds (skeleton; see note above). Shapes per SDXL:
            # added_cond_kwargs: text_embeds (B,1280), time_ids (B,6).
            b_ = latents.shape[0]
            encoder_hidden = torch.zeros(
                b_, 77, unet.config.cross_attention_dim, device=device, dtype=dtype
            )
            added = {
                "text_embeds": torch.zeros(b_, 1280, device=device, dtype=dtype),
                "time_ids": torch.zeros(b_, 6, device=device, dtype=dtype),
            }
            pred = unet(
                model_in, ts, encoder_hidden, added_cond_kwargs=added
            ).sample
            loss = F.mse_loss(pred.float(), noise.float())
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            lr_sched.step()
            final_loss = float(loss.detach().item())

            with torch.no_grad():
                for k, v in unet.state_dict().items():
                    if k in ema:
                        ema[k].mul_(ema_decay).add_(v.float(), alpha=1 - ema_decay)

            step += 1
            if step % 10 == 0 or step == 1:
                print(
                    f"[sdxl-lora] step {step}/{cfg.steps}  loss={final_loss:.6f}"
                    f"  lr={lr_sched.get_last_lr()[0]:.2e}"
                )
            if step >= cfg.steps:
                break

    # Save LoRA adapters (EMA weights swapped in) + cond_encoder + config.
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sd = unet.state_dict()
    for k in ema:
        sd[k] = ema[k].to(sd[k].dtype)
    unet.save_pretrained(out / "unet_lora")  # peft adapter export
    torch.save(cond_encoder.state_dict(), out / "cond_encoder.pt")
    with open(out / "config.json", "w", encoding="utf-8") as fh:
        json.dump(dataclasses.asdict(cfg), fh, indent=2)
    ckpt = _save_ckpt(out, sd, cfg)
    return {"final_loss": final_loss, "steps": step, "ckpt": ckpt}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def train(cfg: TrainConfig, manifest: str | Path) -> dict:
    """Train the painter. Returns {"final_loss": float, "steps": int, "ckpt": path}."""
    if cfg.model == "tiny":
        return _train_tiny(cfg, manifest)
    if cfg.model == "sdxl-lora":
        return _train_sdxl_lora(cfg, manifest)
    raise ValueError(f"unknown model {cfg.model!r}; expected 'tiny' or 'sdxl-lora'")
