#!/usr/bin/env python3
"""Generate a synthetic before/after dataset (SPEC §M1.3).

Example:
    python3 scripts/generate_dataset.py --n 24 --seed 0 --size 256 --out dataset_demo
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=24, help="number of pairs to write")
    ap.add_argument("--seed", type=int, default=0, help="master seed (fully deterministic)")
    ap.add_argument("--size", type=int, default=256, help="square image size, px")
    ap.add_argument("--out", type=str, required=True, help="output directory")
    ap.add_argument("--resolution", type=int, default=5,
                    help="torso icosphere subdivisions (BodySampler resolution)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log guardrail skips")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    from morphengine.datafactory.factory import DatasetFactory

    factory = DatasetFactory(args.out, resolution=args.resolution)
    rows = factory.generate(args.n, seed=args.seed, image_size=args.size)
    report = factory.report_

    print(f"pairs written: {report['written']} / {report['requested']} requested")
    print(f"guardrail skips (resampled): {report['skips']}")
    if report["shortfall"]:
        print(f"shortfall: {report['shortfall']} pair(s) could not be produced "
              f"after 5 attempts each")
    print("per-brand counts:")
    for brand, count in sorted(Counter(r["brand"] for r in rows).items()):
        print(f"  {brand}: {count}")
    print(f"manifest: {Path(args.out) / 'manifest.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
