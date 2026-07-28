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

**rev.4** — slide-share guardrail: in-plane base-width expansion itself adds
volume; when it exceeds the requested volume the engine warns
("implant/chest mismatch") and skips the dome instead of crashing
(verifier-found crash: 100 cc + 15 cm base). Base-width measurement degrades
gracefully instead of raising. Volume-closure non-convergence now surfaces
as a guardrail warning. The non-watertight volume fallback is a rough
first-order estimate (emits RuntimeWarning; the earlier ±10% claim is
withdrawn — curvature terms dominate on convex mounds; the engine only runs
on watertight meshes). Note: `measurements['base_width_cm']` is the applied
dome footprint; final visible extent runs ~1–1.5 cm wider from rim drape
(v1 metric refinement).

**rev.5** — implant DB swapped from illustrative placeholders to **verified
manufacturer dimension tables** (581 SKUs; §2.9). Schema gains optional
`profile_label`, `height_cm`, `notes` (additive, backward-compatible).
Canonical 5-rank `profile_class` ladder (low < moderate < moderate plus <
high < ultra high) maps each manufacturer's own profile name; dimensional
invariants are enforced at generation time by `scripts/build_implants_db.py`
(base width strictly increasing with volume per ladder; projection
non-decreasing up to official-table rounding dips ≤0.2 cm; at fixed volume
higher profile ⇒ base not wider + projection strictly greater; dome-fullness
β ≥ 0.05 floor for every record). Real-data consequence: apex retention
(delta/rated projection) ranges ~0.46–0.94 across the 230→550 cc sweep, so
the projection regression band widens 0.5 → 1.0 cm (v0 skirt physics, see
rev.3; v1 skirted dome should re-tighten).

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
    profile_class: str             # canonical ladder: "low"|"moderate"|"moderate plus"|"high"|"ultra high"
    placement_options: list[Placement]
    values_status: Literal["illustrative_placeholder","verified"]
    source: str                    # manufacturer doc citation + cross-check note
    # rev.5 (optional, additive):
    profile_label: str | None      # manufacturer's own profile name, e.g. "Demi", "SRF (Full)"
    height_cm: float | None        # anatomical shell height (round: absent)
    notes: str | None              # source-conflict resolutions / market caveats

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

### 2.9 implants.json verified catalog  [db]  (rev.5)

