"""Epistemic uncertainty: plausible alternative townships, and robust planning."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from civictwin.analysis.interventions import Intervention
from civictwin.analysis.metrics import cvar
from civictwin.analysis.montecarlo import ScenarioSet, run_set
from civictwin.analysis.optimize import Plan, greedy_plan
from civictwin.config import Config
from civictwin.engine.contract import Overlay
from civictwin.engine.coordinator import Engine
from civictwin.ontology import AssetKind, LinkKind, Portfolio, Township
from civictwin.provenance import Provenance

logger = logging.getLogger(__name__)

DEFAULT_MEMBERS = 20

#: The uncertainty groups reported by value_of_information.
GROUPS = (
    "backup_hours",
    "fragility",
    "water_topology",
    "electrical_topology",
    "fibre_hosting",
    "operator_thresholds",
)

FEEDER_REPOINT_FRACTION = 0.20
TANK_REPOINT_FRACTION = 0.25
FIBRE_UNHOST_PROB = 0.30


@dataclass(slots=True)
class EnsembleMember:
    id: str
    seed: int
    township: Township
    cfg: Config
    perturbations: dict[str, float] = field(default_factory=dict)
    groups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "seed": self.seed,
            "perturbations": {k: round(v, 4) for k, v in self.perturbations.items()},
            "groups": list(self.groups),
        }


def _may_perturb(township: Township, a: str, b: str) -> bool:
    """Topology may only move where at least one end is not an observed fact."""
    pa = township.assets[a].provenance
    pb = township.assets[b].provenance
    return not (pa is Provenance.OBSERVED and pb is Provenance.OBSERVED)


def generate_members(
    township: Township,
    cfg: Config,
    n_members: int = DEFAULT_MEMBERS,
    seed: int = 0,
    groups: Sequence[str] | None = None,
) -> list[EnsembleMember]:
    """Member 0 is the baseline; the rest perturb what we are unsure about."""
    active = tuple(groups) if groups is not None else GROUPS
    out = [
        EnsembleMember(
            id="m00", seed=seed, township=township, cfg=cfg, groups=("baseline",)
        )
    ]
    for i in range(1, n_members):
        rng = np.random.default_rng(seed * 1000 + i)
        out.append(_perturb(township, cfg, f"m{i:02d}", seed * 1000 + i, rng, active))
    return out


def _perturb(
    township: Township,
    cfg: Config,
    member_id: str,
    member_seed: int,
    rng: np.random.Generator,
    groups: Sequence[str],
) -> EnsembleMember:
    assets = {}
    notes: dict[str, float] = {}
    for aid, a in township.assets.items():
        backup = a.backup_hours
        fuel = a.fuel_hours
        median = a.fragility_median_m
        if "backup_hours" in groups:
            if backup > 0:
                backup *= float(rng.uniform(0.5, 1.5))
            if fuel > 0:
                fuel *= float(rng.uniform(0.6, 1.4))
        if "fragility" in groups and median < 99.0:
            factor = float(np.clip(rng.lognormal(0.0, 0.25), 0.6, 1.7))
            median *= factor
            notes[f"fragility:{aid}"] = factor
        assets[aid] = replace(
            a,
            backup_hours=round(backup, 4),
            fuel_hours=round(fuel, 4),
            fragility_median_m=round(median, 4),
        )

    zones = []
    for z in township.zones:
        storage = z.water_storage_hours
        if "water_topology" in groups:
            storage *= float(rng.uniform(0.6, 1.5))
        zones.append(replace(z, water_storage_hours=round(storage, 4)))

    links = list(township.links)
    if "water_topology" in groups:
        links = _repoint_tanks(township, links, rng, notes)
    if "electrical_topology" in groups:
        links = _repoint_feeders(township, links, rng, notes)
    if "fibre_hosting" in groups:
        links = _unhost_fibres(township, links, rng, notes)

    member_cfg = cfg
    if "operator_thresholds" in groups:
        energy = replace(
            cfg.energy,
            deenergise_flood_fraction=float(
                np.clip(
                    cfg.energy.deenergise_flood_fraction + rng.uniform(-0.1, 0.1),
                    0.01,
                    0.95,
                )
            ),
        )
        comms = replace(
            cfg.comms,
            handover_capacity_factor=cfg.comms.handover_capacity_factor
            * float(rng.uniform(0.8, 1.3)),
        )
        member_cfg = replace(cfg, energy=energy, comms=comms)
        notes["deenergise_flood_fraction"] = energy.deenergise_flood_fraction
        notes["handover_capacity_factor"] = comms.handover_capacity_factor

    member_town = Township(
        name=township.name,
        crs_epsg=township.crs_epsg,
        origin_lonlat=township.origin_lonlat,
        assets=assets,
        links=links,
        zones=zones,
        roads=township.roads,
        nodes=township.nodes,
        crews=township.crews,
        extent_m=township.extent_m,
        seed=township.seed,
    )
    return EnsembleMember(
        id=member_id,
        seed=member_seed,
        township=member_town,
        cfg=member_cfg,
        perturbations=notes,
        groups=tuple(groups),
    )


def _repoint_feeders(
    township: Township,
    links: list,
    rng: np.random.Generator,
    notes: dict[str, float],
) -> list:
    """Move a fifth of the feeders onto their second-nearest substation."""
    from civictwin.ontology import Link

    substations = [
        a for a in township.assets.values() if a.kind is AssetKind.SUBSTATION
    ]
    feeders = sorted(
        a.id for a in township.assets.values() if a.kind is AssetKind.FEEDER
    )
    n_move = int(round(len(feeders) * FEEDER_REPOINT_FRACTION))
    if n_move <= 0:
        return links
    chosen = sorted(rng.choice(len(feeders), size=n_move, replace=False).tolist())
    out = list(links)
    for idx in chosen:
        fid = feeders[idx]
        feeder = township.assets[fid]
        ranked = sorted(
            substations, key=lambda s: math.hypot(s.x - feeder.x, s.y - feeder.y)
        )
        if len(ranked) < 2:
            continue
        second = ranked[1].id
        current = township.providers(fid, {LinkKind.POWERS})
        if not current or not _may_perturb(township, current[0], fid):
            continue
        out = [
            ln
            for ln in out
            if not (ln.kind is LinkKind.POWERS and ln.target == fid)
        ]
        out.append(Link(second, fid, LinkKind.POWERS, capacity=12.0))
        notes[f"feeder_repoint:{fid}"] = 1.0
    return out


def _repoint_tanks(
    township: Township,
    links: list,
    rng: np.random.Generator,
    notes: dict[str, float],
) -> list:
    """Move a fifth of the service reservoirs onto their second-nearest pump.

    Which pump actually feeds which reservoir is one of the least certain parts
    of a synthesised water network, and it is the uncertainty the
    `water_topology` group is meant to represent. Perturbing electrical feeders
    here instead measured the wrong thing entirely.
    """
    from civictwin.ontology import Link

    pumps = [a for a in township.assets.values() if a.kind is AssetKind.PUMP]
    tanks = sorted(
        a.id for a in township.assets.values() if a.kind is AssetKind.TANK
    )
    n_move = int(round(len(tanks) * TANK_REPOINT_FRACTION))
    if n_move <= 0 or len(pumps) < 2:
        return links
    chosen = sorted(rng.choice(len(tanks), size=n_move, replace=False).tolist())
    out = list(links)
    for idx in chosen:
        kid = tanks[idx]
        tank = township.assets[kid]
        ranked = sorted(
            pumps, key=lambda p: math.hypot(p.x - tank.x, p.y - tank.y)
        )
        second = ranked[1].id
        current = township.providers(kid, {LinkKind.SUPPLIES_WATER})
        if not current or not _may_perturb(township, current[0], kid):
            continue
        out = [
            ln
            for ln in out
            if not (ln.kind is LinkKind.SUPPLIES_WATER and ln.target == kid)
        ]
        out.append(Link(second, kid, LinkKind.SUPPLIES_WATER, capacity=400.0))
        notes[f"tank_repoint:{kid}"] = 1.0
    return out


def _unhost_fibres(
    township: Township, links: list, rng: np.random.Generator, notes: dict[str, float]
) -> list:
    """Each fibre has a chance of not really running over its bridge."""
    out = list(links)
    for aid, asset in sorted(township.assets.items()):
        if asset.kind is not AssetKind.FIBRE:
            continue
        hosts = township.providers(aid, {LinkKind.HOSTED_ON})
        if not hosts or rng.random() >= FIBRE_UNHOST_PROB:
            continue
        if not all(_may_perturb(township, h, aid) for h in hosts):
            continue
        out = [
            ln
            for ln in out
            if not (ln.kind is LinkKind.HOSTED_ON and ln.target == aid)
        ]
        notes[f"fibre_unhosted:{aid}"] = 1.0
    return out


# --------------------------------------------------------------- robustness


@dataclass(slots=True)
class RobustPlan:
    interventions: list[str]
    chosen_from_member: str
    max_regret_ph: float
    selection_frequency: dict[str, float]
    per_member_cvar: dict[str, float]
    member_plans: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "interventions": list(self.interventions),
            "chosen_from_member": self.chosen_from_member,
            "max_regret_ph": round(self.max_regret_ph, 2),
            "selection_frequency": {
                k: round(v, 4) for k, v in sorted(self.selection_frequency.items())
            },
            "per_member_cvar": {
                k: round(v, 2) for k, v in sorted(self.per_member_cvar.items())
            },
            "member_plans": {k: list(v) for k, v in sorted(self.member_plans.items())},
        }


def _cvar_of(
    member: EnsembleMember,
    scenario_set: ScenarioSet,
    interventions: Sequence[str],
    alpha: float,
) -> float:
    engine = Engine(member.township, member.cfg)
    table = run_set(engine, scenario_set, Overlay(interventions=tuple(interventions)))
    return cvar(table.weighted, alpha)


def robust_plan(
    members: Sequence[EnsembleMember],
    scenario_set: ScenarioSet,
    catalogue: Sequence[Intervention],
    budget_inr: float,
    cfg: Config,
) -> RobustPlan:
    """Minimax-regret plan across the ensemble, plus per-candidate frequencies."""
    alpha = cfg.risk.alpha
    member_plans: dict[str, list[str]] = {}
    own_cvar: dict[str, float] = {}
    for member in members:
        engine = Engine(member.township, member.cfg)
        plan = greedy_plan(
            engine, scenario_set, catalogue, budget_inr, member.cfg
        )
        member_plans[member.id] = plan.interventions
        own_cvar[member.id] = plan.cvar_after

    frequency: dict[str, float] = {c.id: 0.0 for c in catalogue}
    for chosen in member_plans.values():
        for iid in chosen:
            frequency[iid] += 1.0 / len(members)

    regrets: dict[str, float] = {}
    cross: dict[tuple[str, str], float] = {}
    for plan_id, interventions in member_plans.items():
        worst = 0.0
        for member in members:
            value = _cvar_of(member, scenario_set, interventions, alpha)
            cross[(plan_id, member.id)] = value
            worst = max(worst, value - own_cvar[member.id])
        regrets[plan_id] = worst

    best_member = min(sorted(regrets), key=lambda m: regrets[m])
    return RobustPlan(
        interventions=member_plans[best_member],
        chosen_from_member=best_member,
        max_regret_ph=regrets[best_member],
        selection_frequency=frequency,
        per_member_cvar=own_cvar,
        member_plans=member_plans,
    )


def value_of_information(
    township: Township,
    cfg: Config,
    scenario_set: ScenarioSet,
    catalogue: Sequence[Intervention],
    budget_inr: float,
    n_members: int = 6,
    seed: int = 0,
) -> dict[str, dict[str, float]]:
    """What each uncertainty would be worth resolving, in person-hours of tail loss."""
    alpha = cfg.risk.alpha
    baseline_cvar = cvar(
        run_set(Engine(township, cfg), scenario_set).weighted, alpha
    )
    out: dict[str, dict[str, float]] = {}
    for group in GROUPS:
        members = generate_members(
            township, cfg, n_members=n_members, seed=seed, groups=(group,)
        )
        robust = robust_plan(members, scenario_set, catalogue, budget_inr, cfg)
        informed = float(np.mean([robust.per_member_cvar[m.id] for m in members]))
        uninformed = float(
            np.mean(
                [
                    _cvar_of(m, scenario_set, robust.interventions, alpha)
                    for m in members
                ]
            )
        )
        voi = max(0.0, uninformed - informed)
        out[group] = {
            "voi_ph": round(voi, 2),
            "voi_pct_of_baseline_cvar": (
                0.0 if baseline_cvar <= 0 else round(100.0 * voi / baseline_cvar, 3)
            ),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["voi_ph"]))
