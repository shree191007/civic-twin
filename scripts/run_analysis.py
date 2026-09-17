#!/usr/bin/env python3
"""Run the full risk analysis and write every result file the API serves."""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gotham.analysis import baselines as baselines_mod  # noqa: E402
from gotham.analysis import ensemble as ensemble_mod  # noqa: E402
from gotham.analysis import restoration as restoration_mod  # noqa: E402
from gotham.analysis import spof as spof_mod  # noqa: E402
from gotham.analysis.criticality import critical_sets, rank_criticality  # noqa: E402
from gotham.analysis.interventions import generate_catalogue  # noqa: E402
from gotham.analysis.metrics import (  # noqa: E402
    bootstrap_ci,
    cvar,
    eal,
    portfolio_contributions,
    var,
    zone_contributions,
)
from gotham.analysis.ledger import (  # noqa: E402
    for_result as ledger_for_result,
    provenance_summary,
)
from gotham.analysis.montecarlo import make_scenario_set, run_set  # noqa: E402
from gotham.analysis.objectives import resolve as resolve_objective  # noqa: E402
from gotham.analysis.redundancy import redundancy_groups, system_score  # noqa: E402
from gotham.analysis.uncertainty import (  # noqa: E402
    from_interval,
    from_samples,
    round_people,
)
from gotham.hazard.schemas.forecast import UncertaintySource  # noqa: E402
from gotham.analysis.optimize import frontier, greedy_plan, plan_from_prefix  # noqa: E402
from gotham.config import DEFAULT, Config  # noqa: E402
from gotham.engine.contract import HazardScenario, Overlay  # noqa: E402
from gotham.engine.coordinator import Engine  # noqa: E402
from gotham.engine.hazard import _derived_uniform  # noqa: E402
from gotham.io import data_version_of, load_township, model_version, save_json  # noqa: E402

logger = logging.getLogger("run_analysis")

HERO_RETURN_PERIODS = {
    "storm_10y": 10.0,
    "storm_25y": 25.0,
    "storm_50y": 50.0,
    "storm_100y": 100.0,
}
HERO_PLANS = ("baseline", "asset_by_asset", "optimised")
SENSITIVITY_PARAMETERS = {
    "rain_scale_mm": ("hazard", "rain_scale_mm", 0.85, 1.15),
    "deenergise_flood_fraction": ("energy", "deenergise_flood_fraction", 0.6, 1.4),
    "handover_capacity_factor": ("comms", "handover_capacity_factor", 0.8, 1.25),
    "manual_operation_penalty_h": ("response", "manual_operation_penalty_h", 0.5, 2.0),
    "health_weight": ("loss", "health", 0.67, 1.5),
}