**581 SKUs**, all four brands, volumes 95–965 cc, all 5 canonical profile
classes, both shapes (136 anatomical). Every record is transcribed
**programmatically** from vendored official-manufacturer tables by
`scripts/build_implants_db.py` (never hand-edited — regenerate, don't patch).
Source extractions with citations live in `implants/data/sources/*.json`.

Included lines (smooth-shell tables throughout; textured variants differ
≤0.2 cm or share dims and are documented but not duplicated):

| Brand | Lines | Profiles (manufacturer label → canonical class) |
|---|---|---|
| Mentor | MemoryGel (4), MemoryShape (3 styles) | Moderate Classic→moderate, Moderate Plus→moderate plus, High→high, Ultra High→ultra high; MM/MM+/MH |
| Natrelle | Inspira (5; dims gel-independent: Responsive=SoftTouch=Cohesive) | Low→low, Low-Plus→moderate, Moderate→moderate plus, Full→high, Extra-Full→ultra high |
| Motiva | Ergonomix (4), Ergonomix2 (4), TrueFixation FF/MF/LF | Mini→low, Demi→moderate, Full→high, Corsé→ultra high |
| Sientra | OPUS Luxe (5), OPUS Curve (4 variants) | Low→low, Moderate→moderate, Moderate Plus→moderate plus, High→high, Xtra High→ultra high |

Primary sources: MENTOR Product Catalog PN 020827-181217 (mentordirect.com,
cross-checked jnjmedtech.com — exact match); Natrelle Product Catalog
(Allergan US ~2017, cross-checked 2015 sales tool + UK 2016 catalog); Motiva
Implant Matrix (motiva.health, captured 2026-07, cross-checked 2020 catalogue
+ FDA PMA P230005); Sientra MDC-0343 R3 / MDC-0270 R11 / MDC-0400 R4
(sientra.com, cross-checked MDC-0177 R6).

Documented decisions/exclusions:
- **Natrelle 410 excluded** (US market withdrawal July 2019, BIOCELL recall);
  historical tables remain in sources/ for provenance only.
- Motiva Round excluded (dimension-identical to Ergonomix per manufacturer);
  Corsé 1050/1060 cc not listed (delisted from current catalogue).
- Sientra Low Plus excluded (keeps canonical ladder unambiguous); Xtra High
  245 cc dropped (only in 2022 QRG; 2019 catalog + current site start at 275).
- Sientra MP 455 cc projection conflict resolved 4.8 cm (2-of-3 official docs;
  per-record `notes`).
- Motiva TrueFixation flagged per-record: not FDA-approved (international).
- Anchor value: mentor-memorygel-350-hp = 11.7 cm base / 4.8 cm proj
  (MENTOR catalog p.8, exact match jnjmedtech.com).

## 3. Tests (pytest, all must pass)

Each agent tests only its own modules. Required:

**core** — `tests/test_deformation.py`, `test_engine.py`, `test_validation.py`:
- volume closure: achieved per side within tol (default 2 cc) of target on ≥3
  implant sizes (e.g., 250/350/550 cc) — `test_validation.py`
- projection & base width within **±5%** of (existing + delta) target — `test_validation.py`
- placement: upper_pole_slope(submuscular) > upper_pole_slope(subglandular) —
  the metric measures drop-off from the apex; submuscular's compressed upper
  pole drops FASTER (rev.4 text fix; original text had the semantics inverted)
- anatomical: inferior-pole mean displacement > superior-pole (vs round control)
- symmetry: left/right achieved volumes within 5% of each other
- guardrails: oversized base width clamps + warns; ok flag semantics
- determinism: same inputs → identical deformation arrays
- fixture: synthetic_torso is watertight; FixtureLandmarkProvider round-trips
  with constructor params

**db** — `tests/test_implant_db.py`:
- JSON loads; all records validate; ≥500 SKUs; all `verified` with real
  citations (no PLACEHOLDER stubs)
- find() filters (brand, cc range, profile, placement); get() KeyError message
- to_params mapping + invalid placement ValueError
- dimensional sanity: at fixed volume within a manufacturer line, higher
  profile ⇒ base width NOT larger AND strictly larger projection (rev.5:
  official tables have equal-width steps); generator asserts the same
  invariants at build time

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

---

## M1 — Synthetic Data Factory + Painter v0 (rev.6)

**rev.6** — M1 scope: generate synthetic before/after conditioning quadruplets
(before render, after render, geometry conditioning maps, metadata) from the M0
engine, plus the painter training pipeline (dataset loader + train script).
Reference renderer is a pure-numpy z-buffer (deterministic, dependency-free);
GPU training configs are provided but weight training is out of sandbox scope.
New packages: `morphengine.datafactory`, `morphengine.painter`.

### M1.1 datafactory.bodies  [factory]

```python
class BodySampler:
    def __init__(self, seed: int = 0, resolution: int = 5): ...
    def sample(self) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        """(mesh, landmarks, body_params) via geometry.fixtures.synthetic_torso.
        Uniform draws: chest_width 30-42, breast_radius 4.5-7.0,
        breast_projection 2.0-4.5, breast_x 5.5-8.5, breast_y 1.0-3.5,
        torso_depth 17-24. body_params = the exact kwargs used (for manifest).
        Seeded numpy Generator; reproducible."""
    def sample_n(self, n: int) -> list[tuple[trimesh.Trimesh, ChestLandmarks, dict]]
```

### M1.2 datafactory.render  [render]

```python
@dataclass(frozen=True)
class Camera:
    position: tuple[float, float, float]   # cm
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov_deg: float
    image_size: int                        # square

@dataclass
class RenderResult:
    rgb: np.ndarray      # (H,W,3) uint8 — Lambert+Blinn shaded
    depth: np.ndarray    # (H,W) float32 camera-space z (cm); bg = np.nan
    normal: np.ndarray   # (H,W,3) float32 camera-space unit normals; bg = 0
    mask: np.ndarray     # (H,W) bool

class SoftwareRenderer:
    """Pure-numpy z-buffer rasterizer. Deterministic; no GL/GPU deps.
    Perspective projection; backface-cull optional (keep for closed meshes).
    Shading: albedo (0.72,0.57,0.48), ambient 0.15, Lambert key light
    from upper-left-front, Blinn specular 0.2/shininess 32, bg (245,242,238).
    Perf budget: <=20 s per 256x256 render of a ~40k-triangle mesh."""
    def __init__(self, camera: Camera): ...
    def render(self, mesh: trimesh.Trimesh) -> RenderResult: ...

def front_camera(mesh_bbox: np.ndarray, image_size: int = 256) -> Camera
    # centered, +z viewing distance ~3x bbox diagonal, fit-to-bbox 1.15 margin
def oblique_camera(mesh_bbox: np.ndarray, azimuth_deg: float = 40.0,
                   image_size: int = 256) -> Camera
```

### M1.3 datafactory.factory  [factory]

```python
class DatasetFactory:
    def __init__(self, out_dir: str | Path, db: ImplantDB | None = None,
                 engine: MorphEngine | None = None,
                 renderer_cls=SoftwareRenderer): ...
    def make_pair(self, mesh, lm, body_params: dict, sku: ImplantSKU,
                  placement: Placement, camera_kind: str) -> dict | None:
        """Morph + render both states. Returns manifest row, or None when
        engine guardrails clamp/warn mismatch (data-cleanliness gate;
        skip, never emit a clamped pair)."""
    def generate(self, n_pairs: int, seed: int = 0,
                 image_size: int = 256) -> list[dict]:
        """Full run: BodySampler(seed) bodies, weighted sampling, writes
        images + manifest.jsonl, returns rows."""
```

Sampling weights (seeded): volume 200-500 cc 80% / 500-700 15% / else 5%;
profile moderate 25 / moderate plus 25 / high 35 / ultra high 15 (within the
chosen volume's available profiles); placement submuscular 55 / dual-plane 30 /
subglandular 15; shape round 80 / anatomical 20; camera front 60 / oblique 40.

Output layout + channels:
```
{out_dir}/manifest.jsonl            # one JSON object per pair
{out_dir}/images/{pair_id}_before.png        # RGB uint8
{out_dir}/images/{pair_id}_after.png
{out_dir}/cond/{pair_id}_depth_before.npy    # float32 cm, bg NaN
{out_dir}/cond/{pair_id}_depth_after.npy
{out_dir}/cond/{pair_id}_normal_after.npy    # (H,W,3) float32
{out_dir}/cond/{pair_id}_mask_before.npy     # bool
```
pair_id = f"{seeded_index:05d}_{sku_id}_{placement}_{camera_kind}".

Manifest row keys: pair_id, sku_id, brand, product_line, profile_class,
profile_label, volume_cc, base_width_cm, projection_cm, shape, placement,
camera_kind, image_size, body_params, engine {achieved_volume_cc{left,right},
ok, warnings}, files {before, after, depth_before, depth_after, normal_after,
mask_before} (paths relative to out_dir), prompt, seed.

prompt = f"photorealistic breast augmentation result, {volume_cc} cc
{profile_label} {shape} implant, {placement} placement, {camera_kind} view,
same person, natural skin tone".

### M1.4 painter.dataset  [painter]

```python
class PairDataset(torch.utils.data.Dataset):
    def __init__(self, manifest: str | Path, image_size: int = 256): ...
        # loads + center-resizes to image_size
    def __len__(self) -> int
    def __getitem__(self, i) -> dict:
        # before: (3,H,W) float32 in [-1,1]; after: (3,H,W) in [-1,1]
        # cond:   (6,H,W) float32 in [0,1] = [depth_before, depth_after,
        #          normal_after_x, normal_after_y, normal_after_z, mask_before]
        #          depths normalized per-pair by the 1-99 percentile body range
        # prompt: str (from manifest)
```

### M1.5 painter.train  [painter]

```python
@dataclass
class TrainConfig:
    model: str            # "tiny" (CPU smoke) | "sdxl-lora" (GPU runbook)
    image_size: int = 256
    lr: float = 1e-4; batch_size: int = 4; steps: int = 100
    lora_rank: int = 16; lora_alpha: int = 32
    base_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    out_dir: str = "painter_runs/run0"
    seed: int = 0

def build_tiny_unet(in_ch: int = 9, out_ch: int = 3, base: int = 32) -> torch.nn.Module
    # small UNet: concat(before 3ch, cond 6ch) -> after 3ch; ~1M params
def train(cfg: TrainConfig, manifest: str | Path) -> dict:
    # returns {"final_loss": float, "steps": int, "ckpt": path}
    # model="tiny": pure-torch loop (L1 + 0.5*MSE), CPU-capable
    # model="sdxl-lora": requires diffusers/peft (declared in painter extra,
    # NOT imported in tiny mode); see painter/README.md runbook
```

`configs/painter_v0.yaml`: the GPU config (sdxl-lora, 512px, 20k steps,
bf16, batch 8, cosine schedule, EMA) matching the design-doc Stage-1 plan.

### M1.6 tests

- `tests/test_render.py` — shapes/dtypes; NaN bg; z-buffer correctness
  (two overlapping known triangles, nearer wins); determinism (same mesh →
  identical arrays); mask bbox sanity on the fixture torso.
- `tests/test_bodies.py` — sample_n count; all watertight; params in ranges;
  landmark roundtrip; seed reproducibility.
- `tests/test_factory.py` — generate 3 pairs (image_size=128, resolution=4
  bodies) → all files exist; manifest rows have every required key; engine.ok
  true; volume closure within tol; determinism (two runs → identical manifest).
- `tests/test_painter_data.py` — torch = pytest.importorskip; len; tensor
  shapes/ranges; prompt contains the volume.
- `tests/test_painter_train.py` — importorskip torch; tiny UNet forward shape;
  train(steps=3, batch_size=2) on 4 synthetic pairs → finite final_loss,
  ckpt written.
