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

## Pipeline

```
raw photos from clinic          training/data/raw/<clinic>/<pair-id>/
        │                         ├── before.jpg
        ▼                         ├── after.jpg
1. scripts/ingest.py              └── meta.json   (see dataset_schema.json)
   validates pairs + metadata, strips EXIF, dedupes, min-resolution check
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

Run it:

```bash
cd training
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/ingest.py       data/raw data/staging
python scripts/deidentify.py   data/staging data/clean        # add --allow-no-face after auditing a sample
python scripts/build_dataset.py data/clean data/dataset --val-fraction 0.1
```

Audit `data/clean` visually before training — every image, every batch. You
are checking two things: no identifiable faces survived, and the pair is
actually the same patient/pose.

## Training on RunPod

1. Create a pod: 1× A100 80GB, the official PyTorch template, attach a volume.
2. `git clone https://github.com/ostris/ai-toolkit && cd ai-toolkit && pip install -r requirements.txt`
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
Gemini fetch for your endpoint. The request/response contract of
`/api/generate` (base64 in, base64 out) does not need to change, and demo
mode still works for local dev.
