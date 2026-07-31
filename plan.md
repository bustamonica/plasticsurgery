# Parallel execution plan — post-GitHub phase

## Dependency graph

```
                 ┌─ Track A: Anny real bodies (B-1 → M2)      [RUNNING, sandbox]
NOW (parallel) ──┼─ Track B: Backend API service scaffold      [coder subagent]
                 ├─ Track C: Painter Colab training package    [coder subagent]
                 └─ Track D: Research: photo→body fit + real   [explore subagent]
                              before/after datasets

GATE 1: user approves first real-anatomy before/after render
                 ┌─ M2: factory v2 — BodySampler on Anny phenotypes
THEN (parallel) ─┤  regenerate demo datasets on real bodies
                 └─ painter conditioning v2 (real-body geometry maps)

GATE 2: painter smoke-train on Colab (user runs one cell)
                 └─ website integration: Next.js (claude branch) ↔ FastAPI ↔ engine
```

## Track ownership

- **A — Anny bodies** (orchestrator + coder): install, landmark provider
  (nipples/sternum/chest metrics from Anny mesh), proof render, then factory v2.
  Critical path for realism of geometry.
- **B — Backend API** (coder, worktree `service-api`): FastAPI wrapping
  morphengine: POST /morph {sku, placement} → before/after PNGs + engine
  metadata; OpenAPI contract for the website; pytest. Independent of A
  (uses synthetic fixture behind a provider interface Anny slots into later).
- **C — Painter Colab package** (coder, worktree `painter-colab`):
  one-click notebook (deps → dataset upload/generate → SDXL+LoRA train →
  sample grid → weights download), validates configs/painter_v0.yaml paths.
- **D — Research** (explore): photo→body-model fitting options (Anny fit
  API / SAM-3D-Body / TokenHMR), and legal real before/after pair sources
  for painter fine-tune. Feeds Track A follow-up + M3.

## Merge order

B and C merge first (independent dirs: `service/`, `notebooks/`).
A merges after GATE 1. Dataset regen after A. Website last.
