# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Repo layout and branches

- The GitHub default branch is `claude/breast-augmentation-ai-preview-56w494` (the Next.js app + training track).
  `origin/main` is an unrelated legacy Python project; never base work on it or push to it.
- `app/`, `components/`, `lib/` are a Next.js preview-studio app; `training/` is the custom-model training track (data pipeline + ai-toolkit config). See `HANDOFF.md` and `training/README.md`.

## Training track sharp edges

- Authoritative docs: `training/README.md` (pipeline, RunPod steps) and `training/dataset_schema.json` (consent contract per pair).
- Never commit photos or datasets: `training/data/` is gitignored on purpose; the consented clinic corpus lives outside the repo at `~/firstmate/data/clinic-corpus/<clinic>/raw/` (with `.scraper-cache/` beside it).
- Gallery intake: `training/scripts/scrape_gallery.py` (pluggable `CLINICS` configs; currently drkolker + drdanielbarrett-implants-only per captain scope). View laterality / unlabeled views come from a visual-annotation JSON (see the scraper docstring); `training/scripts/annotate_contact_sheets.py` renders the review sheets. Undocumented spec fields are omitted or the schema's `unknown` enum - never invented.
- `ingest.py` `MIN_DIMENSION` is 400 (not 512) by captain ruling: drkolker publishes ~75% of cases at 418px; those pairs need ~2.5x upscale to 1024px training res.
- Tests: `cd training && python -m pytest tests -q` (deps in `training/requirements.txt`).
- `training/configs/qwen_edit_lora.yaml` is validated against ai-toolkit commit `6d8afa5684000b69db97cc40504a972a85615e3b`; the toolkit renames keys often, so re-diff its example config before using a newer commit.
- Caption parity is a hard contract: `build_caption()` in `training/scripts/build_dataset.py` and `buildCustomModelPrompt()` in `lib/prompt.ts` must emit byte-identical instruction text. Update both (and their shared vocabulary in `lib/implants.ts`) together.
- The website's AI provider seam is `AI_PROVIDER` in `.env.example`; `gemini` is the default and the Gemini prompt in `lib/prompt.ts` (`buildEditPrompt`) is intentionally different from the training-caption format.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
