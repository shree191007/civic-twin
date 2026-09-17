"""Effective Redundancy Score: how much of the redundancy on paper is real.

Two towers covering the same zones look like two. If both draw their power from
the same substation, there is one thing to lose, not two. The score is the
ratio of genuinely independent failure paths to the nominal count, and it is
the single number that says whether a redundancy is redundant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from civictwin.ontology import AssetKind, LinkKind, Service, Township

#: Below this, a group's redundancy is mostly decorative.
WEAK_REDUNDANCY = 0.75

#: Reported separately so a group that shares power and backhaul says both,
#: rather than naming whichever happened to sort first.
SHARED_KINDS: tuple[tuple[LinkKind, str], ...] = (
    (LinkKind.POWERS, "power"),
    (LinkKind.SUPPLIES_WATER, "water"),
    (LinkKind.BACKHAULS, "backhaul"),
    (LinkKind.HOSTED_ON, "the same structure"),
)


@dataclass(slots=True)
class RedundancyGroup:
    """One set of assets that is supposed to back each other up."""

    id: str
    service: Service
    members: list[str]
    zones: list[str]
    population: int
    nominal_paths: int
    independent_paths: int
    shared_dependencies: list[str] = field(default_factory=list)
    #: Township-wide dependencies, excluded from the score as uninformative.
    common_roots: list[str] = field(default_factory=list)
    #: The nearest thing shared through each kind of dependency.
    shared_by_kind: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Independent failure paths over nominal ones, in (0, 1]."""
        if self.nominal_paths <= 0:
            return 1.0
        return self.independent_paths / self.nominal_paths

    @property
    def is_weak(self) -> bool:
        return self.score < WEAK_REDUNDANCY

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "service": self.service.value,
            "members": list(self.members),
            "zones": list(self.zones),
            "population": self.population,
            "nominal_paths": self.nominal_paths,
            "independent_paths": self.independent_paths,
            "score": round(self.score, 3),
            "weak": self.is_weak,
            "shared_dependencies": list(self.shared_dependencies),
            "shared_by_kind": dict(self.shared_by_kind),
            "common_roots": list(self.common_roots),
            "explanation": self.explanation,
        }

    @property
    def explanation(self) -> str:
        members = ", ".join(self.members)
        if not self.shared_dependencies:
            tail = (
                f" Above them, the whole township still depends on "
                f"{', '.join(self.common_roots[:3])}."
                if self.common_roots
                else ""
            )
            return (
                f"{members} fail independently: {self.independent_paths} of "
                f"{self.nominal_paths} paths are distinct.{tail}"
            )
        if self.shared_by_kind:
            causes = ", ".join(
                f"{asset} for {label}" for label, asset in self.shared_by_kind.items()
            )
        else:
            causes = ", ".join(self.shared_dependencies[:3])
        return (
            f"{members} look like {self.nominal_paths} independent paths but "
            f"amount to {self.independent_paths}: they share {causes}."
        )

    @property
    def nearest_shared(self) -> str | None:
        """The most specific thing the group shares, which is what to fix."""
        return self.shared_dependencies[0] if self.shared_dependencies else None


def common_roots(township: Township) -> set[str]:
    """Assets the whole township depends on, such as the grid supply.

    Every redundant group shares these, so naming them as the reason a pair is
    not redundant is true but useless. They are reported separately, as a
    systemic root rather than a failure of redundancy.
    """
    total = sum(z.population for z in township.zones)
    return {
        aid
        for aid in township.assets
        if township.served_population(aid) >= total
    }


