"""Ad-hoc and precomputed scenario endpoints."""
from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from gotham.api.schemas import ScenarioRequest
from gotham.api.state import AppState, get_state
from gotham.engine.contract import HazardScenario, Overlay
from gotham.engine.hazard import _derived_uniform, gumbel_cdf
from gotham.ontology import Service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["scenarios"])
State = Annotated[AppState, Depends(get_state)]


#: timeline `totals` key -> the people_summary key it feeds
PEAK_KEYS = {
    "people_no_power": "peak_no_power",
    "people_no_water": "peak_no_water",
    "people_no_comms": "peak_no_comms",
    "people_no_health": "peak_no_health",
}


def peak_counts(frames: Any) -> dict[str, int]:
    """Highest number of people without each service at any point in the event."""
    peaks = {key: 0 for key in PEAK_KEYS.values()}
    for frame in frames or ():
        totals = frame.totals if hasattr(frame, "totals") else (frame.get("totals") or {})
        for source, target in PEAK_KEYS.items():
            value = totals.get(source)
            if value is not None:
                peaks[target] = max(peaks[target], int(value))
    return peaks


def _stable_seed(key: str) -> int:
    """A seed that is the same in every process.

    Python randomises `hash()` per process, so a seed derived from it made the
    same request give different repair-time draws after a restart.
    """
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:6], "big")


def _people_summary(result: Any, township: Any) -> dict[str, float]:
    """Peak people without each service, and total weighted person-hours."""
    del township
    return {
        **peak_counts(result.timeline),
        "person_hours_lost": round(result.weighted_loss_ph, 2),
    }


@router.post("/scenarios")
def run_scenario(request: ScenarioRequest, state: State) -> dict[str, object]:
    key = request.cache_key()
    cached = state.cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    town = state.township
    unknown = [
        aid
        for aid in list(request.forced_failures) + list(request.invulnerable)
        if aid not in town.assets
    ]
    if unknown:
        raise HTTPException(
            status_code=404, detail={"error": "unknown_asset", "id": unknown[0]}
        )
    unknown_iv = [i for i in request.interventions if i not in state.catalogue]
    if unknown_iv:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_intervention", "id": unknown_iv[0]},
        )

    scenario_id = state.scenario_id_for(key)
    cdf = gumbel_cdf(
        request.rain_mm,
        state.cfg.hazard.gumbel_loc_mm,
        state.cfg.hazard.gumbel_scale_mm,
    )
    # Common random numbers: the dice an asset rolls belong to the *storm*, not
    # to the request. Keying them on the whole request -- interventions
    # included -- re-rolled every asset the moment a plan was applied, so a
    # before-and-after comparison was comparing two different storms and the
    # difference could not be attributed to the plan.
    hazard_key = request.hazard_key()
    scenario = HazardScenario(
        id=scenario_id,
        seed=_stable_seed(hazard_key),
        rain_mm=request.rain_mm,
        field_seed=request.field_seed,
        onset_hour=request.onset_hour,
        asset_draws={
            aid: _derived_uniform(request.field_seed, f"{hazard_key}:{aid}")
            for aid in sorted(town.assets)
        },
        return_period_y=None if cdf >= 1.0 else 1.0 / max(1e-12, 1.0 - cdf),
        label="ad hoc",
    )
    overlay = Overlay(
        interventions=tuple(request.interventions),
        forced_failures=tuple(request.forced_failures),
        invulnerable=tuple(request.invulnerable),
    )
    try:
        result = state.engine.simulate(scenario, overlay, record=request.record)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("simulation failed")
        raise HTTPException(
            status_code=500,
            detail={"error": "simulation_failed", "detail": str(exc)},
        ) from exc

    payload = {
        "scenario_id": scenario_id,
        "scenario": scenario.summary(),
        "result": result.to_dict(include_timeline=request.record),
        "people_summary": _people_summary(result, town),
        "cached": False,
        **state.versions(),
    }
    state.cache_put(key, payload)
    return payload


