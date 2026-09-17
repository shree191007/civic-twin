"""Projected-metres to WGS84 conversion and the GeoJSON payloads the map reads."""
from __future__ import annotations

import math
from dataclasses import dataclass

from civictwin.ontology import Portfolio, Township

EARTH_RADIUS_M = 6_378_137.0

#: Vertical separation of the portfolio planes in the frontend's layer-cake view.
LAYER_ALTITUDE_M: dict[Portfolio, float] = {
    Portfolio.TRANSPORT: 0.0,
    Portfolio.WATER: 220.0,
    Portfolio.ENERGY: 440.0,
    Portfolio.COMMS: 660.0,
    Portfolio.SERVICES: 880.0,
}


@dataclass(slots=True)
class Projector:
    """Local equirectangular projection around the township origin.

    Good to well under a metre over a 6 km square, and it needs no pyproj.
    """

    origin_lon: float
    origin_lat: float

    def to_lonlat(self, x_m: float, y_m: float) -> tuple[float, float]:
        lat = self.origin_lat + math.degrees(y_m / EARTH_RADIUS_M)
        lon = self.origin_lon + math.degrees(
            x_m / (EARTH_RADIUS_M * math.cos(math.radians(self.origin_lat)))
        )
        return (round(lon, 7), round(lat, 7))


def projector_for(township: Township) -> Projector:
    return Projector(township.origin_lonlat[0], township.origin_lonlat[1])


def bbox_of(township: Township, projector: Projector) -> list[float]:
    lo = projector.to_lonlat(0.0, 0.0)
    hi = projector.to_lonlat(township.extent_m, township.extent_m)
    return [lo[0], lo[1], hi[0], hi[1]]


def asset_properties(
    township: Township, asset_id: str, include_fragility: bool = True
) -> dict[str, object]:
    a = township.assets[asset_id]
    props: dict[str, object] = {
        "id": a.id,
        "kind": a.kind.value,
        "portfolio": a.portfolio.value,
        "name": a.name,
        "provenance": a.provenance.value,
        "capacity": a.capacity,
        "backup_hours": a.backup_hours,
        "fuel_hours": a.fuel_hours,
        "hand_m": a.hand_m,
        "x_m": a.x,
        "y_m": a.y,
        "scada_controlled": a.scada_controlled,
        "served_population": township.served_population(a.id),
        "layer_altitude_m": LAYER_ALTITUDE_M[a.portfolio],
    }
    if include_fragility:
        props["fragility_median_m"] = a.fragility_median_m
        props["fragility_beta"] = a.fragility_beta
    return props


def _zone_properties(zone: object, include_sensitive: bool) -> dict[str, object]:
    """Zone attributes, minus the dependency map for a public viewer.

    Which substation, feeder and tank serve a zone is the dependency graph in
    another form: given every zone, it reconstructs most of what the role gate
    on `/assets/{id}` and `/criticality` exists to withhold.
    """
    props: dict[str, object] = {
        "id": zone.id,  # type: ignore[attr-defined]
        "population": zone.population,  # type: ignore[attr-defined]
        "vulnerable_fraction": zone.vulnerable_fraction,  # type: ignore[attr-defined]
        "hand_m": zone.hand_m,  # type: ignore[attr-defined]
        "water_storage_hours": zone.water_storage_hours,  # type: ignore[attr-defined]
        "provenance": zone.provenance.value,  # type: ignore[attr-defined]
    }
    if include_sensitive:
        props.update(
            {
                "substation": zone.substation,  # type: ignore[attr-defined]
                "feeder": zone.feeder,  # type: ignore[attr-defined]
                "tank": zone.tank,  # type: ignore[attr-defined]
                "towers": list(zone.towers),  # type: ignore[attr-defined]
            }
        )
    return props


def build_township_payload(
    township: Township, projector: Projector, include_sensitive: bool = True
) -> dict[str, object]:
    """The whole model as GeoJSON, one feature collection per portfolio."""
    layers: dict[str, dict[str, object]] = {
        p.value: {"type": "FeatureCollection", "features": []} for p in Portfolio
    }
    for aid in sorted(township.assets):
        a = township.assets[aid]
        lon, lat = projector.to_lonlat(a.x, a.y)
        feature = {
            "type": "Feature",
            "id": a.id,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": asset_properties(township, aid, include_sensitive),
        }
        layers[a.portfolio.value]["features"].append(feature)  # type: ignore[union-attr]

    zones = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": z.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": list(projector.to_lonlat(z.x, z.y)),
                },
                "properties": _zone_properties(z, include_sensitive),
            }
            for z in township.zones
        ],
    }

    roads = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": e.id,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        list(projector.to_lonlat(*township.nodes[e.u])),
                        list(projector.to_lonlat(*township.nodes[e.v])),
                    ],
                },
                "properties": {
                    "id": e.id,
                    "lanes": e.lanes,
                    "free_flow_kph": e.free_flow_kph,
                    "hand_m": e.hand_m,
                    "host_asset": e.host_asset,
                    "provenance": e.provenance.value,
                    "layer_altitude_m": LAYER_ALTITUDE_M[Portfolio.TRANSPORT],
                },
            }
            for e in township.roads.values()
        ],
    }

    links: list[dict[str, object]] = []
    if include_sensitive:
        for ln in township.links:
            src = township.assets[ln.source]
            dst = township.assets[ln.target]
            links.append(
                {
                    "source": ln.source,
                    "target": ln.target,
                    "kind": ln.kind.value,
                    "provenance": ln.provenance.value,
                    "source_lonlat": list(projector.to_lonlat(src.x, src.y)),
                    "target_lonlat": list(projector.to_lonlat(dst.x, dst.y)),
                    "source_altitude_m": LAYER_ALTITUDE_M[src.portfolio],
                    "target_altitude_m": LAYER_ALTITUDE_M[dst.portfolio],
                }
            )

    summary: dict[str, int] = {}
    for a in township.assets.values():
        summary[a.provenance.value] = summary.get(a.provenance.value, 0) + 1

    return {
        "name": township.name,
        "bbox": bbox_of(township, projector),
        "extent_m": township.extent_m,
        "layers": layers,
        "zones": zones,
        "roads": roads,
        "links": links,
        "provenance_summary": summary,
    }
