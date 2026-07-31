"""Anny mesh fitting (M3): fit the parametric body to an arbitrary target mesh.

This is the converter half of the photo pipeline: SAM 3D Body (user-side,
HF-gated) turns a photo into a mesh; this module turns that mesh into an
Anny body with derived ChestLandmarks, ready for the morph engine.

Fitting is correspondence-free ICP over phenotype + pose parameters via
``anny.ParametersRegressor`` (point-to-mesh distance on sampled points).
Gender and age are OPTIMIZED OUT: the target population is female and adult
age has weak surface effect, so both are fixed (gender=1.0, age anchor
search over a small adult set) — this stops the optimizer from trading
gender/age against weight/height, which a free fit happily does.

Frame contract: targets are given in the anny frame (meters, +x left, +z up,
-y anterior) — SAM/MHR outputs convert with the helpers below.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from .anny_body import derive_chest_landmarks, to_engine_frame
from .landmarks import ChestLandmarks

ADULT_AGE_ANCHORS = (0.2, 0.35, 0.55)


def anny_available() -> bool:
    try:
        import anny  # noqa: F401
        return True
    except ImportError:
        return False


def to_anny_frame(vertices_cm: np.ndarray) -> np.ndarray:
    """engine (cm, +x left, +y up, +z anterior) -> anny (m, +x left, +z up, -y anterior)."""
    v = np.asarray(vertices_cm, dtype=np.float64)
    return np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1) / 100.0


@dataclass
class AnnyFitResult:
    """Output of one fit: the Anny body best explaining the target mesh."""
    phenotype: dict                 # fitted floats (gender/age fixed)
    pose_parameters: np.ndarray     # [n_bones, 4, 4] homogeneous
    vertices_m: np.ndarray          # fitted surface, anny frame (m)
    faces: np.ndarray
    pve_mm: float                   # mean per-vertex surface error
    age_anchor: float

    def to_engine_mesh(self) -> trimesh.Trimesh:
        return trimesh.Trimesh(vertices=to_engine_frame(self.vertices_m),
                               faces=self.faces, process=True)


class AnnyMeshFitter:
    """Fits Anny bodies to target meshes.

    The model is built once per fitter (float32 — the regressor's internal
    tensors are float32) and reused; one fit costs ~5-10 s on CPU.
    """

    def __init__(self, gender: float = 1.0,
                 age_anchors: tuple[float, ...] = ADULT_AGE_ANCHORS,
                 max_n_iters: int = 5, n_points: int = 5000,
                 verbose: bool = False, **model_kwargs):
        self.gender = float(gender)
        self.age_anchors = tuple(age_anchors)
        self.max_n_iters = int(max_n_iters)
        self.n_points = int(n_points)
        self.verbose = bool(verbose)
        self.model_kwargs = dict(triangulate_faces=True, **model_kwargs)
        self._model = None
        self._regressor = None

    def _get_regressor(self):
        if self._regressor is None:
            import anny
            self._model = anny.create_fullbody_model(**self.model_kwargs).float()
            self._regressor = anny.ParametersRegressor(
                self._model, max_n_iters=self.max_n_iters,
                n_points=self.n_points, verbose=self.verbose)
        return self._regressor

    def fit(self, target_vertices_m: np.ndarray,
            initial_phenotype: dict | None = None) -> AnnyFitResult:
        """Fit to target verts (anny frame, meters, [N,3]). Returns AnnyFitResult."""
        import torch

        reg = self._get_regressor()
        v_t = torch.as_tensor(np.asarray(target_vertices_m, dtype=np.float32))[None]
        base = dict(initial_phenotype or {})
        base["gender"] = self.gender

        best = None
        for anchor in self.age_anchors:
            macros = dict(base, age=float(anchor))
            pose, pheno, v_hat = reg(
                v_t, initial_phenotype_kwargs=macros,
                optimize_phenotypes=True,
                excluded_phenotypes=["gender", "age"],
                max_n_iters=self.max_n_iters)
            pve = 1000.0 * torch.norm(v_hat - v_t, dim=-1).mean().item()
            if self.verbose:
                print(f"age anchor {anchor:.2f}: PVE {pve:.2f} mm")
            if best is None or pve < best.pve_mm:
                best = AnnyFitResult(
                    phenotype={k: float(v.reshape(-1)[0]) for k, v in pheno.items()},
                    pose_parameters=pose[0].cpu().numpy(),
                    vertices_m=v_hat[0].cpu().numpy(),
                    faces=self._model.faces.cpu().numpy(),
                    pve_mm=pve, age_anchor=float(anchor))
        return best

    def fit_engine_mesh(self, mesh_cm: trimesh.Trimesh,
                        initial_phenotype: dict | None = None
                        ) -> tuple[trimesh.Trimesh, ChestLandmarks, dict]:
        """Engine-frame convenience: fit a cm mesh, return (mesh, landmarks, meta)."""
        result = self.fit(to_anny_frame(mesh_cm.vertices), initial_phenotype)
        mesh = result.to_engine_mesh()
        lm = derive_chest_landmarks(mesh)
        meta = dict(provider="anny-fit", phenotype=result.phenotype,
                    pve_mm=result.pve_mm, age_anchor=result.age_anchor,
                    n_vertices=len(mesh.vertices),
                    watertight=bool(mesh.is_watertight))
        return mesh, lm, meta
