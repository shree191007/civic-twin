#!/usr/bin/env python3
"""Precompute everything the demo needs so it runs with the network off."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from civictwin.analysis.spof import detect  # noqa: E402
from civictwin.api.geo import build_township_payload, projector_for  # noqa: E402
from civictwin.config import DEFAULT  # noqa: E402
from civictwin.engine.contract import HazardScenario, Overlay  # noqa: E402
from civictwin.engine.coordinator import Engine  # noqa: E402
from civictwin.engine.hazard import _derived_uniform, gumbel_cdf, sample_scenarios  # noqa: E402
from civictwin.io import data_version_of, load_township, model_version, save_json  # noqa: E402

logger = logging.getLogger("precompute_demo")

#: Rainfall stops for the slider, so it never waits on a computation.
SLIDER_MM = list(range(60, 321, 20))
SIZE_BUDGET_MB = 40.0
CAMERA_PITCH = 50.0
CAMERA_ZOOM = 13.6


def slider_scenario(township, name: str, rain_mm: float) -> HazardScenario:
    cdf = gumbel_cdf(rain_mm, DEFAULT.hazard.gumbel_loc_mm, DEFAULT.hazard.gumbel_scale_mm)
    return HazardScenario(
        id=name,
        seed=abs(hash(name)) % 1_000_000,
        rain_mm=float(rain_mm),
        field_seed=4242,
        onset_hour=18,
        asset_draws={
            aid: _derived_uniform(9_999, f"{name}:{aid}") for aid in sorted(township.assets)
        },
        return_period_y=None if cdf >= 1.0 else 1.0 / max(1e-12, 1.0 - cdf),
        label=f"slider {rain_mm} mm",
    )


def spof_targets(township, cfg, results: Path) -> list[dict[str, Any]]:
    """Camera positions for each SPOF card's 'Show me' button."""
    projector = projector_for(township)
    engine = Engine(township, cfg)
    scenarios = sample_scenarios(township, cfg, 200, cfg.risk.seed_train)
    out: list[dict[str, Any]] = []
    for spof in detect(township, cfg, scenarios, engine._base.transport):
        ids = [spof.shared_asset, *spof.redundant_group]
        points = [
            projector.to_lonlat(township.assets[a].x, township.assets[a].y)
            for a in ids
            if a and a in township.assets
        ]
        if not points:
            continue
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        out.append(
            {
                "kind": spof.kind,
                "shared_asset": spof.shared_asset,
                "highlight": [a for a in ids if a and a in township.assets],
                "camera": {
                    "lon": round((min(lons) + max(lons)) / 2, 7),
                    "lat": round((min(lats) + max(lats)) / 2, 7),
                    "zoom": CAMERA_ZOOM,
                    "pitch": CAMERA_PITCH,
                },
            }
        )
    del results
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--township", type=Path, default=Path("data/township.json"))
    ap.add_argument("--out", type=Path, default=Path("results/"))
    ap.add_argument("--fixtures", type=Path, default=Path("web/public/fixtures"))
    ap.add_argument("--skip-fixtures", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = DEFAULT
    township = load_township(args.township)
    engine = Engine(township, cfg)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written: list[tuple[str, float]] = []

    def record(path: Path) -> None:
        written.append((str(path.relative_to(out)), path.stat().st_size / 1e6))

    # 1. the rainfall slider stops, baseline plan
    slider_dir = out / "slider"
    slider_dir.mkdir(exist_ok=True)
    index: list[dict[str, Any]] = []
    for rain in SLIDER_MM:
        name = f"slider_{rain}mm"
        scenario = slider_scenario(township, name, rain)
        result = engine.simulate(scenario, Overlay(), record=True)
        payload = result.to_dict(include_timeline=True)
        payload["scenario"] = scenario.summary()
        payload["plan_id"] = "baseline"
        path = slider_dir / f"{name}.json"
        save_json(payload, path)
        record(path)
        index.append(
            {
                "id": name,
                "rain_mm": rain,
                "return_period_y": scenario.return_period_y,
                "weighted_loss_ph": round(result.weighted_loss_ph, 1),
            }
        )
    save_json(index, slider_dir / "index.json")
    record(slider_dir / "index.json")
    logger.info("precomputed %d slider stops", len(SLIDER_MM))

    # 2. the SPOF "Show me" camera targets
    targets = spof_targets(township, cfg, out)
    save_json(targets, out / "spof_targets.json")
    record(out / "spof_targets.json")
    logger.info("precomputed %d SPOF camera targets", len(targets))

    # 3. the township payload the frontend reads when the API is down
    payload = build_township_payload(township, projector_for(township))
    payload["data_version"] = data_version_of(township)
    payload["model_version"] = model_version()
    save_json(payload, out / "township_payload.json")
    record(out / "township_payload.json")

    # 4. mirror everything into the web fixtures directory
    if not args.skip_fixtures:
        fixtures = Path(args.fixtures)
        fixtures.mkdir(parents=True, exist_ok=True)
        copied = _mirror(out, fixtures)
        logger.info("mirrored %d files into %s", copied, fixtures)

    total = sum(size for _name, size in written)
    logger.info("%-44s %8s", "file", "MB")
    for name, size in sorted(written, key=lambda r: -r[1])[:12]:
        logger.info("%-44s %8.2f", name, size)
    logger.info("%-44s %8.2f", "TOTAL (this script)", total)
    everything = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    logger.info("%-44s %8.2f", "TOTAL (results/)", everything)
    if everything > SIZE_BUDGET_MB:
        logger.warning(
            "results/ is %.1f MB, over the %.0f MB demo budget", everything, SIZE_BUDGET_MB
        )
    return 0


def _mirror(results: Path, fixtures: Path) -> int:
    """Copy what the frontend's fixture fallback looks for."""
    copied = 0
    simple = {
        "risk.json": "risk.json",
        "criticality.json": "criticality.json",
        "spofs.json": "spofs.json",
        "critical_sets.json": "critical_sets.json",
        "frontier.json": "frontier.json",
        "baselines.json": "baselines.json",
        "voi.json": "voi.json",
        "sensitivity.json": "sensitivity.json",
        "ensemble.json": "ensemble.json",
        "township_payload.json": "township.json",
        "spof_targets.json": "spof_targets.json",
    }
    for source, target in simple.items():
        path = results / source
        if path.exists():
            shutil.copyfile(path, fixtures / target)
            copied += 1

    hero_src = results / "hero"
    if hero_src.is_dir():
        hero_dst = fixtures / "hero"
        hero_dst.mkdir(exist_ok=True)
        precomputed: dict[str, dict[str, Any]] = {}
        for path in sorted(hero_src.glob("*__*.json")):
            shutil.copyfile(path, hero_dst / path.name)
            copied += 1
            scenario_id, _, plan = path.stem.partition("__")
            data = json.loads(path.read_text())
            entry = precomputed.setdefault(
                scenario_id,
                {
                    "scenario_id": scenario_id,
                    "rain_mm": data.get("scenario", {}).get("rain_mm"),
                    "return_period_y": data.get("scenario", {}).get("return_period_y"),
                    "plans": [],
                },
            )
            entry["plans"].append(plan)
        for entry in precomputed.values():
            entry["plans"].sort()
        save_json(sorted(precomputed.values(), key=lambda e: e["rain_mm"] or 0),
                  fixtures / "precomputed.json")
        copied += 1

    plans_src = results / "plans"
    if plans_src.is_dir():
        plans_dst = fixtures / "plans"
        plans_dst.mkdir(exist_ok=True)
        for path in plans_src.glob("*.json"):
            shutil.copyfile(path, plans_dst / path.name)
            copied += 1
    return copied


if __name__ == "__main__":
    raise SystemExit(main())
