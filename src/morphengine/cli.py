"""Demo CLI: morph a synthetic torso with an implant SKU and report measurements.

Usage:
    python -m morphengine.cli --sku mentor-memorygel-350-hp --placement submuscular
    python -m morphengine.cli --list
    python -m morphengine.cli --sku <id> --export-obj /tmp/out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="morphengine", description=__doc__)
    parser.add_argument("--sku", help="SKU id from the implant DB")
    parser.add_argument("--placement", default="submuscular",
                        choices=["subglandular", "submuscular", "dual-plane"])
    parser.add_argument("--list", action="store_true", help="list SKUs and exit")
    parser.add_argument("--export-obj", metavar="DIR",
                        help="write before/after meshes as OBJ to DIR")
    parser.add_argument("--chest-width", type=float, default=34.0,
                        help="fixture chest width in cm")
    args = parser.parse_args(argv)

    from morphengine.implants.db import ImplantDB

    db = ImplantDB.from_json()

    if args.list:
        for sku in db.find():
            print(f"{sku.sku_id:38s} {sku.brand:9s} {sku.volume_cc:5.0f} cc  "
                  f"{sku.shape.value:10s} {sku.profile_class}")
        return 0

    if not args.sku:
        parser.error("--sku is required (or use --list)")

    from morphengine.geometry.fixtures import FixtureLandmarkProvider, synthetic_torso
    from morphengine.morph.engine import MorphEngine

    params = db.to_params(args.sku, args.placement)
    mesh = synthetic_torso(chest_width_cm=args.chest_width)
    lm = FixtureLandmarkProvider().locate(mesh)

    result = MorphEngine().morph(mesh, lm, params)

    report = {
        "sku": args.sku,
        "params": json.loads(params.model_dump_json()),
        "guardrails": {
            "ok": result.guardrails.ok,
            "warnings": result.guardrails.warnings,
            "clamped": result.guardrails.clamped,
        },
        "achieved_volume_cc": result.achieved_volume_cc,
        "measurements": result.measurements,
    }
    print(json.dumps(report, indent=2))

    if args.export_obj:
        out = Path(args.export_obj)
        out.mkdir(parents=True, exist_ok=True)
        mesh.export(out / "before.obj")
        result.mesh.export(out / "after.obj")
        print(f"wrote {out/'before.obj'} and {out/'after.obj'}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
