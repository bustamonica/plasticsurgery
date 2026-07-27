# SPEC.md — morphengine (M0)

Implant-parameterized chest morph engine. Single source of truth for architecture,
interfaces, and data formats. Implement faithfully; no unilateral interface changes.

## 0. Context

This library computes the target chest geometry of a 3D body mesh given a real
breast-implant's published dimensions. It is **body-model-agnostic**: it operates
on any `trimesh.Trimesh` + a `ChestLandmarks` object. Body-model integration
(Anny/MHR/SMPL) happens later by writing new `LandmarkProvider`s — this package
must not depend on any licensed body model.

**Conventions**
- Units: **centimeters** everywhere (meshes, landmarks, implant dims). Volumes in **cc** (1 cc = 1 cm³).
- Coordinates: `x` = mediolateral (patient's left is +x), `y` = superoinferior (up is +y), `z` = anteroposterior (anterior/out of chest is +z).
- Python ≥3.10, pydantic v2, numpy, scipy, trimesh. Package uses `src/` layout; import name `morphengine`.
- Everything must run with **zero external assets** (synthetic fixtures only).

## 1. Package layout

```
src/morphengine/
├── geometry/
│   ├── landmarks.py     # ChestLandmarks, LandmarkProvider ABC      [agent: core]
│   ├── fixtures.py      # synthetic_torso(), FixtureLandmarkProvider [agent: core]
│   └── measure.py       # measurement functions                     [agent: core]
├── morph/
│   ├── deformation.py   # falloffs, region selection, placement     [agent: core]
│   ├── guardrails.py    # compatibility checks                      [agent: core]
│   └── engine.py        # MorphEngine                               [agent: core]
├── implants/
│   ├── schema.py        # pydantic models                           [agent: db]
│   ├── db.py            # ImplantDB                                  [agent: db]
│   └── data/implants.json                                          [agent: db]
└── cli.py               # demo entry point                           [MAIN AGENT — do not touch]
tests/                   # each agent writes tests for own modules only
```

## 2. Interface contracts (sacred)

### 2.1 geometry.landmarks  [core]

```python
@dataclass(frozen=True)
class ChestLandmarks:
    nipple_left: np.ndarray        # (3,)
    nipple_right: np.ndarray
    imf_left: np.ndarray           # inframammary fold, below nipple
    imf_right: np.ndarray
    lateral_left: np.ndarray       # lateral breast boundary at nipple height
    lateral_right: np.ndarray
    medial_left: np.ndarray        # medial boundary (toward sternum)
    medial_right: np.ndarray
    clavicle_mid: np.ndarray       # superior reference
    sternum_mid: np.ndarray
    chest_width_cm: float          # transverse chest width at breast level
    def breast_half(self, side: Literal["left","right"]) -> "BreastSide": ...

@dataclass(frozen=True)
class BreastSide:
    """Convenience view for one breast: nipple, imf, lateral, medial."""
    nipple: np.ndarray; imf: np.ndarray
    lateral: np.ndarray; medial: np.ndarray

class LandmarkProvider(ABC):
    @abstractmethod
    def locate(self, mesh: trimesh.Trimesh) -> ChestLandmarks: ...
```

### 2.2 geometry.fixtures  [core]

```python
def synthetic_torso(
    chest_width_cm: float = 34.0,
    torso_depth_cm: float = 20.0,
    height_cm: float = 50.0,
    breast_radius_cm: float = 6.0,
    breast_projection_cm: float = 3.0,
    resolution: int = 96,
) -> trimesh.Trimesh:
    """Procedural WATERTIGHT torso: ellipsoid body + two hemispherical breast
    mounds on the anterior surface. Deterministic given parameters."""

class FixtureLandmarkProvider(LandmarkProvider):
    """Returns landmarks computed from the fixture's construction parameters.
    locate() must reconstruct the exact analytic landmarks of synthetic_torso()
    (it may read mesh.metadata['fixture_params'] set by synthetic_torso)."""
```

Fixture landmark anatomy (provider computes; engine/tests rely on it):
- nipples at breast-mound apexes (max +z), IMF directly below nipple at mound base,
  lateral/medial at mound base edges at nipple height, `chest_width_cm` = constructor arg.
- Store constructor kwargs in `mesh.metadata["fixture_params"]`.

### 2.3 geometry.measure  [core]

```python
def chest_wall_plane(lm: ChestLandmarks, side: str) -> tuple[np.ndarray, np.ndarray]:
    """(point, normal) of the chest-wall plane behind one breast: fit through
    imf/lateral/medial of that side, normal pointing anterior (+z-ish)."""
def measure_projection_cm(mesh, lm, side) -> float:
    """Max distance from chest-wall plane to mesh surface within the breast
    region (see §3.1 for region definition)."""
def measure_base_width_cm(mesh, lm, side) -> float:
    """Extent of the breast region along the mediolateral axis at nipple height
    (widest slice within ±1 cm of nipple y)."""
def displaced_volume_cc(mesh_before, mesh_after, lm, side) -> float:
    """Volume added on one side. If both meshes watertight: split by x-midplane
    sign convention and diff submesh volumes; else surface integral of normal
    displacement over the region (first-order). Must agree within 10% on the
    watertight fixture."""
def upper_pole_slope(mesh, lm, side) -> float:
    """Mean |dz/dy| of the surface over the superior half of the breast region
    (between nipple and superior boundary). Used to compare placements."""
```

### 2.4 morph.deformation  [core]

```python
def breast_region(mesh, lm, side, margin: float = 1.15) -> np.ndarray: ...
def radial_falloff(r_norm: np.ndarray, kind: str = "cosine") -> np.ndarray: ...
def placement_falloff(theta: np.ndarray, r_norm: np.ndarray,
                      placement: str, base: np.ndarray) -> np.ndarray: ...
def anatomical_weight(theta: np.ndarray) -> np.ndarray: ...
```

### 2.6 morph.engine  [core]

```python
class MorphEngine:
    def __init__(self, volume_tol_cc: float = 2.0, max_iters: int = 25): ...
    def morph(self, mesh, lm, params) -> MorphResult: ...
```

## Revision log (authoritative — supersedes prose above where they conflict)

**rev.1** — local_frame is landmark-anchored (chest-wall plane through
imf/lateral/medial), not mesh-normal-derived: smoothed normals tilt on
deformed meshes and made region selection non-deterministic.

**rev.2** — displacement is along LOCAL surface normals, not the global
anterior axis (anterior displacement lost up to ~30% enclosed volume on the
steep mound slope). placement_falloff is volume-neutral against its `base`
and apex-preserving (placement redistributes tissue superior↔inferior; it
never changes enclosed volume or apex height).

**rev.3** — dome profile (1−r²)^β with per-implant fullness β = πa²h/V − 1;
volume closure via bisection on a UNIFORM field multiplier m. Semantics:
**volume and base width are hard constraints; measured projection is the
volume-consistent OUTPUT** (rated projection ≠ in-vivo projection — the
implant dome height is not what a patient gains). Projection gates are
sanity bounds + regression band, not ±5%. Also: region mask margin ≥ s_bw
(scaled dome must fit the mask); base width measured from the applied
normal-field support (absolute elevation caught torso slope + in-plane
slide); hemithorax midline clip; fixture resolution 6 (~40k verts, mesh
quantization was the bw accuracy floor); volume tolerance ±max(2 cc, 1.5%)
(icosphere base is not x-mirror-symmetric). Constructor dropped
`volume_split` — each breast receives one implant of `volume_cc`.
Known v0 limitation: wide-footprint domes spread volume into the skirt, so
projection gain can dip slightly for very large implants (v1: skirted dome).

### 2.5 morph.guardrails  [core]

```python
@dataclass
class GuardrailResult:
    ok: bool
    warnings: list[str]
    clamped: bool
    clamped_params: "ImplantParams | None"   # set iff clamped

def check_compatibility(lm: ChestLandmarks, params: "ImplantParams") -> GuardrailResult:
    """Rules (v0):
    - implant base_width_cm > 0.90 * chest_width_cm/2 → clamp base width to that
      bound (clamped=True, warning) — implant wider than hemithorax is surgically
      implausible.
    - projection_cm > 1.4 * breast_base_radius(lm) → warning (no clamp).
    - volume_cc outside [100, 1000] → warning.
    ok = (no warnings and not clamped)."""
```

### 2.6 morph.engine  [core]

```python
@dataclass
class MorphResult:
    mesh: trimesh.Trimesh
    deformation: np.ndarray          # (N,3) per-vertex displacement applied
    achieved_volume_cc: dict         # {"left": float, "right": float}
    measurements: dict               # per side: projection_cm, base_width_cm
    guardrails: GuardrailResult
```

**Algorithm (rev.3 — authoritative):** guardrail check (clamped params used if
clamped) → per breast: landmark-anchored frame → in-plane base-width scaling
(clamped [0.8, 1.5]) → dome field h·(1−r²)^β along local surface normals,
with volume-neutral placement multiplier (× anatomical weight for teardrop)
→ volume closure: uniform multiplier m via bisection until added volume =
`volume_cc` (one implant per breast) within tolerance. Asymmetry is v2.

### 2.7 implants.schema  [db]

```python
class Shape(str, Enum): ROUND="round"; ANATOMICAL="anatomical"
class Placement(str, Enum):
    SUBGLANDULAR="subglandular"; SUBMUSCULAR="submuscular"; DUAL_PLANE="dual-plane"

class ImplantSKU(BaseModel):
    sku_id: str                    # slug, unique, e.g. "mentor-memorygel-350-hp"
    brand: str                     # "Mentor" | "Natrelle" | "Motiva" | "Sientra"
    product_line: str
    volume_cc: float = Field(gt=0)
    base_width_cm: float = Field(gt=0)
    projection_cm: float = Field(gt=0)
    shape: Shape
    profile_class: str             # free text: "low"|"moderate"|"moderate plus"|"high"|"ultra high"
    placement_options: list[Placement]
    values_status: Literal["illustrative_placeholder","verified"] 
    source: str                    # where dims came from / data-entry note

class ImplantParams(BaseModel):    # geometric bundle consumed by MorphEngine
    volume_cc: float; base_width_cm: float; projection_cm: float
    shape: Shape; placement: Placement
```
`ImplantParams` lives in **schema.py** (db agent) and is imported by core. Contract frozen as above.

### 2.8 implants.db  [db]

```python
class ImplantDB:
    def __init__(self, skus: list[ImplantSKU]): ...
    @classmethod
    def from_json(cls, path: str | Path | None = None) -> "ImplantDB":
        """None → bundled implants/data/implants.json (importlib.resources)."""
    def get(self, sku_id: str) -> ImplantSKU            # KeyError w/ clear msg
    def find(self, *, brand=None, shape=None, profile_class=None,
             min_cc=None, max_cc=None, placement=None) -> list[ImplantSKU]
    def to_params(self, sku_id: str, placement: Placement | str) -> ImplantParams
        # raises ValueError if placement not in sku.placement_options
```

### 2.9 implants.json starter data  [db]

12–16 SKUs, all four brands, volumes 175–650 cc, ≥3 profile classes, both shapes
(anatomical at least 2). Dimensions **realistic but illustrative**: typical
350 cc high-profile round ≈ base 11.5–12.5 cm, projection 4.5–5.0 cm; scale
plausibly across volumes/profiles (higher profile = narrower base + more
projection at same cc). Every record: `values_status="illustrative_placeholder"`,
`source="PLACEHOLDER — populate from manufacturer dimension tables (data-entry task M0)"`.

## 3. Tests (pytest, all must pass)

Each agent tests only its own modules. Required:

**core** — `tests/test_deformation.py`, `test_engine.py`, `test_validation.py`:
- volume closure: achieved per side within tol (default 2 cc) of target on ≥3
  implant sizes (e.g., 250/350/550 cc) — `test_validation.py`
- projection & base width within **±5%** of (existing + delta) target — `test_validation.py`
- placement: upper_pole_slope(submuscular) < upper_pole_slope(subglandular)
- anatomical: inferior-pole mean displacement > superior-pole (vs round control)
- symmetry: left/right achieved volumes within 5% of each other
- guardrails: oversized base width clamps + warns; ok flag semantics
- determinism: same inputs → identical deformation arrays
- fixture: synthetic_torso is watertight; FixtureLandmarkProvider round-trips
  with constructor params

**db** — `tests/test_implant_db.py`:
- JSON loads; all records validate; ≥12 SKUs; all `illustrative_placeholder`
- find() filters (brand, cc range, profile, placement); get() KeyError message
- to_params mapping + invalid placement ValueError
- dimensional sanity: at fixed volume, higher profile ⇒ smaller base width
  AND larger projection (assert over the starter set, per shape)

## 4. Workflow rules

- Shared repo: `/mnt/agents/output/morphengine` (branch `main`). NEVER work in it directly.
- `cd /mnt/agents/output/morphengine && git worktree add $HOME/work-<branch> <branch>`; work there.
- Own only your modules/files per §1. Do NOT edit SPEC.md, pyproject.toml, cli.py, or the other agent's modules/tests.
- Run `pip install -e $HOME/work-<branch>[dev]` (or set PYTHONPATH=src) then `python -m pytest tests/<yours> -q` — green before commit.
- Commit with clear messages; push not required (local repo). Report: files changed, test output, deviations (should be none).
- Never `git worktree prune`. Never touch `main`.

## 5. Acceptance criteria

1. `pytest` green across the merged repo.
2. `ImplantDB.from_json().to_params(...)` feeds `MorphEngine.morph()` on
   `synthetic_torso()` with `FixtureLandmarkProvider` — integration by main agent.
3. Validation gates (±5% projection/base width, ±2 cc volume) pass on fixture.
