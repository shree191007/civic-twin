"""Read-through endpoints over the precomputed analytics files."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from civictwin.api.state import AppState, get_state, require_planner

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
    return {**state.require("frontier"), **state.versions()}


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
