#!/usr/bin/env python3
"""Cross-model, statistical, and decision validation. Writes results/validation.json.

Where an established sector tool is not installed the corresponding entry is
recorded as skipped rather than quietly passing. Any check that fails stays
failed in the artefact.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gotham.analysis.hindcast import (  # noqa: E402
    counterfactual,
    load_events,
    replay,
)
from gotham.analysis.interventions import generate_catalogue  # noqa: E402
from gotham.analysis.metrics import cvar  # noqa: E402
from gotham.analysis.montecarlo import make_scenario_set, run_set  # noqa: E402
from gotham.analysis.optimize import greedy_plan  # noqa: E402
from gotham.config import DEFAULT, Config  # noqa: E402
from gotham.engine.coordinator import Engine  # noqa: E402
from gotham.io import data_version_of, load_township, model_version, save_json  # noqa: E402
from gotham.ontology import AssetKind, DamageState, Service, Township  # noqa: E402

logger = logging.getLogger("verify_models")

ENERGY_TARGET_JACCARD = 0.90
WATER_TARGET_TIMING_H = 3.0
TRANSPORT_TARGET_MAPE = 20.0
STABILITY_TARGET = 0.05
PLAN_OVERLAP_TARGET = 0.85
DEMAND_SATISFIED_THRESHOLD = 0.5

LIMITATIONS = [
    "Water mains and distribution feeders are synthetic (see the provenance report)",
    "Batteries do not recharge within the 72-hour horizon",
    "Costs are illustrative unless marked schedule_of_rates",
    "Operator behaviour is rule-based, not behavioural",
    "Hospital capacity is modelled as availability, not as beds or staffing",
]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _damage_states(engine: Engine, scenario) -> dict[str, DamageState]:
    result = engine.simulate(scenario, _skip_amplification=True)
    return result.damaged_assets


def _fast_dark_zones(engine: Engine, scenario) -> set[str]:
    """Zones the fast energy model leaves without power at the worst hour."""
    result = engine.simulate(scenario, record=True, _skip_amplification=True)
    worst: set[str] = set()
    worst_count = -1
    for frame in result.timeline or ():
        dark = {z for z, s in frame.zones.items() if s["energy"] <= 0.01}
        if len(dark) > worst_count:
            worst_count, worst = len(dark), dark
    return worst


def verify_energy(
    township: Township, engine: Engine, scenarios, cfg: Config
) -> dict[str, Any]:
    """Compare de-energised zone sets against a pandapower load flow."""
    try:
        import pandapower as pp
    except ImportError:
        return {
            "tool": "pandapower",
            "skipped": True,
            "reason": "pandapower is not installed",
        }

    agreements: list[float] = []
    for scenario in scenarios:
        damaged = _damage_states(engine, scenario)
        fast = _fast_dark_zones(engine, scenario)
        net = pp.create_empty_network()
        buses: dict[str, int] = {}
        for asset in township.assets.values():
            if asset.kind in (AssetKind.GRID_SUPPLY, AssetKind.SUBSTATION,
                              AssetKind.FEEDER, AssetKind.TRANSFORMER):
                buses[asset.id] = pp.create_bus(net, vn_kv=11.0, name=asset.id)
        for aid, bus in buses.items():
            if township.assets[aid].kind is AssetKind.GRID_SUPPLY:
                pp.create_ext_grid(net, bus=bus, vm_pu=1.0)
        for link in township.links:
            if link.kind.value != "powers":
                continue
            if link.source not in buses or link.target not in buses:
                continue
            out = (
                damaged.get(link.source) in (DamageState.EXTENSIVE, DamageState.COMPLETE)
                or damaged.get(link.target) in (DamageState.EXTENSIVE, DamageState.COMPLETE)
            )
            pp.create_line_from_parameters(
                net, from_bus=buses[link.source], to_bus=buses[link.target],
                length_km=1.0, r_ohm_per_km=0.16, x_ohm_per_km=0.1,
                c_nf_per_km=0.0, max_i_ka=0.4, in_service=not out,
            )
        for zone in township.zones:
            transformer = f"TR{zone.id[1:]}"
            if transformer in buses:
                pp.create_load(net, bus=buses[transformer], p_mw=zone.population / 5000.0)
        try:
            pp.runpp(net, calculate_voltage_angles=False)
            energised = {
                net.bus.at[i, "name"]
                for i in net.res_bus.index
                if not np.isnan(net.res_bus.at[i, "vm_pu"])
            }
        except Exception:  # pragma: no cover - divergence is a valid outcome
            energised = set()
        detailed = {
            z.id for z in township.zones if f"TR{z.id[1:]}" not in energised
        }
        agreements.append(jaccard(fast, detailed))

    score = float(np.mean(agreements)) if agreements else 0.0
    return {
        "tool": "pandapower",
        "scenarios": len(scenarios),
        "agreement_deenergised_zones": round(score, 3),
        "target": ENERGY_TARGET_JACCARD,
        "pass": score >= ENERGY_TARGET_JACCARD,
        "notes": "Jaccard similarity of the set of zones left without power.",
    }


def verify_water(
    township: Township, engine: Engine, scenarios, cfg: Config
) -> dict[str, Any]:
    """Compare the hour each zone first drops below half its demand."""
    try:
        import wntr
    except ImportError:
        return {"tool": "wntr", "skipped": True, "reason": "wntr is not installed"}

    errors: list[float] = []
    for scenario in scenarios:
        result = engine.simulate(scenario, record=True, _skip_amplification=True)
        fast_times = _first_drop_hours(result, Service.WATER)
        model = wntr.network.WaterNetworkModel()
        model.options.hydraulic.demand_model = "PDD"
        for tank in (a for a in township.assets.values() if a.kind is AssetKind.TANK):
            model.add_reservoir(tank.id, base_head=30.0)
        for zone in township.zones:
            model.add_junction(
                zone.id, base_demand=zone.population / 86400.0, elevation=zone.hand_m
            )
            model.add_pipe(
                f"p{zone.id}", zone.tank, zone.id, length=500.0, diameter=0.3
            )
        simulator = wntr.sim.WNTRSimulator(model)
        try:
            sim = simulator.run_sim()
            supplied = sim.node["demand"]
            detailed_times = {
                zone.id: _first_below(supplied[zone.id].to_numpy(), DEMAND_SATISFIED_THRESHOLD)
                for zone in township.zones
            }
        except Exception:  # pragma: no cover
            detailed_times = {}
        for zone_id, fast_hour in fast_times.items():
            other = detailed_times.get(zone_id)
            if other is not None and np.isfinite(fast_hour) and np.isfinite(other):
                errors.append(abs(fast_hour - other))

    score = float(np.mean(errors)) if errors else float("inf")
    return {
        "tool": "wntr",
        "scenarios": len(scenarios),
        "mean_abs_timing_error_h": None if not errors else round(score, 2),
        "target": WATER_TARGET_TIMING_H,
        "pass": bool(errors) and score <= WATER_TARGET_TIMING_H,
        "notes": "Hour each zone first falls below 50% of demand satisfied.",
    }


def _first_drop_hours(result, service: Service) -> dict[str, float]:
    out: dict[str, float] = {}
    for frame in result.timeline or ():
        for zone_id, levels in frame.zones.items():
            if zone_id in out:
                continue
            if levels[service.value] < DEMAND_SATISFIED_THRESHOLD:
                out[zone_id] = frame.t
    return {z: out.get(z, float("inf")) for z in (result.loss_by_zone_ph or {})}


def _first_below(values: np.ndarray, threshold: float) -> float:
    below = np.where(values < threshold)[0]
    return float(below[0]) if len(below) else float("inf")


def verify_transport(
    township: Township, engine: Engine, scenarios, cfg: Config
) -> dict[str, Any]:
    """Compare zone-to-hospital travel times against an equilibrium assignment."""
    try:
        import aequilibrae  # noqa: F401
    except ImportError:
        return {
            "tool": "aequilibrae",
            "skipped": True,
            "reason": "aequilibrae is not installed",
        }
    return {
        "tool": "aequilibrae",
        "skipped": True,
        "reason": "adapter present but not exercised in this build",
    }


def statistical_stability(
    township: Township, engine: Engine, cfg: Config, catalogue, budget: float, jobs: int
) -> dict[str, Any]:
    """Does the answer hold up out of sample, and with twice the scenarios?"""
    train = make_scenario_set(township, cfg, "train", cfg.risk.n_scenarios_train, cfg.risk.seed_train)
    test = make_scenario_set(township, cfg, "test", cfg.risk.n_scenarios_test, cfg.risk.seed_test)
    train_table = run_set(engine, train, n_jobs=jobs)
    test_table = run_set(engine, test, n_jobs=jobs)
    cvar_train = cvar(train_table.weighted, cfg.risk.alpha)
    cvar_test = cvar(test_table.weighted, cfg.risk.alpha)
    relative = abs(cvar_train - cvar_test) / max(cvar_train, 1.0)

    small = replace(train, scenarios=train.scenarios[:125])
    large = replace(train, scenarios=train.scenarios[:250])
    plan_small = greedy_plan(engine, small, catalogue, budget, cfg, n_jobs=jobs)
    plan_large = greedy_plan(engine, large, catalogue, budget, cfg, n_jobs=jobs)
    overlap = jaccard(set(plan_small.interventions), set(plan_large.interventions))

    return {
        "cvar_train_ph": round(cvar_train, 1),
        "cvar_test_ph": round(cvar_test, 1),
        "relative_difference": round(relative, 4),
        "target": STABILITY_TARGET,
        "pass": relative <= STABILITY_TARGET,
        "plan_overlap_on_doubling_scenarios": round(overlap, 3),
        "plan_overlap_target": PLAN_OVERLAP_TARGET,
        "plan_overlap_pass": overlap >= PLAN_OVERLAP_TARGET,
    }


def historical_backtest(
    township: Township,
    engine: Engine,
    cfg: Config,
    events_path: Path,
    results_dir: Path,
) -> dict[str, Any]:
    """Replay any documented flood events and score the model against them.

    With no event file this reports `skipped` with the reason. A model that has
    never been tested against a real event should say so rather than quietly
    omit the section.
    """
    events = load_events(events_path)
    if not events:
        return {
            "skipped": True,
            "reason": (
                f"no documented events at {events_path}. The replay harness is "
                "built and tested; it needs an observed flood to run against, "
                "which the real-data pipeline of spec 07 would supply."
            ),
            "harness": "gotham.analysis.hindcast",
            "checks_available": [
                "hazard footprint (IoU, precision, recall)",
                "infrastructure exposure",
                "cascade sectors",
                "decision counterfactual",
            ],
        }

    plan_ids: list[str] = []
    plans_dir = results_dir / "plans"
    if plans_dir.is_dir():
        for path in sorted(plans_dir.glob("*.json")):
            plan_ids = json.loads(path.read_text()).get("interventions", [])

    replays = []
    for event in events:
        record = replay(event, engine, cfg)
        if plan_ids:
            record["counterfactual"] = counterfactual(event, engine, cfg, plan_ids)
        replays.append(record)
    return {"events": replays, "n_events": len(replays)}


def decision_value(results_dir: Path) -> dict[str, Any]:
    """Read the decision comparison straight out of the analysis artefacts."""
    path = results_dir / "baselines.json"
    if not path.exists():
        return {"skipped": True, "reason": "run scripts/run_analysis.py first"}
    data = json.loads(path.read_text())
    results = data.get("results", {})
    return {
        "budget_inr": data.get("budget_inr"),
        "cvar_asset_by_asset_ph": round(results.get("asset_by_asset", {}).get("cvar_ph", 0.0), 1),
        "cvar_optimised_ph": round(results.get("optimised", {}).get("cvar_ph", 0.0), 1),
        "cvar_random_mean_ph": round(results.get("random", {}).get("cvar_ph", 0.0), 1),
        "improvement_pct": data.get("optimised_vs_asset_by_asset_pct"),
        "evaluated_on": f"held-out {data.get('evaluated_on')} set",
        "pass": data.get("optimised_vs_asset_by_asset_pct", 0.0) > 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--township", type=Path, default=Path("data/township.json"))
    ap.add_argument("--results", type=Path, default=Path("results/"))
    ap.add_argument("--out", type=Path, default=Path("results/validation.json"))
    ap.add_argument("--scenarios", type=int, default=12)
    ap.add_argument("--budget", type=float, default=30_000_000.0)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--skip-stability", action="store_true")
    ap.add_argument(
        "--events",
        type=Path,
        default=Path("data/events.json"),
        help="documented flood events to replay (spec patch sections 18 and 19)",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = DEFAULT
    township = load_township(args.township)
    engine = Engine(township, cfg)
    catalogue = generate_catalogue(township, cfg)

    spread = make_scenario_set(township, cfg, "verify", args.scenarios * 8, 77)
    scenarios = sorted(spread.scenarios, key=lambda s: s.rain_mm)[:: max(1, (args.scenarios * 8) // args.scenarios)][: args.scenarios]

    logger.info("cross-model verification over %d scenarios", len(scenarios))
    cross = {
        "energy": verify_energy(township, engine, scenarios, cfg),
        "water": verify_water(township, engine, scenarios, cfg),
        "transport": verify_transport(township, engine, scenarios, cfg),
    }

    stability: dict[str, Any]
    if args.skip_stability:
        stability = {"skipped": True, "reason": "--skip-stability"}
    else:
        logger.info("statistical stability")
        stability = statistical_stability(
            township, engine, cfg, catalogue, args.budget, args.jobs
        )

    logger.info("historical replay")
    backtest = historical_backtest(township, engine, cfg, args.events, args.results)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_version": data_version_of(township),
        "model_version": model_version(),
        "cross_model": cross,
        "backtest": backtest,
        "stability": stability,
        "decision": decision_value(args.results),
        "limitations": LIMITATIONS,
    }
    save_json(payload, args.out)
    logger.info("wrote %s", args.out)
    for name, entry in cross.items():
        logger.info("  %-10s %s", name, "skipped" if entry.get("skipped") else f"pass={entry.get('pass')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
