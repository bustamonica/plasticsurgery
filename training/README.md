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
  training folder or leaves your machine. It strips EXIF/GPS by re-encoding
  every image it writes — `ingest.py` does the same on the way in, and that
  redundancy is deliberate. It does **not** detect or blur faces: the consented
  clinics guarantee that no faces appear in what they publish, and the captain
  holds that assurance (ruling 2026-08-14). The model only needs the chest
  region. `emit_corpus.py` is not a way around this: it re-encodes nothing, so
  it verifies instead and refuses to carry an image that still holds EXIF.
- Metadata stripping is not theoretical. A scan of all 5656 corpus images found
  36 carrying EXIF, including `harrington-177-front`, whose images carry the
  camera make and model and 2020 capture timestamps.
- Keep the raw originals on an encrypted drive; treat them as medical records.
- Censored and annotated photos are rejected, not repaired (`censorship.py`).
  A censored pair is worse than a missing one - the v1 LoRA learned to reproduce
  a clinic's blur bands. A clinic watermark clear of breast tissue is kept
  unmasked *only* when it appears on both halves of the pair: a mark burned into
  one half alone correlates perfectly with the before/after label and is cropped
  out of both halves at scrape time (`ClinicConfig.bottom_crop_px` in
  `scripts/scrape_gallery.py`). Which clinic carries what, whether it touches
  breast tissue, and the verdict on each is in `clinic_watermarks.md`.
- Pairs are **retired, not deleted**. `retired_pairs.json` enumerates every pair
  id withheld from the finished corpus with the ruling and the reason;
  `emit_corpus.py` reads it and copies each withheld pair into the quarantine
  tree so its images and consent metadata survive. Nothing under `raw/` or
  `staging/` is ever removed.

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
        │
        ├──▶ scripts/emit_corpus.py  ──▶  <corpus>/<clinic>/<pair-id>/
        │      carries ONE CLINIC's staged pairs through to the FINISHED corpus
        │      tree (staging is flat and mixes clinics, so --clinic selects
        │      which pairs are carried as well as where they go), applying the
        │      emit gates and the retirements in retired_pairs.json. A pair
        │      that never reaches that tree is invisible to every corpus walk
        │      and dataset build, however much material sits in staging. Copies
        │      bytes; re-encodes nothing. This is a TERMINAL branch off staging
        │      - the corpus tree is the durable record of what counts, and
        │      nothing below reads from it.
        │
        ▼
2. scripts/deidentify.py
   re-encodes every image, stripping EXIF/GPS (optionally --crop-top)
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

# Carry one clinic's staged pairs into the finished corpus - staging is flat and
# holds every clinic in data/raw, so run this once per clinic. --quarantine both
# skips pairs already withheld and records the ones this run retires; --report
# writes one row per staged pair, emitted or not.
python scripts/emit_corpus.py data/staging ~/firstmate/data/clinic-corpus \
    --clinic drkolker \
    --quarantine ~/firstmate/data/ba-viz-emit-backlog/quarantine \
    --report emit-drkolker.csv --dry-run

