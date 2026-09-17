"""Which assets matter, and why: criticality, systemic ratio, and N-2 sets."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Sequence

import numpy as np

from civictwin.analysis.metrics import cvar, cvar_indices, eal
from civictwin.analysis.montecarlo import LossTable, ScenarioSet
from civictwin.config import Config
from civictwin.engine.contract import HazardScenario, Overlay
from civictwin.engine.coordinator import Engine
from civictwin.ontology import AssetKind, LinkKind, Portfolio, Township

logger = logging.getLogger(__name__)

#: Beyond the tail, only this many other affected scenarios are re-simulated.
NON_TAIL_SAMPLE_CAP = 200
SYSTEMIC_FLAG_RATIO = 2.0
#: Below this the systemic ratio's denominator is clamped and the ratio stops
#: being meaningful (spec 03 section 3.2).
DENOMINATOR_FLOOR = 1.0
MAX_EXPLANATIONS = 3
N2_CANDIDATES = 25
N2_KEEP = 50

CHAIN_KINDS = {LinkKind.POWERS, LinkKind.SUPPLIES_WATER, LinkKind.BACKHAULS}


def sort_zone_ids(zone_ids: Iterable[str]) -> list[str]:
    """Sort zone ids numerically where they look numeric, textually otherwise.

    `int(zone_id[1:])` assumes every zone is `Z` followed by digits, which is
    true of the synthetic township and need not be of a real one.
    """
    def key(zone_id: str) -> tuple[int, float, str]:
        suffix = zone_id[1:]
        return (0, float(suffix), "") if suffix.isdigit() else (1, 0.0, zone_id)

    return sorted(zone_ids, key=key)
#: Repair takes far longer when every route to the asset crosses a river.
BRIDGE_ACCESS_PENALTY = 2.0
#: And longer still when no depot can reach it by road at all.
UNREACHABLE_PENALTY = 4.0


@dataclass(slots=True)
class SystemicRisk:
    """Why an asset is systemically important, factor by factor.

    Centrality alone finds the obviously well-connected nodes. This finds the
    assets that are not especially likely to fail, and not especially central,
    but whose failure would be both wide and slow to undo.
    """

    failure_probability: float
    failure_impact: float
    dependency_concentration: float
    recovery_difficulty: float

    @property
    def score(self) -> float:
        return (
            self.failure_probability
            * self.failure_impact
            * self.dependency_concentration
            * self.recovery_difficulty
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "failure_probability": round(self.failure_probability, 4),
            "failure_impact": round(self.failure_impact, 4),
            "dependency_concentration": round(self.dependency_concentration, 4),
            "recovery_difficulty": round(self.recovery_difficulty, 4),
            "score": round(self.score, 6),
        }


@dataclass(slots=True)
class AssetCriticality:
    asset_id: str
    portfolio: Portfolio
    standalone_loss_ph: float
    annual_failure_prob: float
    tail_criticality_ph: float
    eal_criticality_ph: float
    systemic_ratio: float
    served_population: int
    recovery_criticality_h: float
    expected_direct_ph: float = 0.0
    #: Topology only: how much of the town sits downstream of this asset.
    structural_importance: float = 0.0
    #: Simulated: how much service is actually lost when it goes.
    functional_importance: float = 0.0
    systemic_risk: SystemicRisk | None = None
    explanation: list[str] = field(default_factory=list)
    scenarios_resimulated: int = 0
    sampled: bool = False

    @property
    def systemic(self) -> bool:
        """Does this asset matter far more in the tail than on its own?

        The ratio divides by `max(expected_direct, 1.0)`, so an asset with
        almost no standalone loss -- a stormwater pump, which does nothing at
        all when there is no rain -- would show an enormous ratio purely
        because the denominator was clamped. Those are not systemic assets,
        they are assets the standalone measure cannot see, so the flag also
        requires the denominator to be real.
        """
        return (
            self.systemic_ratio > SYSTEMIC_FLAG_RATIO
            and self.expected_direct_ph >= DENOMINATOR_FLOOR
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "portfolio": self.portfolio.value,
            "standalone_loss_ph": round(self.standalone_loss_ph, 2),
            "annual_failure_prob": round(self.annual_failure_prob, 5),
            "tail_criticality_ph": round(self.tail_criticality_ph, 2),
            "eal_criticality_ph": round(self.eal_criticality_ph, 2),
            "systemic_ratio": round(self.systemic_ratio, 4),
            "systemic": self.systemic,
            "served_population": self.served_population,
            "recovery_criticality_h": self.recovery_criticality_h,
            "expected_direct_ph": round(self.expected_direct_ph, 2),
            "structural_importance": round(self.structural_importance, 4),
            "functional_importance": round(self.functional_importance, 4),
            "systemic_risk": (
                None if self.systemic_risk is None else self.systemic_risk.to_dict()
            ),
            "explanation": list(self.explanation),
            "scenarios_resimulated": self.scenarios_resimulated,
            "sampled": self.sampled,
        }


def explain_chains(
    township: Township, asset_id: str, limit: int = MAX_EXPLANATIONS
) -> list[str]:
    """Human-readable dependency chains from an asset to the zones it reaches."""
    zone_by_provider: dict[str, list[str]] = {}
    for z in township.zones:
        for provider in township.zone_providers(z.id):
            zone_by_provider.setdefault(provider, []).append(z.id)

    reach = township.downstream(asset_id, CHAIN_KINDS)
    chains: list[tuple[int, str]] = []
    for endpoint in sorted(reach | {asset_id}):
        zones = zone_by_provider.get(endpoint)
        if not zones:
            continue
        path = _shortest_chain(township, asset_id, endpoint)
        if path is None:
            continue
        population = sum(township.zone(z).population for z in zones)
        zone_label = ", ".join(sort_zone_ids(zones)[:4])
        service = _service_of(township, endpoint)
        chains.append(
            (
                population,
                f"{' → '.join(path)} → {zone_label} ({population:,} people, {service})",
            )
        )
    chains.sort(key=lambda c: (-c[0], c[1]))
    return [text for _pop, text in chains[:limit]]


def _service_of(township: Township, asset_id: str) -> str:
    kind = township.assets[asset_id].kind.value
    return {
        "transformer": "energy",
        "feeder": "energy",
        "substation": "energy",
        "tank": "water",
        "tower": "comms",
    }.get(kind, township.assets[asset_id].portfolio.value)


def _shortest_chain(
    township: Township, source: str, target: str
) -> list[str] | None:
    """Breadth-first path from source to target along physical dependencies."""
    if source == target:
        return [source]
    seen = {source}
    queue: list[list[str]] = [[source]]
    while queue:
        path = queue.pop(0)
        for nxt in sorted(township.dependents(path[-1], CHAIN_KINDS)):
            if nxt in seen:
                continue
            if nxt == target:
                return path + [nxt]
            seen.add(nxt)
            queue.append(path + [nxt])
    return None


def asset_criticality(
    engine: Engine,
    scenario_set: ScenarioSet,
    table: LossTable,
    cfg: Config,
    asset_id: str,
    rng: np.random.Generator,
    n_jobs: int = 1,
) -> AssetCriticality:
    """Criticality of one asset, by making it invulnerable where it mattered."""
    town = engine.township
    asset = town.assets[asset_id]
    standalone = engine.standalone_loss(asset_id)
    affected = [i for i, dmg in enumerate(table.damaged) if asset_id in dmg]
    n = len(table)
    prob = len(affected) / n if n else 0.0

    base = AssetCriticality(
        asset_id=asset_id,
        portfolio=asset.portfolio,
        standalone_loss_ph=standalone,
        annual_failure_prob=prob,
        tail_criticality_ph=0.0,
        eal_criticality_ph=0.0,
        systemic_ratio=0.0,
        served_population=town.served_population(asset_id),
        recovery_criticality_h=0.0,
        explanation=explain_chains(town, asset_id),
    )
    if not affected:
        return base

    tail = set(int(i) for i in cvar_indices(table.weighted, cfg.risk.alpha))
    tail_affected = sorted(set(affected) & tail)
    all_others = [i for i in affected if i not in tail]
    others = all_others
    if len(all_others) > NON_TAIL_SAMPLE_CAP:
        others = sorted(
            rng.choice(all_others, size=NON_TAIL_SAMPLE_CAP, replace=False).tolist()
        )
        base.sampled = True
    subset = sorted(set(tail_affected) | set(others))

    overlay = Overlay(invulnerable=(asset_id,))
    modified = table.weighted.copy()
    losses = engine.simulate_batch(
        [(scenario_set.scenarios[i], overlay) for i in subset], n_jobs=n_jobs
    )
    for i, value in zip(subset, losses):
        modified[i] = value
    base.scenarios_resimulated = len(subset)

    # CVaR only looks at the tail, and every affected tail scenario was
    # re-simulated, so this is exact.
    base.tail_criticality_ph = cvar(table.weighted, cfg.risk.alpha) - cvar(
        modified, cfg.risk.alpha
    )

    # EAL looks at all of them, and most were not re-simulated. Taking the mean
    # of a part-updated array counts the un-sampled scenarios as having no
    # improvement at all, which under-reports the criticality by roughly the
    # sampling ratio. Each sampled non-tail scenario therefore stands in for
    # N_others / n_sampled of its peers (Horvitz-Thompson).
    tail_delta = sum(
        table.weighted[i] - modified[i] for i in tail_affected
    )
    sampled_delta = sum(table.weighted[i] - modified[i] for i in others)
    inflation = (len(all_others) / len(others)) if others else 0.0
    base.eal_criticality_ph = (
        tail_delta + sampled_delta * inflation
    ) / max(1, n)
    expected_direct = prob * standalone
    base.expected_direct_ph = expected_direct
    base.systemic_ratio = base.tail_criticality_ph / max(
        expected_direct, DENOMINATOR_FLOOR
    )
    return base


def dependency_concentration(township: Township, asset_id: str) -> float:
    """How much of what depends on this asset has nowhere else to turn.

    One if every dependent has this asset as its only provider of that kind of
    supply; lower as alternatives exist. An asset that feeds zones directly and
    has no downstream assets counts as fully concentrated, because a zone has
    no alternative at all.
    """
    dependents = township.dependents(asset_id)
    if not dependents:
        # It supplies zones directly. A zone covered by two towers still has
        # somewhere to turn; a zone on one transformer does not.
        shares = [
            1.0 / max(1, len(_zone_alternatives(township, z.id, asset_id)))
            for z in township.zones
            if asset_id in township.zone_providers(z.id)
        ]
        return sum(shares) / len(shares) if shares else 0.0
    shares: list[float] = []
    for link in township.links:
        if link.source != asset_id:
            continue
        providers = township.providers(link.target, {link.kind})
        shares.append(1.0 / max(1, len(providers)))
    return sum(shares) / len(shares) if shares else 0.0


def _zone_alternatives(township: Township, zone_id: str, asset_id: str) -> list[str]:
    """The assets playing the same role for a zone as `asset_id` does."""
    zone = township.zone(zone_id)
    if asset_id in zone.towers:
        return list(zone.towers)
    return [asset_id]


def recovery_difficulty(
    township: Township, asset_id: str, cfg: Config
) -> float:
    """Hours to put the asset back, inflated when crews cannot easily reach it."""
    import math

    asset = township.assets[asset_id]
    worst_multiplier = max(cfg.damage.repair_multipliers)
    hours = asset.repair_hours_base * worst_multiplier
    depots = [
        a for a in township.assets.values() if a.kind is AssetKind.DEPOT
    ]
    reachable = any(
        math.isfinite(township.shortest_path_hours(d.node, asset.node))
        for d in depots
    )
    if not reachable:
        # No depot can get to it at all. That is the worst case for recovery,
        # not the easiest -- returning 1.0 hour made cut-off assets look like
        # the quickest things in the township to put right.
        return hours * UNREACHABLE_PENALTY
    bridges = {
        e.id
        for e in township.roads.values()
        if e.host_asset
        and township.assets[e.host_asset].kind is AssetKind.BRIDGE
    }
    needs_bridge = all(
        not math.isfinite(
            township.shortest_path_hours(d.node, asset.node, blocked=bridges)
        )
        for d in depots
    )
    return hours * (BRIDGE_ACCESS_PENALTY if needs_bridge else 1.0)


def attach_systemic_risk(
    township: Township, rows: Sequence[AssetCriticality], cfg: Config
) -> None:
    """Fill in the systemic-risk factors and the structural/functional split.

    Each factor is normalised across the township so the product is comparable
    between assets; the raw components are kept so the score can be explained
    rather than asserted.
    """
    total_population = max(1, sum(z.population for z in township.zones))
    raw_difficulty = {
        row.asset_id: recovery_difficulty(township, row.asset_id, cfg) for row in rows
    }
    max_difficulty = max(raw_difficulty.values(), default=1.0) or 1.0
    max_standalone = max((r.standalone_loss_ph for r in rows), default=1.0) or 1.0
    max_tail = max((r.tail_criticality_ph for r in rows), default=1.0) or 1.0

    for row in rows:
        row.structural_importance = min(
            1.0, township.served_population(row.asset_id) / total_population
        )
        row.functional_importance = min(
            1.0, max(0.0, row.tail_criticality_ph) / max_tail
        )
        row.systemic_risk = SystemicRisk(
            failure_probability=row.annual_failure_prob,
            failure_impact=min(1.0, row.standalone_loss_ph / max_standalone),
            dependency_concentration=dependency_concentration(
                township, row.asset_id
            ),
            recovery_difficulty=raw_difficulty[row.asset_id] / max_difficulty,
        )


def rank_criticality(
    engine: Engine,
    scenario_set: ScenarioSet,
    table: LossTable,
    cfg: Config,
    seed: int = 0,
    n_jobs: int = 1,
) -> list[AssetCriticality]:
    """Criticality for every asset, sorted by tail criticality descending."""
    rng = np.random.default_rng(seed)
    out = [
        asset_criticality(engine, scenario_set, table, cfg, aid, rng, n_jobs=n_jobs)
        for aid in sorted(engine.township.assets)
    ]
    attach_systemic_risk(engine.township, out, cfg)
    out.sort(key=lambda c: (-c.tail_criticality_ph, c.asset_id))
    return out


def n2_candidate_pairs(
    township: Township,
    ranked: Sequence[AssetCriticality],
    candidates: int,
) -> list[tuple[str, str]]:
    """Pairs worth testing together, chosen for structure as well as rank.

    Ranking each asset on its own and pairing the top of the list finds pairs
    that were already individually obvious. The interesting N-2 cases are the
    ones where two assets cover for each other: they serve the same zones, or
    they sit on either side of the same cut. Those are included even when
    neither is near the top of the list.
    """
    top = [c.asset_id for c in ranked[:candidates]]
    pairs: set[tuple[str, str]] = set()
    for a, b in combinations(sorted(top), 2):
        pairs.add((a, b))

    # Assets that cover the same zones are natural pairs: losing both is what
    # removes the cover, and neither alone need look dangerous.
    zones_of: dict[str, frozenset[str]] = {}
    for aid in township.assets:
        zones_of[aid] = frozenset(
            z.id
            for z in township.zones
            if aid in township.zone_dependency_closure(z.id)
        )
    interesting = [c.asset_id for c in ranked[: candidates * 2]]
    for a, b in combinations(sorted(interesting), 2):
        if not zones_of[a] or not zones_of[b]:
            continue
        overlap = zones_of[a] & zones_of[b]
        if not overlap:
            continue
        shared = township.upstream(a) & township.upstream(b)
        co_covering = len(overlap) >= max(1, min(len(zones_of[a]), len(zones_of[b])))
        if co_covering or shared:
            pairs.add((a, b) if a < b else (b, a))

    # Every pair of river crossings, since together they cut the town in half.
    structures = sorted(
        a.id
        for a in township.assets.values()
        if a.kind in (AssetKind.BRIDGE, AssetKind.UNDERPASS)
    )
    for a, b in combinations(structures, 2):
        pairs.add((a, b))
    return sorted(pairs)


def critical_sets(
    engine: Engine,
    ranked: Sequence[AssetCriticality],
    candidates: int = N2_CANDIDATES,
    keep: int = N2_KEEP,
) -> list[dict[str, object]]:
    """N-2 search: pairs whose joint failure is worse than the sum of its parts."""
    town = engine.township
    blank = HazardScenario(
        id="n2",
        seed=0,
        rain_mm=0.0,
        field_seed=0,
        onset_hour=0,
        asset_draws={a: 1.0 for a in town.assets},
    )
    rows: list[dict[str, object]] = []
    for a, b in n2_candidate_pairs(town, ranked, candidates):
        joint = engine.simulate(
            blank,
            Overlay(forced_failures=(a, b), flood_enabled=False),
            _skip_amplification=True,
        ).weighted_loss_ph
        individual = engine.standalone_loss(a) + engine.standalone_loss(b)
        rows.append(
            {
                "assets": [a, b],
                "joint_ph": round(joint, 2),
                "sum_individual_ph": round(individual, 2),
                "synergy_ph": round(joint - individual, 2),
            }
        )
    rows.sort(key=lambda r: -float(r["synergy_ph"]))
    return rows[:keep]
