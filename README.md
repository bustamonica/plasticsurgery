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

**Algorithm** (per breast): guardrail check → region selection in a local frame at
the nipple → normal-direction displacement field (apex = target projection −
existing, radial cosine falloff, placement-specific upper-pole modulation,
teardrop weighting for anatomical shapes) → in-plane base-width scaling →
volume-closure iteration until added volume matches implant volume ±2 cc.

## Validation gates (pytest)

- Achieved volume within ±2 cc of target per side
- Projection and base width within ±5% of target
- Submuscular upper-pole slope < subglandular (placement realism)
- Anatomical shapes skew inferior (teardrop)
- Determinism, symmetry, guardrail semantics

## Status & disclaimers

- Implant dimensions in `implants/data/implants.json` are **illustrative
  placeholders** (`values_status`) pending data entry from manufacturer dimension
  tables. Do not ship user-facing size claims until records are `verified`.
- v1 scope: frontal morphology, symmetric placement, average tissue model.
  Ptosis/asymmetry/tissue-thickness modifiers are v2.
- Units: cm / cc. Coordinates: +x patient-left, +y up, +z anterior.