def independent_paths(
    township: Township,
    members: Sequence[str],
    ignore: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Count failure paths that share nothing, and name what the rest share.

    Members are joined when their dependency closures intersect; each connected
    group of joined members is one failure path, because one upstream loss takes
    all of them. Shared dependencies are reported nearest-first — the asset
    serving the fewest people is the most specific, and therefore the most
    useful thing to name.
    """
    skip = ignore or set()
    closures = {m: (township.upstream(m) | {m}) - skip for m in members}
    parent = {m: m for m in members}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    shared: set[str] = set()
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            overlap = closures[a] & closures[b]
            if overlap:
                union(a, b)
                shared |= overlap
    groups = {find(m) for m in members}
    ranked = sorted(
        shared, key=lambda a: (township.served_population(a), a)
    )
    return len(groups), ranked


def shared_by_kind(
    township: Township, members: Sequence[str], ignore: set[str]
) -> dict[str, str]:
    """The nearest asset the whole group depends on, per kind of dependency."""
    out: dict[str, str] = {}
    for kind, label in SHARED_KINDS:
        closures = [
            (township.upstream(m, {kind}) | {m}) - ignore for m in members
        ]
        overlap = set.intersection(*closures) - set(members) if closures else set()
        if overlap:
            out[label] = min(
                overlap, key=lambda a: (township.served_population(a), a)
            )
    return out


def redundancy_groups(township: Township) -> list[RedundancyGroup]:
    """Every set of assets the township relies on to back each other up."""
    out: list[RedundancyGroup] = []
    roots = common_roots(township)

    # Communications: the towers covering each zone.
    by_towers: dict[tuple[str, ...], list[str]] = {}
    for zone in township.zones:
        if len(zone.towers) > 1:
            by_towers.setdefault(tuple(sorted(zone.towers)), []).append(zone.id)
    for towers, zones in sorted(by_towers.items()):
        count, shared = independent_paths(township, list(towers), roots)
        out.append(
            RedundancyGroup(
                id=f"comms:{'+'.join(towers)}",
                service=Service.COMMS,
                members=list(towers),
                zones=sorted(zones, key=lambda z: int(z[1:])),
                population=sum(township.zone(z).population for z in zones),
                nominal_paths=len(towers),
                independent_paths=count,
                shared_dependencies=shared,
            )
        )

    # Health: the hospitals, which are meant to cover for each other.
    hospitals = sorted(
        a.id for a in township.assets.values() if a.kind is AssetKind.HOSPITAL
    )
    if len(hospitals) > 1:
        count, shared = independent_paths(township, hospitals, roots)
        out.append(
            RedundancyGroup(
                id=f"health:{'+'.join(hospitals)}",
                service=Service.HEALTH,
                members=hospitals,
                zones=[z.id for z in township.zones],
                population=sum(z.population for z in township.zones),
                nominal_paths=len(hospitals),
                independent_paths=count,
                shared_dependencies=shared,
            )
        )

    # Communications backhaul: the fibre routes between the exchanges.
    fibres = sorted(
        a.id for a in township.assets.values() if a.kind is AssetKind.FIBRE
    )
    parallel = [
        f
        for f in fibres
        if set(township.dependents(f)) & {
            a.id for a in township.assets.values() if a.kind is AssetKind.EXCHANGE
        }
    ]
    if len(parallel) > 1:
        count, shared = independent_paths(township, parallel, roots)
        out.append(
            RedundancyGroup(
                id=f"backhaul:{'+'.join(parallel)}",
                service=Service.COMMS,
                members=parallel,
                zones=sorted(
                    (
                        z.id
                        for z in township.zones
                        if set(parallel) & township.zone_dependency_closure(z.id)
                    ),
                    key=lambda z: int(z[1:]),
                ),
                population=sum(
                    z.population
                    for z in township.zones
                    if set(parallel) & township.zone_dependency_closure(z.id)
                ),
                nominal_paths=len(parallel),
                independent_paths=count,
                shared_dependencies=shared,
            )
        )

    for group in out:
        group.common_roots = sorted(roots)
        group.shared_by_kind = shared_by_kind(township, group.members, roots)
    out.sort(key=lambda g: (g.score, -g.population))
    return out


def system_score(groups: Iterable[RedundancyGroup]) -> float:
    """Population-weighted effective redundancy across the township."""
    rows = list(groups)
    total = sum(g.population for g in rows)
    if total <= 0:
        return 1.0
    return sum(g.score * g.population for g in rows) / total
