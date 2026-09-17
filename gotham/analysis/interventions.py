"""Resilience interventions: the catalogue, and how an overlay resolves to effects."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Literal, Mapping, Sequence

from gotham.config import Config
from gotham.ontology import AssetKind, LinkKind, Township

logger = logging.getLogger(__name__)

InterventionKind = Literal[
    "harden", "backup", "fuel", "storage", "tie", "reroute", "mobile", "operational"
]

COST_HARDEN_STRUCTURAL = 4_500_000.0
COST_HARDEN_LOWCOST = 350_000.0
COST_HARDEN_BRIDGE = 9_000_000.0
COST_BACKUP_TOWER = 220_000.0
COST_BACKUP_GENERATOR = 1_400_000.0
COST_FUEL = 600_000.0
COST_STORAGE = 2_100_000.0
COST_TIE = 3_200_000.0
COST_REROUTE = 2_800_000.0
COST_MOBILE = 900_000.0
COST_OPERATIONAL_THRESHOLD = 50_000.0
COST_OPERATIONAL_CREWS = 400_000.0

MOBILE_TARGET_COUNT = 3
#: How far apart two zone centroids can be and still count as neighbours,
#: as a multiple of the township's typical zone spacing.
ZONE_ADJACENCY_FACTOR = 1.4

HARDEN_STRUCTURAL_KINDS = (
    AssetKind.SUBSTATION,
    AssetKind.TREATMENT,
    AssetKind.EXCHANGE,
    AssetKind.PUMP,
    AssetKind.INTAKE,
)
HARDEN_LOWCOST_KINDS = (
    AssetKind.TOWER,
    AssetKind.TRANSFORMER,
    AssetKind.STORMWATER_PUMP,
)


@dataclass(frozen=True, slots=True)
class Intervention:
    """One purchasable resilience measure."""

    id: str
    kind: InterventionKind
    target: str
    cost_inr: float
    label: str
    effect: Mapping[str, float | str]
    cost_basis: Literal["schedule_of_rates", "illustrative"] = "illustrative"
    #: Every asset this intervention acts on. A tie touches two feeders, and
    #: recovering them by splitting `target` on a hyphen breaks the moment an
    #: asset id contains one.
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.targets:
            object.__setattr__(self, "targets", (self.target,))

    @property
    def primary_target(self) -> str:
        """The asset this is mostly about, for ranking and explanation."""
        return self.targets[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "cost_inr": self.cost_inr,
            "label": self.label,
            "effect": dict(self.effect),
            "cost_basis": self.cost_basis,
            "targets": list(self.targets),
        }


@dataclass(slots=True)
class Effect:
    """The resolved consequence of a set of interventions. Never mutates a town."""

    median_override: dict[str, float] = field(default_factory=dict)
    backup_hours: dict[str, float] = field(default_factory=dict)
    fuel_hours: dict[str, float] = field(default_factory=dict)
    repair_factor: float = 1.0
    cfg: Config | None = None
    extra_links: tuple[str, ...] = ()


def _zone_neighbours(township: Township) -> dict[str, set[str]]:
    """Which zones adjoin which, from where they actually are.

    The previous version decoded a position out of the zone id on the
    assumption of a 4x4 grid, which is true of the synthetic township and of
    nothing else. Adjacency here means "close enough to be worth a tie", judged
    from the centroids and the typical spacing between them.
    """
    zones = township.zones
    if len(zones) < 2:
        return {z.id: set() for z in zones}
    spacings: list[float] = []
    for z in zones:
        nearest = min(
            math.hypot(z.x - other.x, z.y - other.y)
            for other in zones
            if other.id != z.id
        )
        spacings.append(nearest)
    typical = sorted(spacings)[len(spacings) // 2]
    reach = typical * ZONE_ADJACENCY_FACTOR
    return {
        z.id: {
            other.id
            for other in zones
            if other.id != z.id
            and math.hypot(z.x - other.x, z.y - other.y) <= reach
        }
        for z in zones
    }


def generate_catalogue(township: Township, cfg: Config) -> list[Intervention]:
    """Every intervention available for this township, in deterministic order."""
    del cfg
    out: list[Intervention] = []
    assets = township.assets

    for aid in sorted(assets):
        a = assets[aid]
        if a.kind in HARDEN_STRUCTURAL_KINDS:
            out.append(
                Intervention(
                    id=f"harden:{aid}",
                    kind="harden",
                    target=aid,
                    cost_inr=COST_HARDEN_STRUCTURAL,
                    label=f"Flood-proof {a.name} (+1.2 m threshold)",
                    effect={"fragility_median_delta_m": 1.2},
                )
            )
        elif a.kind in HARDEN_LOWCOST_KINDS:
            out.append(
                Intervention(
                    id=f"harden:{aid}",
                    kind="harden",
                    target=aid,
                    cost_inr=COST_HARDEN_LOWCOST,
                    label=f"Raise {a.name} on a plinth (+1.0 m threshold)",
                    effect={"fragility_median_delta_m": 1.0},
                )
            )
        elif a.kind is AssetKind.BRIDGE:
            out.append(
                Intervention(
                    id=f"harden:{aid}",
                    kind="harden",
                    target=aid,
                    cost_inr=COST_HARDEN_BRIDGE,
                    label=f"Strengthen and raise {a.name} (+1.0 m)",
                    effect={"fragility_median_delta_m": 1.0},
                )
            )

    for aid in sorted(assets):
        a = assets[aid]
        if a.kind is AssetKind.TOWER:
            out.append(
                Intervention(
                    id=f"backup:{aid}:+12h",
                    kind="backup",
                    target=aid,
                    cost_inr=COST_BACKUP_TOWER,
                    label=f"Extra battery at {a.name} (+12 h)",
                    effect={"backup_hours_delta": 12.0},
                )
            )
        elif a.kind in (AssetKind.EXCHANGE, AssetKind.PUMP):
            out.append(
                Intervention(
                    id=f"backup:{aid}:+48h",
                    kind="backup",
                    target=aid,
                    cost_inr=COST_BACKUP_GENERATOR,
                    label=f"Standby generator at {a.name} (+48 h)",
                    effect={"backup_hours_delta": 48.0, "fuel_hours_delta": 48.0},
                )
            )

    for aid in sorted(assets):
        a = assets[aid]
        if a.kind in (AssetKind.HOSPITAL, AssetKind.EOC):
            out.append(
                Intervention(
                    id=f"fuel:{aid}:+72h",
                    kind="fuel",
                    target=aid,
                    cost_inr=COST_FUEL,
                    label=f"Bunded fuel store at {a.name} (+72 h)",
                    effect={"fuel_hours_delta": 72.0},
                )
            )

    for aid in sorted(assets):
        if assets[aid].kind is AssetKind.TANK:
            out.append(
                Intervention(
                    id=f"storage:{aid}:+24h",
                    kind="storage",
                    target=aid,
                    cost_inr=COST_STORAGE,
                    label=f"Additional storage at {assets[aid].name} (+24 h)",
                    effect={"water_storage_hours_delta": 24.0},
                )
            )

    for f, g in _adjacent_feeder_pairs(township):
        out.append(
            Intervention(
                id=f"tie:{f}-{g}",
                kind="tie",
                target=f"{f}-{g}",
                targets=(f, g),
                cost_inr=COST_TIE,
                label=f"Normally-open tie between feeders {f} and {g}",
                effect={"new_link": f"{f}>{g}>powers"},
            )
        )

    for aid in sorted(assets):
        if assets[aid].kind is not AssetKind.FIBRE:
            continue
        hosts = township.providers(aid, {LinkKind.HOSTED_ON})
        if not any(assets[h].kind is AssetKind.BRIDGE for h in hosts):
            continue
        out.append(
            Intervention(
                id=f"reroute:{aid}",
                kind="reroute",
                target=aid,
                cost_inr=COST_REROUTE,
                label=f"Independent duct for {assets[aid].name} (off the bridge)",
                effect={"remove_hosted_on": aid},
            )
        )

    for aid in sorted(assets):
        if assets[aid].kind is AssetKind.DEPOT:
            out.append(
                Intervention(
                    id=f"mobile:{aid}",
                    kind="mobile",
                    target=aid,
                    cost_inr=COST_MOBILE,
                    label=f"Mobile generator pre-positioned at {assets[aid].name}",
                    effect={"fuel_hours_delta": 48.0},
                )
            )

    out.append(
        Intervention(
            id="operational:deenergise_threshold",
            kind="operational",
            target="global",
            cost_inr=COST_OPERATIONAL_THRESHOLD,
            label="Revised de-energisation rule (+0.15 flood-fraction tolerance)",
            effect={"deenergise_threshold_delta": 0.15},
        )
    )
    out.append(
        Intervention(
            id="operational:crew_prepositioning",
            kind="operational",
            target="global",
            cost_inr=COST_OPERATIONAL_CREWS,
            label="Pre-position repair crews before landfall (-20% repair time)",
            effect={"repair_hours_factor": 0.8},
        )
    )
    return out


def _adjacent_feeder_pairs(township: Township) -> list[tuple[str, str]]:
    """Feeder pairs serving adjacent zones fed by *different* substations."""
    feeder_zones: dict[str, list[str]] = {}
    feeder_sub: dict[str, str] = {}
    for z in township.zones:
        feeder_zones.setdefault(z.feeder, []).append(z.id)
        feeder_sub[z.feeder] = z.substation
    neighbours = _zone_neighbours(township)
    pairs: set[tuple[str, str]] = set()
    for f, fz in feeder_zones.items():
        for g, gz in feeder_zones.items():
            if f >= g or feeder_sub[f] == feeder_sub[g]:
                continue
            if any(set(gz) & neighbours[a] for a in fz):
                pairs.add((f, g))
    return sorted(pairs)


_CATALOGUE_CACHE: dict[int, dict[str, Intervention]] = {}


def catalogue_index(township: Township, cfg: Config) -> dict[str, Intervention]:
    """Catalogue keyed by intervention id, cached per township identity."""
    key = id(township)
    cached = _CATALOGUE_CACHE.get(key)
    if cached is None:
        cached = {i.id: i for i in generate_catalogue(township, cfg)}
        _CATALOGUE_CACHE[key] = cached
    return cached


def resolve(
    township: Township, intervention_ids: Sequence[str], cfg: Config
) -> Effect:
    """Turn a list of intervention ids into concrete parameter changes."""
    effect = Effect(cfg=cfg)
    if not intervention_ids:
        return effect
    index = catalogue_index(township, cfg)
    seen: set[str] = set()
    structural: list[str] = []
    energy_cfg = cfg.energy
    zone_storage: dict[str, float] = {}

    for iid in intervention_ids:
        if iid in seen:
            logger.warning("intervention %s applied twice; ignoring the repeat", iid)
            continue
        seen.add(iid)
        iv = index.get(iid)
        if iv is None:
            logger.warning("unknown intervention id %s", iid)
            continue
        for key, value in iv.effect.items():
            if key == "fragility_median_delta_m":
                base = effect.median_override.get(
                    iv.target, township.assets[iv.target].fragility_median_m
                )
                effect.median_override[iv.target] = base + float(value)
            elif key == "backup_hours_delta":
                base = effect.backup_hours.get(
                    iv.target, township.assets[iv.target].backup_hours
                )
                effect.backup_hours[iv.target] = base + float(value)
            elif key == "fuel_hours_delta":
                base = effect.fuel_hours.get(
                    iv.target, township.assets[iv.target].fuel_hours
                )
                effect.fuel_hours[iv.target] = base + float(value)
            elif key == "water_storage_hours_delta":
                for z in township.zones:
                    if z.tank == iv.target:
                        zone_storage[z.id] = zone_storage.get(z.id, 0.0) + float(value)
            elif key == "capacity_delta":
                structural.append(f"cap|{iv.target}|{float(value)}")
            elif key == "new_link":
                src, dst, kind = str(value).split(">")
                structural.append(f"link+|{src}|{dst}|{kind}")
            elif key == "remove_hosted_on":
                structural.append(f"hosted-|{iv.target}")
            elif key == "deenergise_threshold_delta":
                energy_cfg = replace(
                    energy_cfg,
                    deenergise_flood_fraction=energy_cfg.deenergise_flood_fraction
                    + float(value),
                )
            elif key == "repair_hours_factor":
                effect.repair_factor *= float(value)
            else:  # pragma: no cover - guarded by the catalogue
                logger.warning("unknown effect key %s", key)

    for zid, delta in sorted(zone_storage.items()):
        structural.append(f"zstor|{zid}|{delta}")
    if energy_cfg is not cfg.energy:
        effect.cfg = replace(cfg, energy=energy_cfg)
    effect.extra_links = tuple(sorted(structural))
    return effect


def apply_structural(township: Township, keys: Sequence[str]) -> Township:
    """Return a copy of the township with the structural changes applied."""
    from gotham.ontology import Link

    if not keys:
        return township
    assets = dict(township.assets)
    links = list(township.links)
    zones = list(township.zones)
    for key in keys:
        parts = key.split("|")
        if parts[0] == "link+":
            links.append(Link(parts[1], parts[2], LinkKind(parts[3])))
        elif parts[0] == "hosted-":
            links = [
                ln
                for ln in links
                if not (ln.kind is LinkKind.HOSTED_ON and ln.target == parts[1])
            ]
        elif parts[0] == "cap":
            a = assets[parts[1]]
            assets[parts[1]] = replace(a, capacity=a.capacity + float(parts[2]))
        elif parts[0] == "zstor":
            zones = [
                replace(z, water_storage_hours=z.water_storage_hours + float(parts[2]))
                if z.id == parts[1]
                else z
                for z in zones
            ]
    return Township(
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
