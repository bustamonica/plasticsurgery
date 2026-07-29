# morphengine (M0)

Implant-parameterized chest morph engine — computes target chest geometry on a 3D
body mesh from a real breast implant's published dimensions. Part of a
breast-augmentation visualization platform (see design doc).

**Body-model agnostic:** operates on any `trimesh.Trimesh` + `ChestLandmarks`.
Ships with a procedural synthetic torso fixture so everything runs with zero
external/licensed assets. Real body models (Anny/MHR/SAM 3D Body) integrate later
by implementing `LandmarkProvider`.

## Install & test

```bash
pip install -e .[dev]
python -m pytest -q
```

## Demo

```bash
python -m morphengine.cli --list                                   # browse implant SKUs
python -m morphengine.cli --sku mentor-memorygel-350-hp \
    --placement submuscular --export-obj /tmp/morph                # morph + export OBJs
```

## Architecture

| Module | Role |
|---|---|
| `geometry.landmarks` | `ChestLandmarks` dataclass + `LandmarkProvider` ABC (plug-in point for body models) |
| `geometry.fixtures` | `synthetic_torso()` watertight procedural mesh + analytic landmarks |
| `geometry.measure` | projection / base width / displaced volume / upper-pole slope |
| `morph.deformation` | region selection, radial + placement + anatomical falloffs |
| `morph.guardrails` | implant↔chest compatibility checks and clamping |
| `morph.engine` | `MorphEngine.morph()` — delta morph with volume closure (bisection) |
| `implants.db` | `ImplantDB` — SKU store → `ImplantParams` |

**Algorithm** (per breast, SPEC rev.5): guardrail check → landmark-anchored local
frame → in-plane base-width scaling → dome field `h·(1−r²)^β` (per-implant
fullness β from the rated volume/width/projection triple) along local surface
normals, with volume-neutral placement and anatomical multipliers → slide-share
guardrail → volume closure (uniform multiplier, bisection, ±2 cc / ±1.5%).

**Semantics:** volume and base width are hard constraints; measured projection
is the volume-consistent output (rated projection ≠ in-vivo projection gain).

## Implant catalog (581 SKUs, verified)

Real manufacturer dimension tables — transcribed programmatically by
`scripts/build_implants_db.py` from vendored official sources
(`implants/data/sources/`), never hand-edited:

- **Mentor** MemoryGel (4 profiles) + MemoryShape anatomicals — official
  catalog PN 020827-181217, exact match vs jnjmedtech.com
- **Natrelle** Inspira (5 profiles, dims gel-independent) — official Allergan
  US catalog, cross-checked vs 2015 sales tool + UK 2016 catalog
- **Motiva** Ergonomix + Ergonomix2 (4 profiles each) + TrueFixation
  anatomicals — official Implant Matrix, cross-checked vs FDA PMA P230005
- **Sientra** OPUS Luxe (5 profiles) + OPUS Curve anatomicals — official
  MDC-0343/MDC-0270/MDC-0400 documents

Exclusions documented in SPEC §2.9: Natrelle 410 (US withdrawal 2019),
Motiva Round (identical to Ergonomix), delisted/uncertain sizes.

## Synthetic data factory (M1)

`morphengine.datafactory` turns the engine into painter-training quadruplets:
**BodySampler** (diverse watertight fixture torsos) → morph with weighted
real-SKU sampling → **SoftwareRenderer** (pure-numpy z-buffer: shaded RGB +
depth + smooth normal maps + mask, deterministic, ~0.03 s/img) →
`manifest.jsonl` with full implant/engine metadata. Guardrail-gated: clamped
or mismatch pairs are skipped and resampled, never emitted.

```bash
python3 scripts/generate_dataset.py --n 20000 --seed 0 --size 256 --out dataset/  # --resolution 6 for production
python3 scripts/contact_sheet.py --manifest dataset/manifest.jsonl --out sheet.png
```

## Painter v0 (M1)

`morphengine.painter`: **PairDataset** (before RGB in [-1,1] + 6-channel
geometry cond [depth_before, depth_after, normal_after xyz, mask] + prompt) →
**train()** with a pure-torch tiny-UNet smoke mode (CPU, ~1M params) and an
SDXL+LoRA GPU path (`configs/painter_v0.yaml`). Setup, scaling, and launch
instructions: `src/morphengine/painter/README.md`. Install:
`pip install -e ".[painter]"`.

## Validation gates (pytest, 92 tests)

- Achieved added volume within ±2 cc / ±1.5% of rated, per side
- Base width within ±5% of rated
- Projection gain within sanity bounds (tracks volume)
- Submuscular upper pole emptier than subglandular (placement realism)
- Anatomical shapes skew inferior (teardrop)
- Determinism, symmetry, guardrail clamp/warn semantics

## Known limitations (v0)

- Apex retention (projection gain / rated projection) still falls with
  footprint width — ~1.0 at 230 cc down to ~0.47 at 550 cc on the fixture —
  though the rev.8 skirted dome improved it across the board (was ~0.94/0.46).
- Fixture-based validation only; real-body landmark providers (Anny/MHR)
  are the next milestone.
- Smooth-shell dimensions cataloged; textured variants differ ≤0.2 cm
  (documented in sources/, not duplicated in the DB).

## Status & disclaimers

- Implant dimensions are **verified against official manufacturer documents**
  (see per-record `source`); catalog footnote: individual units may vary
  slightly from printed dimensions. Motiva TrueFixation is not FDA-approved
  (international market); flagged per-record.
- v1 scope: frontal morphology, symmetric placement, average tissue model.
  Ptosis/asymmetry/tissue-thickness modifiers are v2.
- Factory renders are stylized (ellipsoid torso fixture): correct for
  geometry bootstrap; real-body diversity (Anny/MHR) + photorealism arrive
  with the painter's real-pair fine-tune (M3). The dome-rim ring crease was
  fixed in rev.8 (skirted dome with C1 rim); use resolution 6 bodies for
  production data.
- Units: cm / cc. Coordinates: +x patient-left, +y up, +z anterior.