@router.get("/scenarios/precomputed")
def precomputed(state: State) -> list[dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    for scenario_id, plan_id in state.hero:
        grouped.setdefault(scenario_id, []).append(plan_id)
    out = []
    for scenario_id, plans in sorted(grouped.items()):
        sample = state.hero[(scenario_id, sorted(plans)[0])]
        summary = sample.get("scenario", {})
        out.append(
            {
                "scenario_id": scenario_id,
                "rain_mm": summary.get("rain_mm"),
                "return_period_y": summary.get("return_period_y"),
                "plans": sorted(plans),
                **state.versions(),
            }
        )
    return out


def _summarise(payload: dict[str, Any]) -> dict[str, Any]:
    peaks = peak_counts(payload.get("timeline") or [])
    return {
        "scenario_id": payload.get("scenario_id"),
        "plan_id": payload.get("plan_id"),
        "person_hours_lost": payload.get("weighted_loss_ph", 0.0),
        "vulnerable_loss_ph": payload.get("vulnerable_loss_ph", 0.0),
        "recovery_90_h": payload.get("recovery_90_h", {}),
        "damaged_assets": sorted(payload.get("damaged_assets", {})),
        **peaks,
    }


@router.get("/scenarios/compare")
def compare(
    state: State,
    a: str = Query(..., description="scenario_id:plan"),
    b: str = Query(..., description="scenario_id:plan"),
) -> dict[str, object]:
    def fetch(spec: str) -> dict[str, Any]:
        scenario_id, _, plan_id = spec.partition(":")
        payload = state.hero.get((scenario_id, plan_id or "baseline"))
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_scenario", "id": spec},
            )
        return payload

    pa, pb = fetch(a), fetch(b)
    sa, sb = _summarise(pa), _summarise(pb)
    recovery_delta = {
        key: _delta(sb["recovery_90_h"].get(key), sa["recovery_90_h"].get(key))
        for key in sorted(set(sa["recovery_90_h"]) | set(sb["recovery_90_h"]))
    }
    return {
        "a": sa,
        "b": sb,
        "delta": {
            "person_hours_lost": sb["person_hours_lost"] - sa["person_hours_lost"],
            "peak_no_power": sb["peak_no_power"] - sa["peak_no_power"],
            "peak_no_water": sb["peak_no_water"] - sa["peak_no_water"],
            "peak_no_comms": sb["peak_no_comms"] - sa["peak_no_comms"],
            "peak_no_health": sb["peak_no_health"] - sa["peak_no_health"],
            "recovery_90_h": recovery_delta,
        },
        "assets_changed": sorted(
            set(sa["damaged_assets"]) ^ set(sb["damaged_assets"])
        ),
        **state.versions(),
    }


def _delta(b: object, a: object) -> float | None:
    try:
        fb, fa = float(b), float(a)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if fb != fb or fa != fa or fb in (float("inf"),) or fa in (float("inf"),):
        return None
    return fb - fa


def envelope(payload: dict[str, Any], state: AppState) -> dict[str, Any]:
    """Wrap a stored hero result in the same envelope POST /scenarios returns.

    The files on disk hold a bare SimResult; clients should not have to handle
    two different shapes for the same thing.
    """
    result = {
        k: v
        for k, v in payload.items()
        if k not in {"scenario", "plan_id", "interventions", "data_version", "model_version"}
    }
    peaks = peak_counts(payload.get("timeline") or [])
    return {
        "scenario_id": payload.get("scenario_id"),
        "scenario": payload.get("scenario", {}),
        "plan_id": payload.get("plan_id"),
        "interventions": payload.get("interventions", []),
        "result": result,
        "people_summary": {
            **peaks,
            "person_hours_lost": payload.get("weighted_loss_ph", 0.0),
        },
        "cached": True,
        **state.versions(),
    }


@router.get("/scenarios/{scenario_id}")
def get_precomputed(
    scenario_id: str, state: State, plan: str = Query("baseline")
) -> dict[str, object]:
    payload = state.hero.get((scenario_id, plan))
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_scenario", "id": f"{scenario_id}:{plan}"},
        )
    return envelope(payload, state)


@router.get("/restoration/{scenario_id}")
def restoration(scenario_id: str, state: State) -> dict[str, object]:
    data = state.results.get("restoration") or {}
    payload = data.get(scenario_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_scenario", "id": scenario_id},
        )
    return {"scenario_id": scenario_id, "policies": payload, **state.versions()}
