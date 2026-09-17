#!/usr/bin/env python3
"""Generate, validate, and write the synthetic township."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from civictwin.io import save_township  # noqa: E402
from civictwin.synth.township import generate  # noqa: E402

logger = logging.getLogger("build_township")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", type=Path, default=Path("data/township.json"))
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    t = generate(seed=args.seed, scale=args.scale)
    problems = t.validate()
    if problems:
        for p in problems:
            logger.error("validation: %s", p)
        return 1

    logger.info(
        "township '%s': %d assets, %d links, %d zones, %d roads, population %d",
        t.name,
        len(t.assets),
        len(t.links),
        len(t.zones),
        len(t.roads),
        sum(z.population for z in t.zones),
    )
    if args.validate_only:
        return 0
    save_township(t, args.out)
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
