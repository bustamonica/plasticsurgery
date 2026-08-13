# Training our own augmentation-preview model

This directory holds everything for training a custom image-editing model on
consented before/after photo pairs from partner clinics, to eventually replace
the hosted Gemini model in `app/api/generate/route.ts`.

## The stack

| Piece | Choice | Why |
| --- | --- | --- |
| Base model | [Qwen-Image-Edit](https://huggingface.co/Qwen/Qwen-Image-Edit-2509) | Open weights, Apache 2.0 (commercially usable, no license fee), instruction-based image editing — same shape as our task |
| Method | LoRA fine-tune | Cheap ($50–500/run), fast to iterate, small artifacts |
| Trainer | [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) | Supports paired control→target editing datasets for Qwen-Image-Edit |
| GPU | RunPod / Lambda, 1× A100 80GB (or H100) | ~$2–4/hr rented. Hosted trainers (Replicate, fal) prohibit clinical nudity in their ToS — self-managed GPU is the realistic path. Rent per run; no idle cost |
| Serving | RunPod serverless worker or a small always-on GPU pod exposing an HTTP endpoint | The site's `/api/generate` route then points at it instead of Gemini — one file changes |

## Data governance (read first)

- **Never commit photos to git.** `training/data/` is gitignored; keep it that way.
- Every pair MUST carry a `consent_ref` pointing to the signed clinic
  agreement it came from. `ingest.py` rejects pairs without one.
- De-identification (`deidentify.py`) is mandatory before anything reaches a
  training folder or leaves your machine: faces blurred or cropped, EXIF/GPS
  stripped. The model only needs the chest region — it never needs a face.
- Keep the raw originals on an encrypted drive; treat them as medical records.
- Every pair MUST carry a `clothing` value, even though `dataset_schema.json`
  marks it optional. `ingest.py` rejects pairs without one: `build_caption()`
  reads an absent value as clothed and then instructs the model to preserve
  clothing that is not in a nude photograph. Legacy pairs collected before this
  gate fail it until the clinic retro-labels them; that is intended.
- Censored and annotated photos are rejected, not repaired (`censorship.py`).
  A censored pair is worse than a missing one - the v1 LoRA learned to reproduce
  a clinic's blur bands. Corner clinic watermarks are fine and are kept.

## Pipeline

```
raw photos from clinic          training/data/raw/<clinic>/<pair-id>/
        │                         ├── before.jpg
        ▼                         ├── after.jpg
1. scripts/ingest.py              └── meta.json   (see dataset_schema.json)
   validates pairs + metadata, strips EXIF, dedupes, min-resolution check,
   rejects censored/annotated images (scripts/censorship.py)
        │
        ▼                       training/data/staging/
2. scripts/deidentify.py
   detects & blurs faces (or hard-crops the top of the image)
        │
        ▼                       training/data/clean/
3. scripts/build_dataset.py
   writes instruction captions from the metadata (same wording the website
   uses at inference time), splits train/val
        │
        ▼                       training/data/dataset/
                                  ├── train/target/*.jpg + *.txt   (after + caption)
                                  ├── train/control/*.jpg          (before)
                                  ├── val/...
                                  └── manifest.jsonl
```

Run it (requires **Python >= 3.10** - ai-toolkit itself needs 3.10+, 3.12 recommended; the pipeline scripts additionally carry a `from __future__ import annotations` shim so they still run on 3.9 for local data prep):

```bash
cd training
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: pull a consented clinic gallery into data/raw (polite, cached;
# see scripts/scrape_gallery.py docstring for the consent + annotation model)
python scripts/scrape_gallery.py --clinic drkolker --out data/raw --annotations annotations.json

python scripts/ingest.py       data/raw data/staging
python scripts/deidentify.py   data/staging data/clean        # add --allow-no-face after auditing a sample
python scripts/build_dataset.py data/clean data/dataset --val-fraction 0.1
```

Audit `data/clean` visually before training — every image, every batch. You
are checking two things: no identifiable faces survived, and the pair is
actually the same patient/pose.

## Synthetic smoke-test corpus

Before any real consented data exists, generate a schema-valid synthetic corpus
to exercise the whole pipeline (and later the GPU smoke run):

```bash
python scripts/generate_synthetic.py data/raw --count 100 --seed 42
```

The "after" images are programmatic warps of the "before" images, sized by
`volume_cc`, with balanced `clothing` coverage and unique pixels per pair (so
the ingest dedup check is exercised too).
The images are headless, so `deidentify.py` needs `--allow-no-face`.
**Never mix these pairs into a real training corpus** - they teach the model
nothing medically meaningful; they only prove the tooling works end to end.

## Tests

```bash
pip install -r requirements.txt   # includes pytest, pyyaml, jsonschema
python -m pytest tests -q
```

Covers ingest validation/rejection paths, `dataset_schema.json` (including the
guarantee that the optional chart/frame fields never reach a caption), caption
assembly with its `clothing` variants, the censorship detector, and a drift guard
on `configs/qwen_edit_lora.yaml`. The detector's tests draw their own torsos -
no patient imagery is ever committed. Two of them reference the real corpus by
path and skip when it is not mounted.

## Training on RunPod

1. Create a pod: 1× A100 80GB, the official PyTorch template (Python >= 3.10, torch per the ai-toolkit README), attach a volume.
2. `git clone https://github.com/ostris/ai-toolkit && cd ai-toolkit && git checkout 6d8afa5684000b69db97cc40504a972a85615e3b && pip install -r requirements.txt`
   (the pinned commit our config was validated against; if you take a newer one, re-diff `config/examples/train_lora_qwen_image_edit_2509_32gb.yaml` first)
3. Upload `data/dataset/` to the volume (e.g. `runpodctl send` or rsync over SSH).
4. Copy `configs/qwen_edit_lora.yaml` into `ai-toolkit/config/`, adjust paths,
   and sync its keys with the current example config in the ai-toolkit repo
   (`config/examples/`) — the toolkit evolves quickly.
5. `python run.py config/qwen_edit_lora.yaml`
6. Checkpoints + sample grids land in `output/`. Evaluate on the val split:
   the samples are generated from val control images with val captions —
   compare against the real "after" photos.

Rules of thumb: ~1,000 pairs = first signs of life; 3,000–5,000 well-labeled
pairs = production candidate. Under ~500 pairs, expect it to memorize rather
than generalize — keep collecting before drawing conclusions.

## Evaluation checklist per run

- Identity preservation: is everything outside the chest region untouched?
- Instruction adherence: does 250 cc look different from 500 cc? Round vs teardrop?
- Realism: skin texture, lighting continuity, no plastic sheen.
- Failure audit: collect the worst 20 outputs, categorize, fix data or captions.

## Wiring the trained model into the site

Deploy the LoRA behind a small HTTP endpoint (RunPod serverless has a
diffusers worker template), then in `app/api/generate/route.ts` swap the
Gemini fetch for your endpoint (the `AI_PROVIDER` env seam in `.env.example`;
`gemini` stays the default).
Use `buildCustomModelPrompt()` from `lib/prompt.ts` for the instruction - it
emits exactly the `build_caption()` format the model was trained on, unlike
the Gemini prompt.
The request/response contract of
`/api/generate` (base64 in, base64 out) does not need to change, and demo
mode still works for local dev.
