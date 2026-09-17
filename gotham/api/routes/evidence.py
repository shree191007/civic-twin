"""Assumption ledger and effective-redundancy endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from gotham.analysis.ledger import for_asset, for_result, provenance_summary
from gotham.analysis.redundancy import redundancy_groups, system_score
from gotham.api.state import AppState, get_state

router = APIRouter(tags=["evidence"])
State = Annotated[AppState, Depends(get_state)]


@router.get("/assumptions")
def assumptions(
    state: State,
    claim: str = Query("Headline risk figures"),
    asset: str | None = Query(None),
) -> dict[str, object]:
    """Why gotham thinks what it thinks about an asset or a result."""
    if asset is not None:
        if asset not in state.township.assets:
            raise HTTPException(
                status_code=404, detail={"error": "unknown_asset", "id": asset}
            )
        ledger = for_asset(state.township, asset, state.cfg)
    else:
        ledger = for_result(state.township, state.cfg, claim)
    return {
        "ledger": ledger.to_dict(),
        "provenance": provenance_summary(state.township),
        **state.versions(),
    }


@router.get("/redundancy")
def redundancy(state: State) -> dict[str, object]:
    """Effective redundancy: how much of the redundancy on paper is real."""
    groups = redundancy_groups(state.township)
    return {
        "system_score": round(system_score(groups), 3),
        "weak_groups": sum(1 for g in groups if g.is_weak),
        "groups": [g.to_dict() for g in groups],
        **state.versions(),
    }


@router.get("/objectives")
def objectives(state: State) -> dict[str, object]:
    """The priority modes a planner can optimise against."""
    from gotham.analysis.objectives import OBJECTIVES

    return {
        "objectives": [o.to_dict() for o in OBJECTIVES.values()],
        "default": "balanced",
        **state.versions(),
    }
