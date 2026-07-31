# Research: photo→3D fitting & real before/after data (Track D, 2026-07-29)

> Decision-grade summary of the Track D research report. Unverified items at end.

## Q1 — Photo → body fitting: license map (the critical finding)

| Component | License | Commercial? |
|---|---|---|
| `anny` pip package (body model, incl. `ParametersRegressor` mesh-fit) | Apache-2.0 (assets CC0) | ✅ |
| Anny "smplx" topology zip | Non-commercial | ❌ |
| Anny-Fit (photo→Anny pipeline, CVPR'26) | NAVER Non-Commercial | ❌ |
| Multi-HMR / Multi-HMR 2 | NAVER Non-Commercial | ❌ |
| SAM 3D Body (Meta, SOTA single-image body recovery) | SAM License (commercial OK per Roboflow; needs legal read) — **HF download still gated w/ contact form** | ✅ (verify) |
| MHR (Meta Human Registry — SAM 3D Body's output model) | Apache-2.0 | ✅ |
| All SMPL/SMPL-X-output methods (TokenHMR, SMPLer-X, OSX, PyMAF-X, WHAM, 4D-Humans) | code often MIT/Apache but SMPL model license = academic-only; Epic acquired Meshcapade (commercial SMPL terms uncertain) | ❌ |

**Recommended v0 fitting pipeline:** SAM 3D Body (photo→MHR mesh) → own
MHR→Anny converter (vertex correspondence + `anny.ParametersRegressor`,
Apache-2.0, in-package) → Anny body → our morph engine.
Industry precedent: clad.you runs exactly this bridge in production (~$0.09/scan).
Fallback: ViTPose (Apache-2.0) 2D keypoints → optimize Anny params directly
(Anny is differentiable; a simplified self-written Anny-Fit).

**Do NOT ship:** Anny-Fit, Multi-HMR(2), CameraHMR, TokenHMR, SMPLer-X,
PyMAF-X (all non-commercial); anything needing SMPL model files.

## Q2 — Real before/after pairs for painter fine-tune

- **No public training-licensed paired pre/post breast dataset exists**
  (closest: BC 3,762 post-op-only; CINDERELLA private; SurFace1259 faces-only,
  not releasable).
- **Scraping clinic galleries = legal gray zone everywhere** (EU TDM opt-outs,
  UK non-commercial only, US fair use unsettled + right-of-publicity + health-data
  privacy). Not for training; at most a small documented internal eval set (<50).
- **Knoedler et al. 2024**: GAN trained on ~3k real pairs produced simulations
  indistinguishable from real post-ops (52.5% ≈ chance) → ~1–3k pairs is enough
  at our domain's scale.
- **v0 data strategy (adopted):**
  1. Train v0 exclusively on self-owned synthetic→pseudo-real pairs
     (our Anny-morphed geometry → SDXL img2img, IP-Adapter conditioning,
     Apache-2.0 stack only). License-clean by construction; medical
     self-augmentation literature supports it.
  2. Clinic partnership track: DUA + explicit patient training consent,
     target 1–3k standardized pairs — also doubles as clinical validation.
  3. Provenance logging; honor robots.txt/ToS opt-outs; no identifiable-face
     reuse without consent; legal read of SAM License before hosting weights.
  4. InstantID: code Apache-2.0 but face checkpoints non-commercial — keep
     insightface-dependent pieces out of the commercial path.

## Unverified (check before relying)

- Whether SMPL→Anny vertex-regressor weights ship in the Apache `anny` package
  or only in the NC `noncommercial.zip` (if NC-only: train our own
  correspondence on Anny self-generated pairs — same topology, easy).
- Anny-One dataset license (780k images, JS-gated page).
- SAM License exact hosting/redistribution wording.
- BC dataset download location/terms.
