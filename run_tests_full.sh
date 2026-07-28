#!/bin/bash
set -e
python3 -m pip install -q numpy scipy trimesh pydantic pytest manifold3d mapbox-earcut rtree pillow
python3 -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install -q -e .
python3 -m pytest tests/ -q
