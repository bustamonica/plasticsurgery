"""The training config must stay in sync with ai-toolkit's current
Qwen-Image-Edit example (drift table in the Phase A scope report). These
tests load the real YAML and assert the keys the 2509 run depends on."""

from pathlib import Path

import pytest
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "qwen_edit_lora.yaml"


@pytest.fixture(scope="module")
def process():
    config = yaml.safe_load(CONFIG_PATH.read_text())
    processes = config["config"]["process"]
    assert len(processes) == 1
    return processes[0]


def test_config_is_valid_yaml():
    assert yaml.safe_load(CONFIG_PATH.read_text())["job"] == "extension"


def test_process_type_is_diffusion_trainer(process):
    assert process["type"] == "diffusion_trainer"


def test_arch_is_qwen_image_edit_plus(process):
    # The 2509/2511 weights require the _plus arch; plain qwen_image_edit fails to load.
    assert process["model"]["arch"] == "qwen_image_edit_plus"
    assert process["model"]["name_or_path"] == "Qwen/Qwen-Image-Edit-2509"


def test_control_path_is_a_list(process):
    dataset = process["datasets"][0]
    assert isinstance(dataset["control_path"], list)
    assert len(dataset["control_path"]) == 1


def test_multiresolution_replaces_buckets(process):
    dataset = process["datasets"][0]
    assert dataset["resolution"] == [512, 768, 1024]
    assert "buckets" not in dataset


def test_train_block_uses_current_key_names(process):
    train = process["train"]
    assert train["gradient_accumulation"] >= 1
    assert "gradient_accumulation_steps" not in train
    assert train["timestep_type"] == "weighted"
    assert train["cache_text_embeddings"] is True


def test_quantization_stack_for_sub_80gb_cards(process):
    model = process["model"]
    assert model["quantize"] is True
    assert model["qtype"].startswith("uint3|")
    assert model["quantize_te"] is True
    assert model["low_vram"] is True


def test_samples_use_ctrl_img_1(process):
    samples = process["sample"]["samples"]
    assert samples, "config should ship at least one sample prompt"
    for sample in samples:
        assert "ctrl_img_1" in sample
        assert "ctrl_img" not in sample
        assert sample["prompt"]


def test_pinned_ai_toolkit_commit_is_documented():
    text = CONFIG_PATH.read_text()
    assert "6d8afa5684000b69db97cc40504a972a85615e3b" in text
