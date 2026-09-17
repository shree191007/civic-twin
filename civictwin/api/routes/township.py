"""Township structure, asset detail, and dependency traces."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from civictwin.api.geo import asset_properties
from civictwin.api.state import AppState, get_state, require_planner
from civictwin.ontology import LinkKind, Service

router = APIRouter(tags=["township"])

State = Annotated[AppState, Depends(get_state)]

TRACE_SERVICE_BY_KIND = {
    LinkKind.POWERS: Service.ENERGY,
    LinkKind.SUPPLIES_WATER: Service.WATER,
    LinkKind.BACKHAULS: Service.COMMS,
}


@router.get("/township")
def get_township(state: State) -> dict[str, object]:
    payload = (
        state.township_payload_public if state.is_public else state.township_payload
    )
    return {**payload, **state.versions()}


def _criticality_for(state: AppState, asset_id: str) -> dict[str, object] | None:
    rows = state.results.get("criticality") or []
    for row in rows:
        if row.get("asset_id") == asset_id:
            return row
    return None


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, state: State) -> dict[str, object]:
    if asset_id not in state.township.assets:
        raise HTTPException(
            status_code=404, detail={"error": "unknown_asset", "id": asset_id}
        )
    town = state.township
    out: dict[str, object] = {
        "asset": asset_properties(town, asset_id, include_fragility=not state.is_public),
        **state.versions(),
    }
    if not state.is_public:
        out["upstream"] = sorted(town.upstream(asset_id))
        out["downstream"] = sorted(town.downstream(asset_id))
        out["criticality"] = _criticality_for(state, asset_id)
        out["spofs"] = [
            s
            for s in (state.results.get("spofs") or [])
            if s.get("shared_asset") == asset_id
            or asset_id in (s.get("redundant_group") or [])
        ]
        from civictwin.analysis.criticality import explain_chains

        out["explanations"] = explain_chains(town, asset_id)

    if state.is_public:
        # The list of zones an asset serves is the downstream half of the
        # dependency map, which this role does not get.
        return out

    affected = []
    for z in town.zones:
        if asset_id not in town.zone_dependency_closure(z.id):
            continue
        services = []
        if asset_id in {z.substation, z.feeder} or asset_id in town.upstream(z.feeder):
            services.append(Service.ENERGY.value)
        if asset_id == z.tank or asset_id in town.upstream(z.tank):
            services.append(Service.WATER.value)
        if asset_id in z.towers or any(
            asset_id in town.upstream(t) for t in z.towers
        ):
            services.append(Service.COMMS.value)
        affected.append(
            {"id": z.id, "population": z.population, "services": sorted(set(services))}
        )
    out["affected_zones"] = affected
    return out


@router.get("/assets/{asset_id}/trace")
def trace(
    asset_id: str,
    state: State,
    direction: str = Query("down", pattern="^(up|down)$"),
    kinds: str | None = Query(None, description="comma-separated LinkKind values"),
) -> dict[str, object]:
    require_planner(state)
    town = state.township
    if asset_id not in town.assets:
        raise HTTPException(
            status_code=404, detail={"error": "unknown_asset", "id": asset_id}
        )
    selected = (
        {LinkKind(k.strip()) for k in kinds.split(",") if k.strip()} if kinds else None
    )
    reach = (
        town.downstream(asset_id, selected)
        if direction == "down"
        else town.upstream(asset_id, selected)
    )
    members = reach | {asset_id}
    nodes = [
        {
            "id": aid,
            "kind": town.assets[aid].kind.value,
            "portfolio": town.assets[aid].portfolio.value,
            "name": town.assets[aid].name,
            "provenance": town.assets[aid].provenance.value,
            "functionality": 1.0,
            "is_root": aid == asset_id,
        }
        for aid in sorted(members)
    ]
    edges = [
        {"source": ln.source, "target": ln.target, "kind": ln.kind.value}
        for ln in town.links
        if ln.source in members
        and ln.target in members
        and (selected is None or ln.kind in selected)
    ]
    population = sum(
        z.population
        for z in town.zones
        if members & town.zone_dependency_closure(z.id)
    ) if direction == "down" else town.served_population(asset_id)
    return {
        "nodes": nodes,
        "edges": edges,
        "affected_population": population,
        **state.versions(),
    }
