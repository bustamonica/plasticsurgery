"""Regression test: extended conv_in must stay trainable under peft wrapping.

Bug history (found by Track C static review, 2026-07-31): the SDXL+LoRA path
extends ``unet.conv_in`` from 4 -> 4+cond channels (new channels zero-init) and
then wraps the UNet with ``get_peft_model``. peft's default freezes every
parameter that is not a LoRA target — so the new conditioning channels were
stuck at exactly zero and geometry conditioning contributed nothing. Fixed by
``modules_to_save=["conv_in"]`` in the LoraConfig.

This test mirrors that exact pattern on a tiny stand-in module (no SDXL
weights needed): peft wraps any torch module whose submodule names match
``target_modules``.
"""
import pytest

torch = pytest.importorskip("torch")
peft = pytest.importorskip("peft")


class _TinyUnet(torch.nn.Module):
    def __init__(self, in_ch: int = 8):
        super().__init__()
        # 8 channels = 4 latent + 4 conditioning, exactly like the real path
        self.conv_in = torch.nn.Conv2d(in_ch, 8, 3, padding=1)
        self.to_q = torch.nn.Linear(8, 8)


def _wrap(model, modules_to_save=None):
    cfg_kwargs = dict(
        r=4,
        lora_alpha=8,
        init_lora_weights="gaussian",
        target_modules=["to_q"],
    )
    if modules_to_save is not None:
        cfg_kwargs["modules_to_save"] = modules_to_save
    return peft.get_peft_model(model, peft.LoraConfig(**cfg_kwargs))


def _conv_in_trainable(model) -> bool:
    """Is conv_in trainable on the ACTIVE forward path?

    With modules_to_save, peft keeps a frozen inert ``original_module`` copy
    and routes the forward through ``modules_to_save.default`` — the copy that
    must be trainable. Without modules_to_save, conv_in params are frozen
    outright (the bug)."""
    active = [
        p.requires_grad
        for name, p in model.named_parameters()
        if ("conv_in" in name and "modules_to_save" in name)
        or (".conv_in." in name and "original_module" not in name
            and "modules_to_save" not in name)
    ]
    return bool(active) and all(active)


def test_conv_in_frozen_without_modules_to_save():
    """Documents the original bug: default peft config freezes conv_in."""
    model = _wrap(_TinyUnet())
    assert not _conv_in_trainable(model)


def test_conv_in_trainable_with_modules_to_save():
    """The fix: modules_to_save=['conv_in'] keeps it fully trainable."""
    model = _wrap(_TinyUnet(), modules_to_save=["conv_in"])
    assert _conv_in_trainable(model)
    # optimizer-style filter (train.py:277) must include conv_in params
    trainable = [n for n, p in model.named_parameters() if p.requires_grad]
    assert any("conv_in" in n for n in trainable)
    # LoRA params still present and trainable too
    assert any("lora_" in n for n in trainable)


def test_train_py_lora_config_has_modules_to_save():
    """Guard the actual config in train.py against regression."""
    import inspect
    from morphengine.painter import train as train_mod

    src = inspect.getsource(train_mod)
    assert 'modules_to_save=["conv_in"]' in src
