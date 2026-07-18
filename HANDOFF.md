# Project Handoff — AI Breast Augmentation Preview ("Aurelle")

_Last updated: July 2026. Branch: `claude/breast-augmentation-ai-preview-56w494` (not yet merged to `main`)._

## What this project is

A consumer website for people considering breast augmentation. Users upload a
photo, choose implant **brand** (Mentor, Natrelle, Motiva, Sientra),
**shape** (round/teardrop), **profile**, and **volume** (150–800 cc), and an
AI generates a realistic before/after preview with a comparison slider.

Two tracks exist in this repo:

1. **The website** — complete and working, currently backed by Google's
   Gemini image model (`app/api/generate/route.ts`).
2. **The custom-model track** (`training/`) — tooling to eventually replace
   Gemini with our own model, fine-tuned on consented clinic before/after
   photos. The pipeline is built; no real data has been processed yet.

## State of the website: DONE and verified

- Pages: landing (`/`), preview studio (`/studio`), implant guide (`/learn`),
  privacy & disclaimers (`/privacy`).
- Stack: Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS 4.
- `/api/generate`: validates input, rate-limits per IP, enforces body-size
  caps, pins the AI output aspect ratio to the uploaded photo, and falls back
  to a **demo mode** (echoes the original photo) when `GEMINI_API_KEY` is
  unset — so the whole flow is testable without a key.
- Privacy posture: photos processed in memory only, never stored server-side;
  consent checkbox (18+, own photo, "illustration not prediction") gates
  generation; medical disclaimers on every page.
- Quality: production build passes; the flow was driven end-to-end in a real
  browser (desktop + mobile); a 29-agent adversarial review produced 20
  confirmed findings (race conditions, aspect-ratio misalignment, WCAG
  contrast, screen-reader gaps, medical-copy inconsistencies) — all fixed and
  re-verified with regression tests.

Run it: `npm install && npm run dev` → http://localhost:3000. Real AI
previews: put a key in `.env.local` (`GEMINI_API_KEY=...`, free at
https://aistudio.google.com/apikey). See `README.md`.

## State of the training track: TOOLING DONE, awaiting real data

Decisions made (rationale in `training/README.md`):

- Base model **Qwen-Image-Edit** (Apache 2.0, commercially clean), **LoRA**
  fine-tuning via **ostris/ai-toolkit**, on rented GPUs (**RunPod/Lambda**,
  A100 80GB) — hosted trainers' ToS prohibit clinical nudity, so
  self-managed GPU rental is the path.

Built and tested on synthetic data:

- `training/scripts/ingest.py` — validates clinic deliveries; **rejects any
  pair without a `consent_ref`**; strips EXIF/GPS; dedupes; size checks.
- `training/scripts/deidentify.py` — face detection + irreversible
  pixelation (or top-crop); rejects no-face images unless explicitly
  overridden; mandatory visual audit after.
- `training/scripts/build_dataset.py` — builds instruction captions from the
  implant metadata using the same wording the site sends at inference time
  (`lib/prompt.ts`); train/val split; ai-toolkit layout + `manifest.jsonl`.
- `training/configs/qwen_edit_lora.yaml` — starting training config.
- `training/dataset_schema.json` — the per-pair metadata contract to send to
  clinics.

Governance already enforced by tooling: photo directories are gitignored
(nothing sensitive can be committed), consent references are mandatory,
de-identification is default-on.

## What's left (in rough order)

| # | Task | Needs |
| --- | --- | --- |
| 1 | Add `GEMINI_API_KEY` and test real generations with real photos; tune `lib/prompt.ts` if needed | Owner: get key (5 min) |
| 2 | Merge this branch to `main`, deploy (Vercel is the intended path), add the key as an env var, custom domain | Owner decision + ~30 min |
| 3 | Replace placeholder branding ("Aurelle" in `lib/site.ts`), logo, colors | Owner |
| 4 | Legal review: privacy page, disclaimers, marketing claims; **confirm clinic agreements cover patient consent for AI training** | Attorney |
| 5 | Collect photo pairs from partner clinics per `training/dataset_schema.json` (targets: 500 pairs = experiments, 1,000–5,000 = production; prioritize labeled implant specs and any bra/sports-bra sets) | Owner + clinics |
| 6 | Run the pipeline on the first real batch; visually audit de-identified output | Dev, ~1 day |
| 7 | First LoRA run on RunPod; evaluate on val split (identity preservation, cc-adherence, realism) | Dev + ~$100 GPU |
| 8 | Deploy trained model behind an HTTP endpoint; swap provider in `app/api/generate/route.ts` (contract stays the same) | Dev |
| 9 | Production hardening as traffic grows: shared rate-limit store (current one is per-instance in-memory), monitoring, analytics | Dev |
| 10 | Known gap: clinical training photos are nude; site users upload clothed photos. Mitigations: collect bra-set photos, synthetic clothed augmentation, and keep Gemini as fallback until the custom model wins on real user-style input | Dev/ML |

## Key facts for whoever picks this up

- The AI provider is deliberately swappable: `app/api/generate/route.ts` +
  `lib/prompt.ts` are the only files that know Gemini exists.
- The implant catalog and all option copy live in `lib/implants.ts` — edit
  there, and the configurator, landing page, guide, and AI prompts all update.
- Never commit photos. Never train on photos lacking documented clinic *and*
  patient consent. De-identify before anything leaves the intake machine.
- All AI outputs are presented as illustrations, not medical predictions —
  keep it that way in any copy you add; it's a legal posture, not decoration.
