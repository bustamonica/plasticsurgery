#!/usr/bin/env bash
# Repeatable test runner for sandboxed environments where site-packages
# may not persist between shell invocations.
set -e
cd "$(dirname "$0")"
python3 -m pip install -q numpy scipy trimesh pydantic pytest manifold3d mapbox-earcut rtree
python3 -m pip install -q -e .
python3 -m pytest tests/ -q "$@"
