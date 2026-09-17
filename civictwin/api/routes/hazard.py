"""Hazard ingestion and forecast-to-impact endpoints."""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from civictwin.analysis.forecast import impact_of
from civictwin.analysis.ledger import for_result
from civictwin.api.state import AppState, get_state
from civictwin.hazard.ingestion.api import UnsupportedForecast, ingest
from civictwin.hazard.normalization.normalizer import SCENARIO_FAMILIES
from civictwin.hazard.schemas.forecast import HazardType, UncertaintySource

logger = logging.getLogger(__name__)
router = APIRouter(tags=["hazard"])
State = Annotated[AppState, Depends(get_state)]

#: Kept small: this runs live, and the caller is waiting.
DEFAULT_FORECAST_SCENARIOS = 120
MAX_FORECAST_SCENARIOS = 400


@router.get("/hazard/schema")
def hazard_schema(state: State) -> dict[str, object]:
    """What a provider has to send, and what civic-twin does with it."""
    return {
        "hazard_types": [h.value for h in HazardType],
        "uncertainty_sources": [u.value for u in UncertaintySource],
        "scenario_families": {
            k.value: list(v) for k, v in SCENARIO_FAMILIES.items()
        },
        "required": [
            "hazard_type",
            "probability",
            "start_time_h",
            "duration_h",
            "severity_distribution",
        ],
        "optional": ["spatial_footprint", "uncertainty", "source", "issued_at", "notes"],
        "accepted_shapes": [
            {
                "adapter": "native",
                "example": {
                    "hazard_type": "river_flood",
                    "severity_distribution": [
                        {"probability": 0.5, "low": 60, "high": 110, "unit": "mm"}
                    ],
                },
            },
            {
                "adapter": "ensemble_quantiles",
                "example": {"quantiles": {"0.1": 60, "0.5": 110, "0.9": 220}},
            },
            {
                "adapter": "deterministic",
                "example": {"depth_m": 1.3},
                "note": (
                    "A single value is widened into a band, and the widening is "
                    "recorded as our assumption rather than the provider's."
                ),
            },
        ],
        "note": (
            "civic-twin does not forecast weather. It takes a hazard from an "
            "external model and answers what that hazard does to the system."
        ),
        **state.versions(),
    }


@router.post("/hazard/ingest")
def hazard_ingest(payload: dict[str, Any], state: State) -> dict[str, object]:
    """Normalise one provider payload into the internal hazard schema."""
    try:
        forecast = ingest(payload)
    except UnsupportedForecast as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "unsupported_forecast", "detail": str(exc)},
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_forecast", "detail": str(exc)},
        ) from exc
    return {"forecast": forecast.to_dict(), **state.versions()}


@router.post("/hazard/impact")
def hazard_impact(payload: dict[str, Any], state: State) -> dict[str, object]:
    """Forecast to impact: what this hazard does to the system, as a range."""
    body = dict(payload)
    n = int(body.pop("n_scenarios", DEFAULT_FORECAST_SCENARIOS))
    interventions = tuple(body.pop("interventions", ()) or ())
    if not 1 <= n <= MAX_FORECAST_SCENARIOS:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_scenario_count", "max": MAX_FORECAST_SCENARIOS},
        )
    try:
        forecast = ingest(body)
    except (UnsupportedForecast, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_forecast", "detail": str(exc)},
        ) from exc

    from civictwin.engine.contract import Overlay

    impact = impact_of(
        forecast,
        state.engine,
        state.cfg,
        n_scenarios=n,
        overlay=Overlay(interventions=interventions),
    )
    ledger = for_result(
        state.township,
        state.cfg,
        claim=f"Impact of {forecast.id}",
        assets=impact.most_likely_first_failure,
        forecast=forecast,
    )
    return {
        "forecast": forecast.to_dict(),
        "impact": impact.to_dict(),
        "assumptions": ledger.to_dict(),
        **state.versions(),
    }
