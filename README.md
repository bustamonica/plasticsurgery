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

**Algorithm** (per breast, SPEC rev.4): guardrail check → landmark-anchored local
frame → in-plane base-width scaling → dome field `h·(1−r²)^β` (per-implant
fullness β from the rated volume/width/projection triple) along local surface
normals, with volume-neutral placement and anatomical multipliers → slide-share
guardrail → volume closure (uniform multiplier, bisection, ±2 cc / ±1.5%).

**Semantics:** volume and base width are hard constraints; measured projection
is the volume-consistent output (rated projection ≠ in-vivo projection gain).

## Validation gates (pytest, 55 tests)

- Achieved added volume within ±2 cc / ±1.5% of rated, per side
- Base width within ±5% of rated
- Projection gain within sanity bounds (tracks volume)
- Submuscular upper pole emptier than subglandular (placement realism)
- Anatomical shapes skew inferior (teardrop)
- Determinism, symmetry, guardrail clamp/warn semantics

## Known limitations (v0)

- Wide-footprint domes spread volume into the skirt, so projection gain can
  dip slightly for very large implants (v1: skirted-dome profile).
- Fixture-based validation only; real-body landmark providers (Anny/MHR)
  are the next milestone.
- Implant dimensions are illustrative placeholders pending manufacturer data.

## Status & disclaimers

- Implant dimensions in `implants/data/implants.json` are **illustrative
  placeholders** (`values_status`) pending data entry from manufacturer dimension
  tables. Do not ship user-facing size claims until records are `verified`.
- v1 scope: frontal morphology, symmetric placement, average tissue model.
  Ptosis/asymmetry/tissue-thickness modifiers are v2.
- Units: cm / cc. Coordinates: +x patient-left, +y up, +z anterior.