python scripts/deidentify.py   data/staging data/clean
python scripts/build_dataset.py data/clean data/dataset --val-fraction 0.1
```

Always `--dry-run` first and read the per-disposition counts; the dry run
consults the destinations, so it predicts what the real run will do.
`emit_corpus.py` never overwrites an existing corpus pair - a clash is an error,
not a merge: the pair is recorded as `emit-failed` in the report and the run
exits non-zero, so a partial emit is always auditable from the CSV it leaves
behind. A destination that is byte-identical to the staged pair is not a clash;
it reads as `already-emitted`, which is the normal shape of a second run over a
clinic, and is not rewritten either. A pair lands whole or not at all, and a row
reads `emit` only once it is complete in the finished tree; a row still reading
`pending` was decided but never written, so an interrupted run under-states what
landed rather than over-stating it.

`--clinic` selects which staged pairs are carried as well as where they go: a
staged pair whose id belongs to another clinic is reported as `other-clinic`,
counted in the summary and left for that clinic's own run, never written under
this one. A `--clinic` that matches no staged pair id at all is refused outright,
rather than opening a clinic subdirectory nobody meant to create.

Audit `data/clean` visually before training — every image, every batch, to
confirm the pair is actually the same patient in the same pose and that the
before/after direction is right.

## Synthetic smoke-test corpus

Before any real consented data exists, generate a schema-valid synthetic corpus
to exercise the whole pipeline (and later the GPU smoke run):

```bash
python scripts/generate_synthetic.py data/raw --count 100 --seed 42
```

The "after" images are programmatic warps of the "before" images, sized by
`volume_cc`, with balanced `clothing` coverage and unique pixels per pair (so
the ingest dedup check is exercised too).
**Never mix these pairs into a real training corpus** - they teach the model
nothing medically meaningful; they only prove the tooling works end to end.

## Tests

```bash
pip install -r requirements.txt   # includes pytest, pyyaml, jsonschema
python -m pytest tests -q
```

Covers ingest validation/rejection paths, `dataset_schema.json` (including the
guarantee that the optional chart/frame fields never reach a caption), caption
assembly with its `clothing` variants, the censorship detector, the emit stage
and its retirements (`retired_pairs.json` is asserted directly - count, shape
and named exceptions - and again through the only code that can put a pair in
the corpus), the gallery parsers against pinned per-clinic fixtures (including
the Etna case-list endpoint, whose access gate is asserted from both sides: it
refuses a clinic holding no grant, and the seven already-enumerated clinics hold
none), and a drift guard on `configs/qwen_edit_lora.yaml`. The detector's
tests draw their own torsos - no patient imagery is ever committed. Six tests
reference the real corpus and its quarantine tree by path and skip when those
are not mounted: two in the detector's suite, and four asserting that heavenly's
retirement is real on disk and not merely registered (a registry entry gates the
emit path only - see `clinic_watermarks.md`). Two of those four cover the
leftover duplicate-ingest dumps at the corpus root, which the first retirement
pass did not reach.

## Training on RunPod

**To run one by hand, follow [`MANUAL-RUN.md`](MANUAL-RUN.md)** - exact commands,
measured costs, and the traps, written so a person gets a checkpoint on local disk
without any driver code. The notes below are the why behind it.

### Every local guard sleeps when the laptop does

A run lost an entire balance this way: the Mac entered Idle Sleep 29 seconds after
the last poll and stayed asleep ~7h while the pod billed at $3.32/h. Neither the
poll loop nor the watchdog thread was scheduled, so nothing fired and nothing was
logged - the run simply had a seven-hour hole in it. `pmset -g log` confirmed 450
minutes of sleep across a 420-minute window.

So: run under `caffeinate -i`, and set `--terminate-after` to the run estimate plus
an hour rather than a nominal 24h. **The provider-side deadline is the only guard
that survives a sleeping laptop**, which also means an earlier "the subprocess
timeout never fired" diagnosis was wrong - the process was suspended, not hung.

1. Create a pod: 1× 80GB card, the official PyTorch template, `--ports 22/tcp`
   (without the port flag SSH is never mapped and the pod bills unreachable).
2. `git clone https://github.com/ostris/ai-toolkit && cd ai-toolkit && git checkout 6d8afa5684000b69db97cc40504a972a85615e3b && pip install -r requirements.txt`
   (the pinned commit our config was validated against; if you take a newer one, re-diff `config/examples/train_lora_qwen_image_edit_2509_32gb.yaml` first)
3. Upload `data/dataset/` — **as one archive, not as a directory** (see below).
4. Copy `configs/qwen_edit_lora.yaml` into `ai-toolkit/config/`, adjust paths,
   and sync its keys with the current example config in the ai-toolkit repo
   (`config/examples/`) — the toolkit evolves quickly.
5. `python run.py config/qwen_edit_lora.yaml`
6. Checkpoints + sample grids land in `output/`. Evaluate on the val split:
   the samples are generated from val control images with val captions —
   compare against the real "after" photos.

