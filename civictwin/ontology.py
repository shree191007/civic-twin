"""Typed domain model for a township: assets, links, zones, roads, crews."""
from __future__ import annotations

import heapq
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum

from civictwin.provenance import Provenance


class Portfolio(str, Enum):
    ENERGY = "energy"
    WATER = "water"
    COMMS = "comms"
    TRANSPORT = "transport"
    SERVICES = "services"


class AssetKind(str, Enum):
    # energy
    GRID_SUPPLY = "grid_supply"
    SUBSTATION = "substation"
    FEEDER = "feeder"
    TRANSFORMER = "transformer"
    FUEL_STATION = "fuel_station"
    # water
    INTAKE = "intake"
    TREATMENT = "treatment"
    PUMP = "pump"
    TANK = "tank"
    STORMWATER_PUMP = "stormwater_pump"
    # comms
    EXCHANGE = "exchange"
    TOWER = "tower"
    FIBRE = "fibre"
    # transport
    BRIDGE = "bridge"
    UNDERPASS = "underpass"
    # services
    HOSPITAL = "hospital"
    CLINIC = "clinic"
    FIRE_STATION = "fire_station"
    EOC = "eoc"
    SHELTER = "shelter"
    # logistics
    DEPOT = "depot"


class LinkKind(str, Enum):
    POWERS = "powers"
    SUPPLIES_WATER = "supplies_water"
    BACKHAULS = "backhauls"
    CONTROLS = "controls"
    HOSTED_ON = "hosted_on"
    FUELS = "fuels"


class Service(str, Enum):
    ENERGY = "energy"
    WATER = "water"
    COMMS = "comms"
    HEALTH = "health"
    MOBILITY = "mobility"


class OperatingState(str, Enum):
    """How an asset is running right now, as an operator would describe it.

    This is not the same thing as `DamageState`. An undamaged pump whose grid
    supply has failed is `BACKUP`, not damaged at all; a moderately damaged
    substation still carrying load is `DEGRADED`. Damage is what the hazard did
    to the asset; the operating state is what the asset is doing about it.
    """

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    BACKUP = "backup"
    CRITICAL = "critical"
    FAILED = "failed"


class DamageState(str, Enum):
    NONE = "none"
    SLIGHT = "slight"
    MODERATE = "moderate"
    EXTENSIVE = "extensive"
    COMPLETE = "complete"


DAMAGE_ORDER: list[DamageState] = [
    DamageState.NONE,
    DamageState.SLIGHT,
    DamageState.MODERATE,
    DamageState.EXTENSIVE,
    DamageState.COMPLETE,
]

_DAMAGE_INDEX: dict[DamageState, int] = {ds: i for i, ds in enumerate(DAMAGE_ORDER)}

#: Link kinds that describe a physical/structural dependency and must be acyclic.
ACYCLIC_LINK_KINDS: frozenset[LinkKind] = frozenset(
    {LinkKind.POWERS, LinkKind.SUPPLIES_WATER, LinkKind.BACKHAULS, LinkKind.HOSTED_ON}
)


def damage_index(ds: DamageState) -> int:
    """Ordinal position of a damage state, NONE=0 .. COMPLETE=4."""
    return _DAMAGE_INDEX[ds]


