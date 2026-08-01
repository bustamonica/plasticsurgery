"""Painter inference (M4): load a trained checkpoint, paint conditioning -> after.

Rebuilds the exact training-time graph (painter/README §6–7):
frozen SDXL VAE, UNet with the 4->8 ch conv_in extension, peft LoRA adapter
from ``unet_lora/``, the small ``cond_encoder`` CNN from ``cond_encoder.pt``,
DDPM sampling with the same zero prompt embeds as training (the model is
unconditional — geometry, not text, steers the edit).

The 6-channel conditioning map MUST be built by
``morphengine.painter.dataset.build_cond`` (shared with training).

Heavy deps (torch/diffusers/peft) are imported lazily; models can also be
injected for CPU tests via ``from_parts``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .dataset import build_cond

COND_LATENT_CH = 4
COND_IN_CH = 9          # before(3) + cond(6)
DEFAULT_STEPS = 30


class PainterInference:
    """Conditioned diffusion sampler for the painter.

    Use ``from_ckpt`` for real checkpoints, ``from_parts`` to inject models
    directly (tests, CPU stubs).
    """

    def __init__(self, vae, unet, cond_encoder, scheduler,
                 device, dtype, image_size: int = 256):
        self.vae = vae
        self.unet = unet
        self.cond_encoder = cond_encoder
        self.scheduler = scheduler
        self.device = device
        self.dtype = dtype
        self.image_size = int(image_size)

    # -- constructors ---------------------------------------------------

    @classmethod
    def from_parts(cls, vae, unet, cond_encoder, scheduler,
                   device, dtype, image_size: int = 256) -> "PainterInference":
        return cls(vae, unet, cond_encoder, scheduler, device, dtype, image_size)

    @classmethod
    def from_ckpt(cls, ckpt_dir: str | Path, device: str | None = None,
                  image_size: int | None = None) -> "PainterInference":
        """Load a training export (unet_lora/ + cond_encoder.pt + config.json)."""
        try:
            import torch
            import torch.nn as nn
            from diffusers import AutoencoderKL, DDPMScheduler, UNet2DConditionModel
            from peft import PeftModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "install the painter extra: "
                "pip install torch diffusers peft accelerate transformers"
            ) from exc

        ckpt_dir = Path(ckpt_dir)
        cfg = json.loads((ckpt_dir / "config.json").read_text())
        base_model = cfg["base_model"]
        image_size = int(image_size or cfg.get("image_size", 256))

        dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        dtype = torch.bfloat16 if dev.type == "cuda" else torch.float32

        vae = AutoencoderKL.from_pretrained(
            base_model, subfolder="vae", torch_dtype=dtype).to(dev)
        vae.requires_grad_(False).eval()

        unet = UNet2DConditionModel.from_pretrained(
            base_model, subfolder="unet", torch_dtype=dtype)
        # re-apply the documented conv_in extension (4 -> 4 + cond_latent_ch)
        old = unet.conv_in
        new = nn.Conv2d(old.in_channels + COND_LATENT_CH, old.out_channels,
                        kernel_size=old.kernel_size, stride=old.stride,
                        padding=old.padding, dtype=old.weight.dtype)
        with torch.no_grad():
            new.weight.zero_()
            new.weight[:, : old.in_channels] = old.weight
            new.bias.copy_(old.bias)
        unet.conv_in = new
        unet.config.in_channels = new.in_channels
        unet = PeftModel.from_pretrained(unet, ckpt_dir / "unet_lora").to(dev).eval()

        cond_encoder = nn.Sequential(
            nn.Conv2d(COND_IN_CH, 32, 3, stride=2, padding=1), nn.SiLU(),
            nn.Conv2d(32, 32, 3, stride=2, padding=1), nn.SiLU(),
            nn.Conv2d(32, COND_LATENT_CH, 3, stride=2, padding=1),
        ).to(device=dev, dtype=dtype)
        cond_encoder.load_state_dict(
            torch.load(ckpt_dir / "cond_encoder.pt", map_location=dev))
        cond_encoder.eval()

        scheduler = DDPMScheduler.from_pretrained(base_model, subfolder="scheduler")
        return cls(vae, unet, cond_encoder, scheduler, dev, dtype, image_size)

    # -- sampling ---------------------------------------------------------

    def paint(self, before: np.ndarray, cond: np.ndarray,
              steps: int = DEFAULT_STEPS, seed: int | None = None) -> np.ndarray:
        """before (3,H,W) [-1,1] + cond (6,H,W) -> after (H,W,3) float [0,1]."""
        import torch

        b = torch.as_tensor(before, dtype=self.dtype)[None].to(self.device)
        c = torch.as_tensor(cond, dtype=self.dtype)[None].to(self.device)
        with torch.no_grad():
            cond_lat = self.cond_encoder(torch.cat([b, c], dim=1))

            g = (torch.Generator(device=self.device).manual_seed(seed)
                 if seed is not None else None)
            lat = torch.randn(
                1, 4, self.image_size // 8, self.image_size // 8,
                device=self.device, dtype=self.dtype, generator=g)
            hidden = torch.zeros(1, 77, self.unet.config.cross_attention_dim,
                                 device=self.device, dtype=self.dtype)
            added = {
                "text_embeds": torch.zeros(1, 1280, device=self.device, dtype=self.dtype),
                "time_ids": torch.zeros(1, 6, device=self.device, dtype=self.dtype),
            }
            self.scheduler.set_timesteps(steps, device=self.device)
            for t in self.scheduler.timesteps:
                eps = self.unet(torch.cat([lat, cond_lat], dim=1), t, hidden,
                                added_cond_kwargs=added).sample
                lat = self.scheduler.step(eps, t, lat).prev_sample
            img = self.vae.decode(lat / self.vae.config.scaling_factor).sample[0]
        return (img.float().clamp(-1, 1).cpu().numpy() * 0.5 + 0.5).transpose(1, 2, 0)

    def paint_geometry(self, before_rgb: np.ndarray,
                       depth_before: np.ndarray, depth_after: np.ndarray,
                       normal_after: np.ndarray, mask_before: np.ndarray,
                       steps: int = DEFAULT_STEPS,
                       seed: int | None = None) -> np.ndarray:
        """Renderer-level convenience: (H,W,3) [0,1] rgb + geometry maps.

        ``before_rgb`` is resized/expected at ``self.image_size``; geometry
        maps go through the shared ``build_cond`` normalization.
        """
        size = self.image_size
        if before_rgb.shape[:2] != (size, size):
            from PIL import Image
            img = Image.fromarray((np.clip(before_rgb, 0, 1) * 255).astype(np.uint8))
            img = img.resize((size, size), Image.BILINEAR)
            before_rgb = np.asarray(img, dtype=np.float32) / 255.0
        from .dataset import _nearest_resize
        cond = build_cond(
            _nearest_resize(depth_before.astype(np.float32), size),
            _nearest_resize(depth_after.astype(np.float32), size),
            _nearest_resize(normal_after.astype(np.float32), size),
            _nearest_resize(mask_before.astype(np.float32), size),
        )
        before = (before_rgb * 2.0 - 1.0).transpose(2, 0, 1).astype(np.float32)
        return self.paint(before, cond.transpose(2, 0, 1).astype(np.float32),
                          steps=steps, seed=seed)