### Measured facts, 2026-08-16/17 (H100, 1902 pairs)

These were paid for; do not re-derive them.

| | Measured |
| --- | --- |
| bf16 (`quantize: false`), H100 PCIe | 16.5 s/optimizer-step |
| bf16, H100 80GB HBM3 (SXM) | 10.5–10.9 s/step |
| Committed uint3 32GB recipe, H100 PCIe | 24.0 s/step (45% slower) |
| Billed rate, secure cloud + 120GB disk | H100 PCIe/SXM ≈ $2.9–3.3/h |

- **Price per hour is the wrong metric; cost per step is nearly identical across
  every 80GB card** (H100 SXM $0.0152, A100 SXM4 $0.0149, A100 PCIe $0.0142 at
  the rates above). So pick on wall-clock exposure, not sticker price: the H100
  finishes in half the time, halving the window for a host fault or reclaim.
- The catalogue `lowestPrice` GraphQL field quotes a **cheaper tier than
  `--cloud-type SECURE` actually bills**. Read the pod's own `costPerHr` after
  creation before trusting any budget arithmetic.
- Ubuntu 24.04 images have a PEP 668 externally-managed Python: a bare
  `pip install` refuses. Use `python3 -m venv --system-site-packages`, which
  both satisfies PEP 668 and inherits the image's CUDA-matched torch.
- Quantization is not needed on an 80GB card. The committed config carries
  ai-toolkit's 32GB recipe (uint3 + accuracy-recovery adapter + `low_vram`) for
  portability; `quantize: false` is ~45% faster and fits 80GB with room.

### Never `scp -r` the dataset

`scp -r` opens a transfer per file, so the dataset's **file count** is the
constraint, not its size. Measured on the same 366MB / 5707-file dataset:
5.4 min, 21.7 min, then a `connection reset` failure after **134.8 min** — which
is 57, 228 and 1417 ms per file across three hosts. Link bandwidth to these pods
is only ~0.9 MB/s, so one stream takes ~7 min; the rest was pure per-file
overhead. Tar it and send verified chunks with resume: the same payload then
landed in 7.1 min with an MD5 match.

### A checkpoint that cannot leave the pod is not an artifact

**Pull every checkpoint the moment it is written, and treat a failed pull as
fatal.** On 2026-08-17 a 1600-step run completed, wrote 8 checkpoints, and
delivered nothing: the harvest step used plain `scp -r` on the 6GB output
directory, it died with `connection reset by peer`, the failure was caught and
logged as `harvest failed`, and training carried on for three more hours to a
machine whose only exit route was already known to be broken. The pod was then
reclaimed when the balance hit zero and took every checkpoint with it. Cost:
the whole $58 balance, no model.

Two rules follow, and the first is the one that was actually violated:

- The same per-file fragility applies in BOTH directions. The upload had
  already been fixed with chunk-and-verify; the download had not, though the
  measurement that justified the fix applied equally to it.
- A failed egress is not a warning. If a checkpoint cannot be retrieved, the
  run has no product, so it must abort at that point rather than continue
  billing. `pull_checkpoints.py` in the run's work directory does the chunked,
  MD5-verified, resumable pull and also checks the safetensors header parses -
  a file that arrived is not the same as a file that loads.

### Guard the run, and watch the guard fire

Three separate guards on this project have reported "armed" and then done
nothing: `runpodctl pod terminate` (not a real subcommand — it exits 0 without
terminating; use `pod delete` plus a `podTerminate` mutation and re-verify
through the API), a `subprocess` timeout that never triggered, and an idle
watchdog that let a 2h15m upload run under a 45-minute rule.

That last one is the instructive failure: it probed disk-used for "progress",
and files *were* landing, so the idle clock reset at every poll. **An idle
detector cannot see a phase progressing far too slowly to finish.** Give every
phase a hard wall-clock budget as well, run both checks on a thread that never
touches the phase it watches (a main thread blocked in a syscall cannot report
its own stall), and test that each one fires before relying on it.

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
