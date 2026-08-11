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
- Gallery intake: `training/scripts/scrape_gallery.py` (pluggable `CLINICS` configs; 23 clinics as of the 2026-08 batch - see the module docstring and per-parser docstrings for each gallery's markup contract). View laterality / unlabeled views come from a visual-annotation JSON (see the scraper docstring); `training/scripts/annotate_contact_sheets.py` renders the review sheets. Undocumented spec fields are omitted or the schema's `unknown` enum - never invented.
- sanantonio (BRAG book plugin) sharp edges: images are side-by-side before|after composites split at the horizontal midpoint (`split_composite_image`); the WordPress URL slug (not the BRAG `data-case-id`) is the unique case key; Height/Weight have no documented units (kept verbatim in notes); Natrelle Inspira model numbers (e.g. 'SRM-445') encode cc but are NOT decoded, per the drkolker 'Mini Motiva' precedent - a decoder is a captain-level decision.
- Gallery platform families (2026-08 batch of 20 more consented clinics; one parser per family, reused across clinics on the same builder): Influx legacy numbered-subpage (drkolker, lakeshore/marina via the shared `influx_swiper` kind, austinweston, charlotte, allure), Etna Interactive composite (drrohrich's 2x2 grid, wny's per-view 2-up), Webflow (sixsurgery structured fields, skplastic's 2x3 grid), Studio 3 Marketing/DatoCMS paginated (drgrover, basu, drteitelbaum - strip the `?w=` query string for the bare original), and one bespoke WordPress parser per remaining clinic (harrington, drjeremyhunt, drmiroshnik, privateclinic, mitchellbrown, heavenly, mya, drtavakoli). `crop_grid_cell()` handles multi-panel grid composites (front/oblique/side x before/after in one file) via `ImagePair.grid_shape`/`before_cell`/`after_cell`; `resolve_view()` also accepts a `view_hint` that is already a full schema view (e.g. heavenly's older filenames spell out 'Left-Oblique').
- Visual annotation is genuinely expensive at this corpus's scale (3000+ pairs across the 2026-08 batch have no page-documented view): treat it as a distinct, bounded pass per clinic via `annotate_contact_sheets.py`, not something to rush or guess through. Laterality (left/right) specifically requires a real distinguishing feature per case (piercing, tattoo, asymmetry) visible in both the reference and target images - if none exists, leave the pair unannotated rather than guess; a wrong guess is worse than a missing pair in medical training data.
- mitchellbrown's gallery page lives on `drmitchellbrown.com` but canonicalizes (redirect) to `torontoplasticsurgery.com`, which also hosts all its images; `ClinicConfig.base_url` points at the canonical host directly. That host had one multi-hour outage window during the 2026-08 scrape (TCP connection refused, recovered on its own) - unrelated to the scraper.
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