class Stage:
    """Logs the elapsed time of each analysis stage."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> Stage:
        self.start = time.perf_counter()
        logger.info("%-22s ...", self.name)
        return self

    def __exit__(self, *exc: object) -> None:
        logger.info("%-22s done in %.1fs", self.name, time.perf_counter() - self.start)


def hero_scenario(township, cfg: Config, name: str, return_period_y: float) -> HazardScenario:
    """A named design storm at a given return period."""
    p = 1.0 - 1.0 / return_period_y
    rain = cfg.hazard.gumbel_loc_mm - cfg.hazard.gumbel_scale_mm * math.log(-math.log(p))
    return HazardScenario(
        id=name,
        seed=abs(hash(name)) % 100_000,
        rain_mm=float(rain),
        field_seed=4242,
        onset_hour=18,
        asset_draws={
            aid: _derived_uniform(9_999, f"{name}:{aid}") for aid in sorted(township.assets)
        },
        return_period_y=return_period_y,
        label=name,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--township", type=Path, default=Path("data/township.json"))
    ap.add_argument("--out", type=Path, default=Path("results/"))
    ap.add_argument("--n-train", type=int, default=1000)
    ap.add_argument("--n-test", type=int, default=500)
    ap.add_argument("--members", type=int, default=20)
    ap.add_argument("--budget", type=float, default=30_000_000.0)
    ap.add_argument("--max-budget", type=float, default=120_000_000.0)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument(
        "--opt-scenarios",
        type=int,
        default=250,
        help="scenarios the optimiser screens on (a subset of the train set)",
    )
    ap.add_argument("--max-zone-cvar", type=float, default=None)
    ap.add_argument(
        "--objective",
        default="balanced",
        choices=["balanced", "protect_life", "minimise_economic_loss", "restore_fast"],
        help="what the optimiser minimises (spec patch section 15)",
    )
    ap.add_argument(
        "--sensitivity-candidates",
        type=int,
        default=20,
        help="catalogue size the sensitivity re-plan may choose from",
    )
    ap.add_argument("--skip-ensemble", action="store_true")
    ap.add_argument("--skip-criticality", action="store_true")
    ap.add_argument("--skip-sensitivity", action="store_true")
    ap.add_argument("--skip-restoration", action="store_true")
    ap.add_argument("--milp", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.smoke:
        # A ten-second sanity run: enough to exercise every code path and write
        # every output file, not enough to mean anything.
        args.n_train, args.n_test, args.members = 20, 10, 2
        args.opt_scenarios = 20
        args.budget = min(args.budget, 6_000_000.0)
        args.max_budget = min(args.max_budget, 10_000_000.0)
        args.sensitivity_candidates = 4
        args.skip_ensemble = True
        restoration_mod.LOCAL_SEARCH_ITERATIONS = 5

    cfg = DEFAULT
    township = load_township(args.township)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    engine = Engine(township, cfg)
    catalogue = generate_catalogue(township, cfg)
    catalogue_by_id = {i.id: i for i in catalogue}
    train = make_scenario_set(township, cfg, "train", args.n_train, cfg.risk.seed_train)
    test = make_scenario_set(township, cfg, "test", args.n_test, cfg.risk.seed_test)
    opt_set = replace(train, scenarios=train.scenarios[: args.opt_scenarios])

    with Stage("baseline monte carlo"):
        table = run_set(engine, train, n_jobs=args.jobs)
        test_table = run_set(engine, test, n_jobs=args.jobs)
        (out / "losses").mkdir(exist_ok=True)
        table.save_parquet(out / "losses" / f"{table.overlay_hash}.parquet")

    with Stage("risk metrics"):
        alpha = cfg.risk.alpha
        drivers = (
            UncertaintySource.HAZARD_INTENSITY,
            UncertaintySource.ASSET_VULNERABILITY,
            UncertaintySource.RESTORATION_TIME,
        )
        # Every headline figure is reported as a range with a confidence, not
        # as a point: the inputs do not support the precision a point implies.
        eal_lo, eal_hi = bootstrap_ci(table.weighted, eal, n=400, seed=7)
        cvar_lo, cvar_hi = bootstrap_ci(
            table.weighted, lambda x: cvar(x, alpha), n=400, seed=7
        )
        people = np.array(
            [
                sum(
                    z.population
                    for z in township.zones
                    if table.by_zone[z.id][i] > 0.0
                )
                for i in range(len(table))
            ],
            dtype=float,
        )
        amps = np.array(
            [
                engine.simulate(s, _skip_amplification=False).amplification_ratio
                for s in train.scenarios[: min(50, len(train))]
            ]
        )
        risk = {
            "alpha": alpha,
            "n_scenarios": len(table),
            "eal_ph": eal(table.weighted),
            "var95_ph": var(table.weighted, alpha),
            "cvar95_ph": cvar(table.weighted, alpha),
            "eal_ci95": [eal_lo, eal_hi],
            "cvar95_ci95": [cvar_lo, cvar_hi],
            "eal": from_interval(
                eal_lo, eal(table.weighted), eal_hi, "person-hours", drivers,
                n_samples=len(table),
            ).to_dict(),
            "cvar95": from_interval(
                cvar_lo, cvar(table.weighted, alpha), cvar_hi, "person-hours",
                drivers, n_samples=len(table),
            ).to_dict(),
            "people_affected": from_samples(people, "people", drivers).to_dict(),
            "people_affected_range": list(
                round_people(from_samples(people, "people", drivers))
            ),
            "vulnerable_eal_ph": eal(table.vulnerable),
            "service_contributions_ph": {
                s.value: v
                for s, v in portfolio_contributions(table, alpha, cfg).items()
            },
            "zone_contributions_ph": zone_contributions(table, alpha),
            "loss_samples_ph": [round(float(v), 1) for v in table.weighted],
            "amplification": {
                "mean": float(np.mean(amps)) if len(amps) else 0.0,
                "p05": float(np.percentile(amps, 5)) if len(amps) else 0.0,
                "p95": float(np.percentile(amps, 95)) if len(amps) else 0.0,
            },
        }
        risk["worst_zone"] = max(
            risk["zone_contributions_ph"], key=risk["zone_contributions_ph"].get
        )
        risk["assumptions"] = ledger_for_result(
            township, cfg, "Annual loss distribution and tail risk"
        ).to_dict()
        risk["provenance"] = provenance_summary(township)
        save_json(risk, out / "risk.json")

    with Stage("effective redundancy"):
        groups = redundancy_groups(township)
        save_json(
            {
                "system_score": round(system_score(groups), 3),
                "weak_groups": sum(1 for g in groups if g.is_weak),
                "groups": [g.to_dict() for g in groups],
            },
            out / "redundancy.json",
        )

    ranked = []
    if not args.skip_criticality:
        with Stage("criticality"):
            ranked = rank_criticality(engine, train, table, cfg, n_jobs=args.jobs)
            save_json([c.to_dict() for c in ranked], out / "criticality.json")
        with Stage("n-2 critical sets"):
            save_json(critical_sets(engine, ranked), out / "critical_sets.json")

    with Stage("single points of failure"):
        spofs = spof_mod.detect(
            township, cfg, train.scenarios, engine._base.transport, engine=engine
        )
        save_json([s.to_dict() for s in spofs], out / "spofs.json")

    priority = {c.asset_id: c.tail_criticality_ph for c in ranked}
    objective = resolve_objective(args.objective)
    logger.info("optimising for: %s", objective.label)
    with Stage("frontier"):
        front = frontier(
            engine, opt_set, catalogue, args.max_budget, cfg,
            priority=priority, n_jobs=args.jobs, objective=objective,
        )
        save_json(
            {
                "max_budget_inr": args.max_budget,
                "evaluated_on": f"train[:{len(opt_set)}]",
                "objective": objective.to_dict(),
                "steps": [s.to_dict() for s in front.steps],
            },
            out / "frontier.json",
        )

    with Stage("plan at budget"):
        plan = greedy_plan(
            engine, opt_set, catalogue, args.budget, cfg,
            priority=priority, max_zone_cvar=args.max_zone_cvar, n_jobs=args.jobs,
            objective=objective,
        )
        (out / "plans").mkdir(exist_ok=True)
        payload = plan.to_dict()
        payload["selection_frequency"] = {}
        payload["assumptions"] = ledger_for_result(
            township,
            cfg,
            f"Recommended package at {args.budget:,.0f} INR",
            assets=[
                catalogue_by_id[i].target
                for i in plan.interventions
                if i in catalogue_by_id
            ],
        ).to_dict()
        save_json(payload, out / "plans" / f"{int(args.budget)}.json")

    baseline_named = {}
    with Stage("baselines"):
        rng = np.random.default_rng(11)
        baseline_named = baselines_mod.baseline_plans(
            township, catalogue, args.budget, ranked, cfg, rng
        )
        baseline_named["optimised"] = plan.interventions
        randoms = baselines_mod.random_plans(catalogue, args.budget, rng)
        results: dict[str, dict[str, object]] = {}
        for name, ids in baseline_named.items():
            t = run_set(engine, test, Overlay(interventions=tuple(ids)), n_jobs=args.jobs)
            results[name] = {
                "eal_ph": eal(t.weighted),
                "cvar_ph": cvar(t.weighted, cfg.risk.alpha),
                "n_interventions": len(ids),
                "interventions": list(ids),
            }
        random_cvars = []
        for ids in randoms[: 5 if args.smoke else 20]:
            t = run_set(engine, test, Overlay(interventions=tuple(ids)), n_jobs=args.jobs)
            random_cvars.append(cvar(t.weighted, cfg.risk.alpha))
        results["random"] = {
            "eal_ph": 0.0,
            "cvar_ph": float(np.mean(random_cvars)),
            "ci95": [
                float(np.percentile(random_cvars, 2.5)),
                float(np.percentile(random_cvars, 97.5)),
            ],
            "n_draws": len(random_cvars),
        }
        abba = results["asset_by_asset"]["cvar_ph"]
        opt = results["optimised"]["cvar_ph"]
        save_json(
            {
                "budget_inr": args.budget,
                "evaluated_on": "test",
                "baseline_cvar_ph": cvar(test_table.weighted, cfg.risk.alpha),
                "results": results,
                "optimised_vs_asset_by_asset_pct": (
                    0.0 if abba <= 0 else round(100.0 * (abba - opt) / abba, 2)
                ),
            },
            out / "baselines.json",
        )

    with Stage("hero scenarios"):
        (out / "hero").mkdir(exist_ok=True)
        heroes = {
            name: hero_scenario(township, cfg, name, rp)
            for name, rp in HERO_RETURN_PERIODS.items()
        }
        plans_for_hero = {
            "baseline": (),
            "asset_by_asset": tuple(baseline_named.get("asset_by_asset", ())),
            "optimised": tuple(plan.interventions),
        }
        for name, scenario in heroes.items():
            for plan_id, ids in plans_for_hero.items():
                res = engine.simulate(
                    scenario, Overlay(interventions=ids), record=True
                )
                payload = res.to_dict(include_timeline=True)
                payload["scenario"] = scenario.summary()
                payload["plan_id"] = plan_id
                payload["interventions"] = list(ids)
                save_json(payload, out / "hero" / f"{name}__{plan_id}.json")

    if not args.skip_restoration:
        with Stage("restoration"):
            restoration = {}
            for name, scenario in heroes.items():
                plans = restoration_mod.compare_policies(engine, scenario, cfg)
                restoration[name] = {k: v.to_dict() for k, v in plans.items()}
            save_json(restoration, out / "restoration.json")
    else:
        save_json({"skipped": True}, out / "restoration.json")

    if not args.skip_ensemble:
        with Stage("ensemble"):
            members = ensemble_mod.generate_members(township, cfg, args.members, seed=5)
            robust = ensemble_mod.robust_plan(members, opt_set, catalogue, args.budget, cfg)
            save_json(
                {
                    "members": [m.to_dict() for m in members],
                    "robust_plan": robust.to_dict(),
                },
                out / "ensemble.json",
            )
            payload = plan.to_dict()
            payload["selection_frequency"] = robust.selection_frequency
            save_json(payload, out / "plans" / f"{int(args.budget)}.json")
        with Stage("value of information"):
            save_json(
                ensemble_mod.value_of_information(
                    township, cfg, opt_set, catalogue, args.budget,
                    n_members=max(2, args.members // 4), seed=5,
                ),
                out / "voi.json",
            )
    else:
        save_json({"members": [], "robust_plan": None, "skipped": True}, out / "ensemble.json")
        save_json({}, out / "voi.json")

    if args.skip_sensitivity:
        save_json([], out / "sensitivity.json")
        return _finish(out, township, cfg, args, catalogue, train, test, opt_set, started)

    with Stage("sensitivity"):
        tornado = []
        base_cvar = cvar(table.weighted, cfg.risk.alpha)
        base_set = set(plan.interventions)
        # Re-planning under every perturbed parameter is the expensive part of
        # this stage, so the re-plan chooses from the plan we already have plus
        # the most critical remaining candidates rather than the whole catalogue.
        ranked_ids = [c.asset_id for c in ranked]
        sensitivity_catalogue = [
            c
            for c in catalogue
            if c.id in base_set
            or c.target in ranked_ids[: args.sensitivity_candidates]
            or c.target == "global"
        ]
        logger.info(
            "sensitivity re-plan chooses from %d of %d interventions",
            len(sensitivity_catalogue),
            len(catalogue),
        )
        for label, (section, fieldname, lo, hi) in SENSITIVITY_PARAMETERS.items():
            row: dict[str, object] = {"parameter": label}
            for tag, factor in (("low", lo), ("high", hi)):
                sub = getattr(cfg, section)
                value = getattr(sub, fieldname) * factor
                variant = replace(cfg, **{section: replace(sub, **{fieldname: value})})
                variant_engine = Engine(township, variant)
                t = run_set(variant_engine, opt_set, n_jobs=args.jobs)
                row[f"{tag}_value"] = value
                row[f"{tag}_cvar_ph"] = cvar(t.weighted, variant.risk.alpha)
                variant_plan = greedy_plan(
                    variant_engine, opt_set, sensitivity_catalogue, args.budget,
                    variant, baseline=t, priority=priority, n_jobs=args.jobs,
                )
                overlap = (
                    100.0
                    * len(base_set & set(variant_plan.interventions))
                    / max(1, len(base_set))
                )
                row[f"{tag}_plan_overlap_pct"] = round(overlap, 1)
            row["base_cvar_ph"] = base_cvar
            tornado.append(row)
        tornado.sort(
            key=lambda r: -abs(float(r["high_cvar_ph"]) - float(r["low_cvar_ph"]))
        )
        save_json(tornado, out / "sensitivity.json")

    return _finish(out, township, cfg, args, catalogue, train, test, opt_set, started)


def _finish(out, township, cfg, args, catalogue, train, test, opt_set, started) -> int:
    """Write meta.json and report the wall time."""
    meta = {
        "data_version": data_version_of(township),
        "model_version": model_version(),
        "township": {
            "name": township.name,
            "assets": len(township.assets),
            "zones": len(township.zones),
            "population": sum(z.population for z in township.zones),
        },
        "config": cfg.to_dict(),
        "scenarios": {
            "train": len(train),
            "test": len(test),
            "optimiser": len(opt_set),
            "seed_train": cfg.risk.seed_train,
            "seed_test": cfg.risk.seed_test,
        },
        "catalogue_size": len(catalogue),
        "objective": args.objective,
        "budget_inr": args.budget,
        "max_budget_inr": args.max_budget,
        "hero_scenarios": sorted(HERO_RETURN_PERIODS),
        "hero_plans": list(HERO_PLANS),
        "started_at": started,
        "finished_at": time.time(),
        "elapsed_s": round(time.time() - started, 1),
        "smoke": args.smoke,
    }
    save_json(meta, out / "meta.json")
    logger.info("wrote results to %s in %.1fs", out, time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
