#!/bin/bash
set -e
pip install -q numpy scipy trimesh pydantic pytest manifold3d mapbox-earcut rtree pillow
pip install -q torch --index-url https://download.pytorch.org/whl/cpu
pip install -q -e .
python3 -m pytest tests/ -q
