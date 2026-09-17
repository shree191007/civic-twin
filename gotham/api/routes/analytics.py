"""Read-through endpoints over the precomputed analytics files."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from gotham.api.state import AppState, get_state, require_planner

router = APIRouter(tags=["analytics"])
State = Annotated[AppState, Depends(get_state)]


@router.get("/risk")
def risk(state: State) -> dict[str, object]:
    return {**state.require("risk"), **state.versions()}


@router.get("/criticality")
def criticality(
    state: State,
    portfolio: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, object]:
    require_planner(state)
    rows: list[dict[str, Any]] = state.require("criticality")
    if portfolio:
        rows = [r for r in rows if r.get("portfolio") == portfolio]
    return {
        "count": len(rows),
        "items": rows[:limit],
        **state.versions(),
    }


@router.get("/spofs")
def spofs(state: State, limit: int = Query(20, ge=1, le=100)) -> dict[str, object]:
    require_planner(state)
    rows: list[dict[str, Any]] = state.require("spofs")
    return {"count": len(rows), "items": rows[:limit], **state.versions()}


@router.get("/critical-sets")
def critical_sets(
    state: State, limit: int = Query(50, ge=1, le=200)
) -> dict[str, object]:
    require_planner(state)
    rows: list[dict[str, Any]] = state.require("critical_sets")
    return {"count": len(rows), "items": rows[:limit], **state.versions()}


@router.get("/frontier")
def frontier(state: State) -> dict[str, object]:
    """The greedy frontier, with each step described well enough to list.

    Every prefix of the steps is a plan, so a client can show the package for
    any budget without another optimisation run.
    """
    from gotham.api.routes.plans import why_for

    data = dict(state.require("frontier"))
    steps = []
    for step in data.get("steps", []):
        iv = state.catalogue.get(step.get("intervention_id", ""))
        steps.append(
            {
                **step,
                "kind": iv.kind if iv else "unknown",
                "cost_inr": iv.cost_inr if iv else None,
                "target_asset": iv.primary_target if iv else None,
                "why": why_for(state, iv.primary_target, iv.kind) if iv else "",
            }
        )
    data["steps"] = steps
    return {**data, **state.versions()}


@router.get("/baselines")
def baselines(state: State) -> dict[str, object]:
    return {**state.require("baselines"), **state.versions()}


@router.get("/voi")
def voi(state: State) -> dict[str, object]:
    return {"items": state.results.get("voi", {}), **state.versions()}


@router.get("/sensitivity")
def sensitivity(state: State) -> dict[str, object]:
    return {"items": state.results.get("sensitivity", []), **state.versions()}


@router.get("/ensemble")
def ensemble(state: State) -> dict[str, object]:
    return {**(state.results.get("ensemble") or {}), **state.versions()}
