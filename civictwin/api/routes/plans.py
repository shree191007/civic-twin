"""Investment plans and the decision log."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from civictwin.analysis.criticality import explain_chains
from civictwin.api.schemas import DecisionRequest
from civictwin.api.state import AppState, get_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["plans"])
State = Annotated[AppState, Depends(get_state)]

DECISIONS_FILE = "decisions.jsonl"


#: What a measure with no single target asset does, in words.
KIND_WHY = {
    "operational": "A change to operating rules; it applies across the whole network.",
    "tie": "Lets a healthy feeder pick up the load of a failed neighbour.",
}


#: Assets whose value is not a supply chain to a zone, described by role.
ASSET_WHY = {
    "stormwater_pump": "{name} keeps rainwater draining from its low-lying basin; if it stops, the flood there gets deeper.",
    "bridge": "{name} is one of only two river crossings, so repair crews and fuel trucks depend on it.",
    "depot": "{name} is where repair crews start from.",
}


def why_for(state: AppState, target: str, kind: str = "") -> str:
    """A one-line reason an intervention is worth buying, from the analytics."""
    if target not in state.township.assets:
        return KIND_WHY.get(kind, "Applies across the network rather than to one asset.")
    chains = explain_chains(state.township, target, limit=1)
    if chains:
        return chains[0]
    asset = state.township.assets[target]
    if asset.kind.value in ASSET_WHY:
        return ASSET_WHY[asset.kind.value].format(name=asset.name)
    population = state.township.served_population(target)
    if population <= 0:
        return f"{asset.name} does not supply any zone directly."
    return f"{target} serves {population:,} people."


@router.get("/plans")
def get_plan(
    state: State, budget: float = Query(30_000_000.0, ge=0.0)
) -> dict[str, object]:
    if not state.plans:
        raise HTTPException(
            status_code=503,
            detail={"error": "results_missing", "file": "plans/*.json"},
        )
    affordable = [b for b in sorted(state.plans) if b <= budget]
    chosen_budget = affordable[-1] if affordable else min(state.plans)
    plan: dict[str, Any] = state.plans[chosen_budget]

    frontier = (state.results.get("frontier") or {}).get("steps", [])
    frequency = plan.get("selection_frequency") or {}
    reduction_by_id = {
        s["intervention_id"]: s for s in plan.get("steps", [])
    }

    items = []
    previous_cvar = plan.get("cvar_before", 0.0)
    for iid in plan.get("interventions", []):
        iv = state.catalogue.get(iid)
        step = reduction_by_id.get(iid, {})
        cvar_after = step.get("cvar_after", previous_cvar)
        items.append(
            {
                "id": iid,
                "label": iv.label if iv else iid,
                "kind": iv.kind if iv else "unknown",
                "cost_inr": iv.cost_inr if iv else 0.0,
                "target_asset": iv.target if iv else iid,
                "selection_frequency": frequency.get(iid),
                "cvar_reduction_ph": round(previous_cvar - cvar_after, 2),
                "why": why_for(state, iv.primary_target, iv.kind) if iv else "",
            }
        )
        previous_cvar = cvar_after

    index = next(
        (
            i
            for i, s in enumerate(frontier)
            if s.get("cumulative_cost_inr", 0.0) > plan.get("cost_inr", 0.0)
        ),
        len(frontier),
    )
    return {
        "budget_inr": budget,
        "plan_budget_inr": chosen_budget,
        "plan": plan,
        "interventions": items,
        "frontier_position": {"index": index, "of": len(frontier)},
        **state.versions(),
    }


@router.post("/decisions")
def record_decision(request: DecisionRequest, state: State) -> dict[str, object]:
    record = {
        **request.model_dump(exclude_none=True),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_at_epoch": time.time(),
        **state.versions(),
    }
    path = state.results_path / DECISIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    # Two councillors recording a decision at the same moment must not
    # interleave half a line each. An advisory lock keeps the append whole.
    with path.open("a", encoding="utf-8") as fh:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):  # pragma: no cover - not on this platform
            logger.debug("file locking unavailable; appending without it")
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return record


@router.get("/decisions")
def list_decisions(state: State) -> dict[str, object]:
    path = state.results_path / DECISIONS_FILE
    records: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn line from an older, unlocked write. Skip it rather
                    # than failing the whole listing.
                    logger.warning("skipping malformed decision record")
    return {"count": len(records), "items": records, **state.versions()}
