"""Finding hidden single points of failure behind apparent redundancy."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from civictwin.config import Config
from civictwin.engine.contract import HazardScenario
from civictwin.analysis.criticality import sort_zone_ids
from civictwin.engine.damage import exceedance_probability
from civictwin.engine.hazard import HazardModel
from civictwin.engine.layers.transport import TransportLayer
from civictwin.ontology import AssetKind, LinkKind, Service, Township

logger = logging.getLogger(__name__)

SpofKind = Literal["shared_dependency", "colocation", "common_cause", "recovery"]

COMMON_CAUSE_THRESHOLD = 0.25
#: Scenarios sampled when measuring how often a group fails together.
COMMON_CAUSE_SCENARIOS = 120
MODERATE_STATE_INDEX = 1  # index of MODERATE inside DamageConfig.state_multipliers
TOP_N = 20


@dataclass(slots=True)
class Spof:
    kind: SpofKind
    shared_asset: str | None
    redundant_group: list[str]
    affected_zones: list[str]
    affected_population: int
    service: Service
    design_storm_failure_prob: float
    explanation: str
    #: Loss(system without this asset) - Loss(normal system), in person-hours.
    #: Measured by simulation, not inferred from the topology.
    counterfactual_ph: float = 0.0
    #: The same comparison under the design storm rather than in still weather.
    counterfactual_storm_ph: float = 0.0
    #: People who lose a service in the counterfactual but not in the baseline.
    counterfactual_people: int = 0

    @property
    def rank_score(self) -> float:
        """Expected harm: what it would cost, times how likely it is.

        Ranked on the measured counterfactual where one exists, and on the
        population at risk where it does not, so a structurally alarming but
        practically harmless finding does not outrank a real one.
        """
        harm = self.counterfactual_ph or float(self.affected_population)
        return harm * self.design_storm_failure_prob

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "shared_asset": self.shared_asset,
            "redundant_group": list(self.redundant_group),
            "affected_zones": list(self.affected_zones),
            "affected_population": self.affected_population,
            "service": self.service.value,
            "design_storm_failure_prob": round(self.design_storm_failure_prob, 4),
            "explanation": self.explanation,
            "counterfactual_ph": round(self.counterfactual_ph, 1),
            "counterfactual_storm_ph": round(self.counterfactual_storm_ph, 1),
            "counterfactual_people": self.counterfactual_people,
            "rank_score": round(self.rank_score, 1),
        }


def design_storm(scenarios: Sequence[HazardScenario]) -> HazardScenario:
    """The most extreme storm in a scenario set: our design event."""
    return max(scenarios, key=lambda s: s.rain_mm)


JointFailure = Callable[[Sequence[str]], float]


def empirical_joint_failure(
    township: Township, cfg: Config, scenarios: Sequence[HazardScenario]
) -> JointFailure:
    """Measure how often a whole group fails together, across real draws.

    Multiplying marginal probabilities assumes the members are independent,
    which is exactly the assumption a common-cause finding exists to challenge:
    two towers in the same basin flood in the same storms. This counts the
    scenarios in which all of them fail, so the correlation is carried by the
    shared hazard field rather than assumed away.
    """
    sample = list(scenarios[:COMMON_CAUSE_SCENARIOS])
    if not sample:
        return lambda members: 0.0

    mult = cfg.damage.state_multipliers[MODERATE_STATE_INDEX]
    fails: list[set[str]] = []
    for scenario in sample:
        model = HazardModel(township, scenario, cfg)
        failed: set[str] = set()
        for aid, asset in township.assets.items():
            depth = model.max_depth(aid)
            if depth <= 0.0:
                continue
            probability = exceedance_probability(
                depth, asset.fragility_median_m, asset.fragility_beta, mult
            )
            if probability > scenario.asset_draws.get(aid, 1.0):
                failed.add(aid)
        fails.append(failed)

    def joint(members: Sequence[str]) -> float:
        """P(all of them fail | at least one of them fails).

        The unconditional rate answers "how often does this group fail
        together", which is dominated by how often it storms at all. The
        conditional rate answers the question a common-cause finding is
        actually asking: when one of these goes, does the rest of the group go
        with it? Independent members give a low number however often they fail;
        members sharing one basin give something close to one.
        """
        wanted = set(members)
        any_fail = sum(1 for failed in fails if wanted & failed)
        if not any_fail:
            return 0.0
        all_fail = sum(1 for failed in fails if wanted <= failed)
        return all_fail / any_fail

    return joint


class _Fragility:
    """P(at least moderate damage) for every asset under the design storm."""

    def __init__(self, township: Township, scenario: HazardScenario, cfg: Config) -> None:
        model = HazardModel(township, scenario, cfg)
        mult = cfg.damage.state_multipliers[MODERATE_STATE_INDEX]
        self.prob: dict[str, float] = {}
        for aid, asset in township.assets.items():
            depth = model.max_depth(aid)
            self.prob[aid] = exceedance_probability(
                depth, asset.fragility_median_m, asset.fragility_beta, mult
            )

    def failure_prob(self, asset_id: str, township: Township) -> float:
        """Probability the asset is lost, directly or through its host."""
        direct = self.prob.get(asset_id, 0.0)
        for host in township.providers(asset_id, {LinkKind.HOSTED_ON}):
            direct = 1.0 - (1.0 - direct) * (1.0 - self.failure_prob(host, township))
        return direct


def _redundant_groups(
    township: Township, transport: TransportLayer, cfg: Config
) -> list[tuple[Service, str, list[str]]]:
    """(service, zone, members) for every zone-level group that looks redundant."""
    groups: list[tuple[Service, str, list[str]]] = []
    access_h = cfg.sim.health_access_minutes / 60.0
    hospitals = [a for a in township.assets.values() if a.kind is AssetKind.HOSPITAL]
    for z in township.zones:
        if len(z.towers) > 1:
            groups.append((Service.COMMS, z.id, sorted(z.towers)))
        reachable = sorted(
            h.id
            for h in hospitals
            if transport.travel_time(z.node, h.node) <= access_h
        )
        if len(reachable) > 1:
            groups.append((Service.HEALTH, z.id, reachable))
        tanks = sorted({z.tank})
        if len(tanks) > 1:  # pragma: no cover - the synthetic town has one each
            groups.append((Service.WATER, z.id, tanks))
    return groups


def detect(
    township: Township,
    cfg: Config,
    scenarios: Sequence[HazardScenario],
    transport: TransportLayer,
    engine: object | None = None,
) -> list[Spof]:
    """Run all four detectors, then measure, deduplicate and rank.

    The detectors are structural: they find what *looks* like a single point of
    failure. Passing an engine adds the counterfactual test that says whether it
    actually is one -- the difference between the system with the asset and the
    system without it -- so nothing is called a single point of failure on the
    strength of the topology alone.
    """
    storm = design_storm(scenarios)
    frag = _Fragility(township, storm, cfg)
    groups = _redundant_groups(township, transport, cfg)
    found: list[Spof] = []

    found.extend(_shared_dependency(township, groups, frag))
    found.extend(_colocation(township, groups, frag))
    found.extend(
        _common_cause(
            township,
            groups,
            frag,
            empirical_joint_failure(township, cfg, scenarios),
        )
    )
    found.extend(_recovery(township, transport, frag))
    ranked = _dedupe_and_rank(township, found)
    if engine is not None:
        measure_counterfactuals(ranked, township, engine, storm)
        ranked.sort(
            key=lambda s: (-s.rank_score, s.kind, s.shared_asset or "")
        )
    return ranked


def measure_counterfactuals(
    spofs: Sequence[Spof], township: Township, engine: object, storm: HazardScenario
) -> None:
    """Fill in what each candidate single point of failure actually costs.

    Impact(asset) = Loss(system without it) - Loss(system as it is), measured
    twice: in still weather, which isolates the dependency, and under the design
    storm, which shows what it adds on top of everything else going wrong.
    """
    from civictwin.engine.contract import Overlay

    baseline_storm = engine.simulate(  # type: ignore[attr-defined]
        storm, Overlay(), _skip_amplification=True
    ).weighted_loss_ph
    for spof in spofs:
        asset = spof.shared_asset
        if asset is None or asset not in township.assets:
            continue
        # In still weather nothing fails, so forcing the asset down measures
        # what it carries on its own.
        spof.counterfactual_ph = engine.standalone_loss(asset)  # type: ignore[attr-defined]

        # Under the design storm the asset is already failing, so forcing it to
        # fail again measures nothing. The question that has an answer is the
        # other way round: how much of the storm's damage goes away if this one
        # asset holds.
        protected = engine.simulate(  # type: ignore[attr-defined]
            storm,
            Overlay(invulnerable=(asset,)),
            _skip_amplification=True,
        ).weighted_loss_ph
        spof.counterfactual_storm_ph = max(0.0, baseline_storm - protected)
        spof.counterfactual_people = (
            spof.affected_population if spof.counterfactual_ph > 0 else 0
        )


def _shared_dependency(
    township: Township,
    groups: Sequence[tuple[Service, str, list[str]]],
    frag: _Fragility,
) -> list[Spof]:
    out: list[Spof] = []
    for service, zone_id, members in groups:
        closures = [township.upstream(m) | {m} for m in members]
        shared = set.intersection(*closures) - set(members)
        for asset in sorted(shared):
            out.append(
                Spof(
                    kind="shared_dependency",
                    shared_asset=asset,
                    redundant_group=list(members),
                    affected_zones=[zone_id],
                    affected_population=township.zone(zone_id).population,
                    service=service,
                    design_storm_failure_prob=frag.failure_prob(asset, township),
                    explanation=(
                        f"{', '.join(members)} look independent for {service.value} in "
                        f"{zone_id}, but every one of them depends on "
                        f"{asset} ({township.assets[asset].name})."
                    ),
                )
            )
    return out


def _colocation(
    township: Township,
    groups: Sequence[tuple[Service, str, list[str]]],
    frag: _Fragility,
) -> list[Spof]:
    out: list[Spof] = []
    for service, zone_id, members in groups:
        host_sets = [_hosts_of(township, m) for m in members]
        shared_hosts = set.intersection(*host_sets) if host_sets else set()
        for host in sorted(shared_hosts):
            out.append(
                Spof(
                    kind="colocation",
                    shared_asset=host,
                    redundant_group=list(members),
                    affected_zones=[zone_id],
                    affected_population=township.zone(zone_id).population,
                    service=service,
                    design_storm_failure_prob=frag.failure_prob(host, township),
                    explanation=(
                        f"{', '.join(members)} are all carried by {host} "
                        f"({township.assets[host].name}); one structure takes them "
                        "all out together."
                    ),
                )
            )
    # groups of provider assets, not just zone-level services: fibre pairs etc.
    by_target: dict[tuple[str, ...], list[str]] = {}
    for asset in township.assets.values():
        hosts = tuple(sorted(_hosts_of(township, asset.id)))
        if hosts:
            by_target.setdefault(hosts, []).append(asset.id)
    for hosts, members in sorted(by_target.items()):
        if len(members) < 2:
            continue
        for host in hosts:
            zones = sorted(
                {
                    z.id
                    for z in township.zones
                    if any(m in township.zone_dependency_closure(z.id) for m in members)
                }
            )
            out.append(
                Spof(
                    kind="colocation",
                    shared_asset=host,
                    redundant_group=sorted(members),
                    affected_zones=zones,
                    affected_population=sum(
                        township.zone(z).population for z in zones
                    ),
                    service=Service.COMMS,
                    design_storm_failure_prob=frag.failure_prob(host, township),
                    explanation=(
                        f"{', '.join(sorted(members))} are a redundant pair on paper, "
                        f"but both are carried by {host} "
                        f"({township.assets[host].name})."
                    ),
                )
            )
    return out


def _hosts_of(township: Township, asset_id: str, _guard: frozenset[str] = frozenset()) -> set[str]:
    """Transitive HOSTED_ON providers of an asset."""
    out: set[str] = set()
    for host in township.providers(asset_id, {LinkKind.HOSTED_ON}):
        if host in _guard:
            continue
        out.add(host)
        out |= _hosts_of(township, host, _guard | {asset_id})
    return out


def _common_cause(
    township: Township,
    groups: Sequence[tuple[Service, str, list[str]]],
    frag: _Fragility,
    joint_probability: "JointFailure | None" = None,
) -> list[Spof]:
    out: list[Spof] = []
    for service, zone_id, members in groups:
        if joint_probability is not None:
            joint = joint_probability(members)
        else:
            joint = 1.0
            for m in members:
                joint *= frag.failure_prob(m, township)
        if joint <= COMMON_CAUSE_THRESHOLD:
            continue
        out.append(
            Spof(
                kind="common_cause",
                shared_asset=None,
                redundant_group=list(members),
                affected_zones=[zone_id],
                affected_population=township.zone(zone_id).population,
                service=service,
                design_storm_failure_prob=joint,
                explanation=(
                    f"When any of {', '.join(members)} floods, all of them do "
                    f"{joint:.0%} of the time: the redundancy shares one hazard, "
                    "not one asset."
                ),
            )
        )
    return out


def _recovery(
    township: Township, transport: TransportLayer, frag: _Fragility
) -> list[Spof]:
    """Structures without which a depot cannot reach an asset to repair it.

    A single articulation edge is the obvious case. The interesting one here is
    a cut of size two: with every depot on one bank, the pair of river bridges
    is what stands between the crews and half the town, and neither bridge
    alone would show up as a single point of failure.
    """
    out: list[Spof] = []
    depots = {
        a.portfolio: a
        for a in township.assets.values()
        if a.kind is AssetKind.DEPOT
    }
    structures = _structure_edges(township)
    for asset in sorted(township.assets.values(), key=lambda a: a.id):
        depot = depots.get(asset.portfolio)
        if depot is None or asset.kind is AssetKind.DEPOT:
            continue
        cut = _recovery_cut(township, depot.node, asset.node, structures)
        if not cut:
            continue
        zones = sorted(
            z.id
            for z in township.zones
            if asset.id in township.zone_dependency_closure(z.id)
        )
        population = sum(township.zone(z).population for z in zones)
        joint = 1.0
        for host in cut:
            joint *= frag.failure_prob(host, township) if host in township.assets else 1.0
        for host in cut:
            others = [h for h in cut if h != host]
            with_others = (
                f" together with {', '.join(others)}" if others else ""
            )
            out.append(
                Spof(
                    kind="recovery",
                    shared_asset=host,
                    redundant_group=[asset.id],
                    affected_zones=zones,
                    affected_population=population,
                    service=Service.MOBILITY,
                    design_storm_failure_prob=joint,
                    explanation=(
                        f"Depot {depot.id} can only reach {asset.id} across {host}"
                        f"{with_others}; lose that and {asset.id} cannot be repaired "
                        "inside the horizon."
                    ),
                )
            )
    return out


def _structure_edges(township: Township) -> dict[str, set[str]]:
    """host asset id -> the road edges it carries."""
    out: dict[str, set[str]] = {}
    for edge in township.roads.values():
        if edge.host_asset:
            out.setdefault(edge.host_asset, set()).add(edge.id)
    return out


def _recovery_cut(
    township: Township,
    src: int,
    dst: int,
    structures: dict[str, set[str]],
) -> list[str]:
    """Smallest set of host structures (size 1, then 2) that isolates dst."""
    for host, edges in sorted(structures.items()):
        if math.isinf(township.shortest_path_hours(src, dst, blocked=edges)):
            return [host]
    names = sorted(structures)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            blocked = structures[a] | structures[b]
            if math.isinf(township.shortest_path_hours(src, dst, blocked=blocked)):
                return [a, b]
    return []


def _gating_edges(township: Township, src: int, dst: int) -> list[str]:
    """Edges on the shortest path whose removal disconnects src from dst."""
    path = _shortest_path_edges(township, src, dst)
    return [
        eid
        for eid in path
        if math.isinf(township.shortest_path_hours(src, dst, blocked={eid}))
    ]


def _shortest_path_edges(township: Township, src: int, dst: int) -> list[str]:
    import heapq

    adjacency = township.road_graph()
    dist: dict[int, float] = {src: 0.0}
    prev: dict[int, tuple[int, str]] = {}
    pq: list[tuple[float, int]] = [(0.0, src)]
    while pq:
        d, n = heapq.heappop(pq)
        if n == dst:
            break
        if d > dist.get(n, math.inf):
            continue
        for nb, eid, tt in adjacency.get(n, ()):
            nd = d + tt
            if nd < dist.get(nb, math.inf):
                dist[nb] = nd
                prev[nb] = (n, eid)
                heapq.heappush(pq, (nd, nb))
    if dst not in prev and dst != src:
        return []
    edges: list[str] = []
    cur = dst
    while cur != src:
        cur, eid = prev[cur]
        edges.append(eid)
    return list(reversed(edges))


def _dedupe_and_rank(township: Township, found: Sequence[Spof]) -> list[Spof]:
    """Merge by (kind, shared asset, service), summing the population at risk."""
    merged: dict[tuple[str, str | None, str], Spof] = {}
    for spof in found:
        key = (spof.kind, spof.shared_asset, spof.service.value)
        existing = merged.get(key)
        if existing is None:
            merged[key] = Spof(
                kind=spof.kind,
                shared_asset=spof.shared_asset,
                redundant_group=list(spof.redundant_group),
                affected_zones=list(spof.affected_zones),
                affected_population=spof.affected_population,
                service=spof.service,
                design_storm_failure_prob=spof.design_storm_failure_prob,
                explanation=spof.explanation,
            )
            continue
        zones = sort_zone_ids(
            set(existing.affected_zones) | set(spof.affected_zones)
        )
        existing.affected_zones = zones
        existing.affected_population = sum(
            township.zone(z).population for z in zones
        )
        existing.redundant_group = sorted(
            set(existing.redundant_group) | set(spof.redundant_group)
        )
        existing.design_storm_failure_prob = max(
            existing.design_storm_failure_prob, spof.design_storm_failure_prob
        )
    ranked = sorted(
        merged.values(), key=lambda s: (-s.rank_score, s.kind, s.shared_asset or "")
    )
    return ranked[:TOP_N]