@dataclass(slots=True)
class Asset:
    id: str
    kind: AssetKind
    portfolio: Portfolio
    name: str
    x: float
    y: float
    node: int
    hand_m: float
    fragility_median_m: float
    provenance: Provenance
    fragility_beta: float = 0.4
    capacity: float = 1.0
    backup_hours: float = 0.0
    fuel_hours: float = 0.0
    repair_hours_base: float = 24.0
    scada_controlled: bool = False
    meta: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "portfolio": self.portfolio.value,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "node": self.node,
            "hand_m": self.hand_m,
            "fragility_median_m": self.fragility_median_m,
            "fragility_beta": self.fragility_beta,
            "capacity": self.capacity,
            "backup_hours": self.backup_hours,
            "fuel_hours": self.fuel_hours,
            "repair_hours_base": self.repair_hours_base,
            "scada_controlled": self.scada_controlled,
            "provenance": self.provenance.value,
            "meta": dict(self.meta),
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> Asset:
        return Asset(
            id=str(d["id"]),
            kind=AssetKind(str(d["kind"])),
            portfolio=Portfolio(str(d["portfolio"])),
            name=str(d["name"]),
            x=float(d["x"]),  # type: ignore[arg-type]
            y=float(d["y"]),  # type: ignore[arg-type]
            node=int(d["node"]),  # type: ignore[arg-type]
            hand_m=float(d["hand_m"]),  # type: ignore[arg-type]
            fragility_median_m=float(d["fragility_median_m"]),  # type: ignore[arg-type]
            provenance=Provenance(str(d["provenance"])),
            fragility_beta=float(d.get("fragility_beta", 0.4)),  # type: ignore[arg-type]
            capacity=float(d.get("capacity", 1.0)),  # type: ignore[arg-type]
            backup_hours=float(d.get("backup_hours", 0.0)),  # type: ignore[arg-type]
            fuel_hours=float(d.get("fuel_hours", 0.0)),  # type: ignore[arg-type]
            repair_hours_base=float(d.get("repair_hours_base", 24.0)),  # type: ignore[arg-type]
            scada_controlled=bool(d.get("scada_controlled", False)),
            meta=dict(d.get("meta", {})),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class Link:
    """A dependency of `target` on `source`.

    Beyond the bare topology, a dependency has behaviour in time: how much of
    the provider the dependent actually needs, how long it can carry on without
    it, and how long the loss takes to bite. The defaults describe the simplest
    possible dependency -- any supply at all is enough, loss is felt at once,
    and there is no reserve -- so a link that says nothing about its behaviour
    behaves exactly as a plain edge would.
    """

    source: str
    target: str
    kind: LinkKind
    capacity: float = 1.0
    provenance: Provenance = Provenance.SYNTHETIC
    #: Minimum provider availability the dependent needs, in [0, 1].
    threshold: float = 0.0
    #: Hours the dependent can keep running on this link's own reserve once the
    #: provider falls below the threshold (a header tank, a local UPS).
    backup_capacity_h: float = 0.0
    #: Hours between the provider falling short and the dependent feeling it.
    delay_h: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "capacity": self.capacity,
            "provenance": self.provenance.value,
            "threshold": self.threshold,
            "backup_capacity_h": self.backup_capacity_h,
            "delay_h": self.delay_h,
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> Link:
        return Link(
            source=str(d["source"]),
            target=str(d["target"]),
            kind=LinkKind(str(d["kind"])),
            capacity=float(d.get("capacity", 1.0)),  # type: ignore[arg-type]
            provenance=Provenance(str(d.get("provenance", "synthetic"))),
            threshold=float(d.get("threshold", 0.0)),  # type: ignore[arg-type]
            backup_capacity_h=float(d.get("backup_capacity_h", 0.0)),  # type: ignore[arg-type]
            delay_h=float(d.get("delay_h", 0.0)),  # type: ignore[arg-type]
        )

    @property
    def key(self) -> str:
        """Stable identity for per-link simulation state."""
        return f"{self.source}>{self.target}>{self.kind.value}"


@dataclass(slots=True)
class DemandZone:
    id: str
    x: float
    y: float
    node: int
    hand_m: float
    population: int
    vulnerable_fraction: float
    substation: str
    feeder: str
    tank: str
    towers: list[str]
    provenance: Provenance
    water_storage_hours: float = 6.0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "node": self.node,
            "hand_m": self.hand_m,
            "population": self.population,
            "vulnerable_fraction": self.vulnerable_fraction,
            "substation": self.substation,
            "feeder": self.feeder,
            "tank": self.tank,
            "towers": list(self.towers),
            "water_storage_hours": self.water_storage_hours,
            "provenance": self.provenance.value,
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> DemandZone:
        return DemandZone(
            id=str(d["id"]),
            x=float(d["x"]),  # type: ignore[arg-type]
            y=float(d["y"]),  # type: ignore[arg-type]
            node=int(d["node"]),  # type: ignore[arg-type]
            hand_m=float(d["hand_m"]),  # type: ignore[arg-type]
            population=int(d["population"]),  # type: ignore[arg-type]
            vulnerable_fraction=float(d["vulnerable_fraction"]),  # type: ignore[arg-type]
            substation=str(d["substation"]),
            feeder=str(d["feeder"]),
            tank=str(d["tank"]),
            towers=[str(t) for t in d["towers"]],  # type: ignore[union-attr]
            provenance=Provenance(str(d["provenance"])),
            water_storage_hours=float(d.get("water_storage_hours", 6.0)),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class RoadEdge:
    id: str
    u: int
    v: int
    length_m: float
    lanes: int
    free_flow_kph: float
    capacity_vph: float
    hand_m: float
    host_asset: str | None
    provenance: Provenance

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "u": self.u,
            "v": self.v,
            "length_m": self.length_m,
            "lanes": self.lanes,
            "free_flow_kph": self.free_flow_kph,
            "capacity_vph": self.capacity_vph,
            "hand_m": self.hand_m,
            "host_asset": self.host_asset,
            "provenance": self.provenance.value,
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> RoadEdge:
        host = d.get("host_asset")
        return RoadEdge(
            id=str(d["id"]),
            u=int(d["u"]),  # type: ignore[arg-type]
            v=int(d["v"]),  # type: ignore[arg-type]
            length_m=float(d["length_m"]),  # type: ignore[arg-type]
            lanes=int(d["lanes"]),  # type: ignore[arg-type]
            free_flow_kph=float(d["free_flow_kph"]),  # type: ignore[arg-type]
            capacity_vph=float(d["capacity_vph"]),  # type: ignore[arg-type]
            hand_m=float(d["hand_m"]),  # type: ignore[arg-type]
            host_asset=None if host is None else str(host),
            provenance=Provenance(str(d["provenance"])),
        )


@dataclass(slots=True)
class Crew:
    id: str
    portfolio: Portfolio
    depot: str
    speed_kph: float = 25.0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "portfolio": self.portfolio.value,
            "depot": self.depot,
            "speed_kph": self.speed_kph,
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> Crew:
        return Crew(
            id=str(d["id"]),
            portfolio=Portfolio(str(d["portfolio"])),
            depot=str(d["depot"]),
            speed_kph=float(d.get("speed_kph", 25.0)),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class Township:
    """An immutable township. Scenario changes are overlays, never mutations."""

    name: str
    crs_epsg: int
    origin_lonlat: tuple[float, float]
    assets: dict[str, Asset]
    links: list[Link]
    zones: list[DemandZone]
    roads: dict[str, RoadEdge]
    nodes: dict[int, tuple[float, float]]
    crews: list[Crew]
    extent_m: float
    seed: int

    # caches (built in __post_init__)
    _providers_by_target: dict[str, list[Link]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _dependents_by_source: dict[str, list[Link]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _by_portfolio: dict[Portfolio, list[Asset]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _zones_by_id: dict[str, DemandZone] = field(
        default_factory=dict, repr=False, compare=False
    )
    _closure_cache: dict[str, set[str]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _upstream_cache: dict[tuple[str, frozenset[LinkKind] | None], set[str]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _adjacency: dict[int, list[tuple[int, str, float]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _served_pop_cache: dict[str, int] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.rebuild_caches()

    # ------------------------------------------------------------------ caches

    def rebuild_caches(self) -> None:
        """(Re)build all derived indices. Called once at construction."""
        prov: dict[str, list[Link]] = defaultdict(list)
        dep: dict[str, list[Link]] = defaultdict(list)
        for link in self.links:
            prov[link.target].append(link)
            dep[link.source].append(link)
        self._providers_by_target = dict(prov)
        self._dependents_by_source = dict(dep)

        by_pf: dict[Portfolio, list[Asset]] = defaultdict(list)
        for a in self.assets.values():
            by_pf[a.portfolio].append(a)
        self._by_portfolio = dict(by_pf)

        self._zones_by_id = {z.id: z for z in self.zones}
        self._closure_cache = {}
        self._upstream_cache = {}
        self._served_pop_cache = {}

        adj: dict[int, list[tuple[int, str, float]]] = {n: [] for n in self.nodes}
        for e in self.roads.values():
            tt = (e.length_m / 1000.0) / max(e.free_flow_kph, 1e-6)
            adj.setdefault(e.u, []).append((e.v, e.id, tt))
            adj.setdefault(e.v, []).append((e.u, e.id, tt))
        self._adjacency = adj

    # ----------------------------------------------------------- graph queries

    def providers(self, asset_id: str, kinds: set[LinkKind] | None = None) -> list[str]:
        """Ids of assets that directly supply `asset_id`."""
        links = self._providers_by_target.get(asset_id, ())
        if kinds is None:
            return [ln.source for ln in links]
        return [ln.source for ln in links if ln.kind in kinds]

    def dependents(self, asset_id: str, kinds: set[LinkKind] | None = None) -> list[str]:
        """Ids of assets that directly depend on `asset_id`."""
        links = self._dependents_by_source.get(asset_id, ())
        if kinds is None:
            return [ln.target for ln in links]
        return [ln.target for ln in links if ln.kind in kinds]

    def upstream(self, asset_id: str, kinds: set[LinkKind] | None = None) -> set[str]:
        """Transitive closure of providers. Cycle-safe."""
        key = (asset_id, None if kinds is None else frozenset(kinds))
        cached = self._upstream_cache.get(key)
        if cached is not None:
            return cached
        seen: set[str] = set()
        stack = deque(self.providers(asset_id, kinds))
        while stack:
            cur = stack.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.providers(cur, kinds))
        self._upstream_cache[key] = seen
        return seen

    def downstream(self, asset_id: str, kinds: set[LinkKind] | None = None) -> set[str]:
        """Transitive closure of dependents. Cycle-safe."""
        seen: set[str] = set()
        stack = deque(self.dependents(asset_id, kinds))
        while stack:
            cur = stack.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.dependents(cur, kinds))
        return seen

    def assets_of(self, portfolio: Portfolio) -> list[Asset]:
        return list(self._by_portfolio.get(portfolio, ()))

    def zone(self, zone_id: str) -> DemandZone:
        return self._zones_by_id[zone_id]

    def zone_providers(self, zone_id: str) -> set[str]:
        """Direct providers of a zone: substation, feeder, tank, towers.

        The zone's distribution transformer is included too when one exists.
        A zone takes its electricity through that transformer, so leaving it
        out would report a served population of zero for every transformer in
        the township. It is matched by the naming convention of spec 00
        section 5 (`TR<n>` for `Z<n>`), which is also how the energy layer
        finds it.
        """
        z = self._zones_by_id[zone_id]
        out = {z.substation, z.feeder, z.tank}
        out.update(z.towers)
        transformer = f"TR{z.id[1:]}"
        if transformer in self.assets:
            out.add(transformer)
        return out

    def zone_dependency_closure(self, zone_id: str) -> set[str]:
        """Direct zone providers plus everything upstream of them. Cached."""
        cached = self._closure_cache.get(zone_id)
        if cached is not None:
            return cached
        out = set(self.zone_providers(zone_id))
        for aid in list(out):
            out |= self.upstream(aid)
        self._closure_cache[zone_id] = out
        return out

    def served_population(self, asset_id: str) -> int:
        """Population of every zone whose dependency closure contains the asset."""
        cached = self._served_pop_cache.get(asset_id)
        if cached is not None:
            return cached
        total = 0
        for z in self.zones:
            if asset_id in self.zone_dependency_closure(z.id):
                total += z.population
        self._served_pop_cache[asset_id] = total
        return total

    def road_graph(self) -> dict[int, list[tuple[int, str, float]]]:
        """node -> [(neighbour, edge_id, free-flow travel time in hours)]."""
        return self._adjacency

    def components(self) -> list[set[int]]:
        """Connected components of the road graph."""
        seen: set[int] = set()
        out: list[set[int]] = []
        for start in self.nodes:
            if start in seen:
                continue
            comp: set[int] = set()
            stack = [start]
            while stack:
                n = stack.pop()
                if n in comp:
                    continue
                comp.add(n)
                for nb, _eid, _tt in self._adjacency.get(n, ()):
                    if nb not in comp:
                        stack.append(nb)
            seen |= comp
            out.append(comp)
        return out

    def shortest_path_hours(
        self, src: int, dst: int, blocked: set[str] | None = None
    ) -> float:
        """Dijkstra travel time in hours; `inf` if unreachable."""
        if src == dst:
            return 0.0
        blocked = blocked or set()
        dist: dict[int, float] = {src: 0.0}
        pq: list[tuple[float, int]] = [(0.0, src)]
        while pq:
            d, n = heapq.heappop(pq)
            if n == dst:
                return d
            if d > dist.get(n, math.inf):
                continue
            for nb, eid, tt in self._adjacency.get(n, ()):
                if eid in blocked:
                    continue
                nd = d + tt
                if nd < dist.get(nb, math.inf):
                    dist[nb] = nd
                    heapq.heappush(pq, (nd, nb))
        return math.inf

    # ------------------------------------------------------------- validation

    def validate(self) -> list[str]:
        """Return a list of structural problems; empty means valid."""
        problems: list[str] = []
        ids = self.assets

        for ln in self.links:
            if ln.source not in ids:
                problems.append(f"link source missing: {ln.source} -> {ln.target}")
            if ln.target not in ids:
                problems.append(f"link target missing: {ln.source} -> {ln.target}")
            if ln.source == ln.target:
                problems.append(f"self-link on {ln.source} ({ln.kind.value})")

        problems.extend(self._cycle_problems())

        for a in ids.values():
            if a.node not in self.nodes:
                problems.append(f"asset {a.id} references missing node {a.node}")
            if a.hand_m < 0:
                problems.append(f"asset {a.id} has negative hand_m")
            if a.fragility_median_m <= 0:
                problems.append(f"asset {a.id} has non-positive fragility_median_m")
            if a.fragility_beta <= 0:
                problems.append(f"asset {a.id} has non-positive fragility_beta")

        for e in self.roads.values():
            if e.u not in self.nodes:
                problems.append(f"road {e.id} references missing node {e.u}")
            if e.v not in self.nodes:
                problems.append(f"road {e.id} references missing node {e.v}")

        comps = self.components()
        if len(comps) > 1:
            for comp in sorted(comps, key=len, reverse=True)[1:]:
                problems.append(
                    f"road graph island of {len(comp)} nodes: "
                    f"{sorted(comp)[:5]}{'...' if len(comp) > 5 else ''}"
                )

        for z in self.zones:
            for fieldname in ("substation", "feeder", "tank"):
                aid = getattr(z, fieldname)
                if aid not in ids:
                    problems.append(f"zone {z.id} {fieldname} missing asset {aid}")
            if not z.towers:
                problems.append(f"zone {z.id} has no towers")
            for t in z.towers:
                if t not in ids:
                    problems.append(f"zone {z.id} tower missing asset {t}")
            if z.population <= 0:
                problems.append(f"zone {z.id} has non-positive population")
            if not 0.0 <= z.vulnerable_fraction <= 1.0:
                problems.append(f"zone {z.id} vulnerable_fraction out of range")
            if z.node not in self.nodes:
                problems.append(f"zone {z.id} references missing node {z.node}")

        needs_power = {
            AssetKind.TOWER,
            AssetKind.PUMP,
            AssetKind.EXCHANGE,
            AssetKind.TREATMENT,
            AssetKind.HOSPITAL,
        }
        for a in ids.values():
            if a.kind in needs_power and not self.providers(a.id, {LinkKind.POWERS}):
                problems.append(f"asset {a.id} ({a.kind.value}) has no POWERS provider")
            if a.kind is AssetKind.TOWER and not self.providers(
                a.id, {LinkKind.BACKHAULS}
            ):
                problems.append(f"tower {a.id} has no BACKHAULS provider")

        for c in self.crews:
            dep = ids.get(c.depot)
            if dep is None:
                problems.append(f"crew {c.id} references missing depot {c.depot}")
            elif dep.kind is not AssetKind.DEPOT:
                problems.append(f"crew {c.id} depot {c.depot} is not a DEPOT")

        present = {a.kind for a in ids.values()}
        for required in (
            AssetKind.SUBSTATION,
            AssetKind.PUMP,
            AssetKind.TANK,
            AssetKind.TOWER,
            AssetKind.EXCHANGE,
            AssetKind.HOSPITAL,
            AssetKind.BRIDGE,
            AssetKind.DEPOT,
        ):
            if required not in present:
                problems.append(f"no asset of required kind {required.value}")

        return problems

    def _cycle_problems(self) -> list[str]:
        """Detect cycles among structural (non-CONTROLS/FUELS) link kinds."""
        adj: dict[str, list[str]] = defaultdict(list)
        for ln in self.links:
            if ln.kind in ACYCLIC_LINK_KINDS and ln.source in self.assets and ln.target in self.assets:
                adj[ln.source].append(ln.target)
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {a: WHITE for a in self.assets}
        problems: list[str] = []

        for start in self.assets:
            if colour[start] != WHITE:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            path: list[str] = []
            colour[start] = GREY
            path.append(start)
            while stack:
                node, idx = stack[-1]
                children = adj.get(node, ())
                if idx < len(children):
                    stack[-1] = (node, idx + 1)
                    child = children[idx]
                    if colour.get(child, WHITE) == GREY:
                        cyc = path[path.index(child):] + [child]
                        problems.append("structural cycle: " + " -> ".join(cyc))
                    elif colour.get(child, WHITE) == WHITE:
                        colour[child] = GREY
                        path.append(child)
                        stack.append((child, 0))
                else:
                    colour[node] = BLACK
                    stack.pop()
                    path.pop()
        return problems

    # -------------------------------------------------------------- serialise

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "crs_epsg": self.crs_epsg,
            "origin_lonlat": [self.origin_lonlat[0], self.origin_lonlat[1]],
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "links": [ln.to_dict() for ln in self.links],
            "zones": [z.to_dict() for z in self.zones],
            "roads": {k: v.to_dict() for k, v in self.roads.items()},
            "nodes": {str(k): [v[0], v[1]] for k, v in self.nodes.items()},
            "crews": [c.to_dict() for c in self.crews],
            "extent_m": self.extent_m,
            "seed": self.seed,
        }

    @staticmethod
    def from_dict(d: dict[str, object]) -> Township:
        origin = d["origin_lonlat"]
        return Township(
            name=str(d["name"]),
            crs_epsg=int(d["crs_epsg"]),  # type: ignore[arg-type]
            origin_lonlat=(float(origin[0]), float(origin[1])),  # type: ignore[index]
            assets={
                k: Asset.from_dict(v) for k, v in d["assets"].items()  # type: ignore[union-attr]
            },
            links=[Link.from_dict(x) for x in d["links"]],  # type: ignore[union-attr]
            zones=[DemandZone.from_dict(x) for x in d["zones"]],  # type: ignore[union-attr]
            roads={
                k: RoadEdge.from_dict(v) for k, v in d["roads"].items()  # type: ignore[union-attr]
            },
            nodes={
                int(k): (float(v[0]), float(v[1]))
                for k, v in d["nodes"].items()  # type: ignore[union-attr]
            },
            crews=[Crew.from_dict(x) for x in d["crews"]],  # type: ignore[union-attr]
            extent_m=float(d["extent_m"]),  # type: ignore[arg-type]
            seed=int(d["seed"]),  # type: ignore[arg-type]
        )
