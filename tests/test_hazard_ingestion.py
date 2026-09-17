"""Tests for spec-patch sections 3, 4, 5 and 16 — the hazard ingestion layer."""
from __future__ import annotations

import json

import pytest

from gotham.analysis.forecast import impact_of
from gotham.config import DEFAULT
from gotham.engine.coordinator import Engine
from gotham.hazard.ingestion.api import UnsupportedForecast, ingest, ingest_file
from gotham.hazard.normalization.normalizer import (
    SCENARIO_FAMILIES,
    band_allocation,
    normalize,
    to_scenarios,
)
from gotham.hazard.schemas.forecast import (
    HazardForecast,
    HazardType,
    SeverityBand,
    bands_from_quantiles,
)
from gotham.ontology import Township


def forecast(**kw) -> HazardForecast:
    defaults = dict(
        id="f1",
        hazard_type=HazardType.RIVER_FLOOD,
        probability=0.8,
        start_time_h=6.0,
        duration_h=30.0,
        severity_distribution=(
            SeverityBand(0.5, 60.0, 110.0),
            SeverityBand(0.3, 110.0, 190.0),
            SeverityBand(0.2, 190.0, 300.0),
        ),
    )
    defaults.update(kw)
    return HazardForecast(**defaults)  # type: ignore[arg-type]


# ----------------------------------------------------------------- schema


def test_severity_probabilities_must_be_a_distribution() -> None:
    with pytest.raises(ValueError, match="sum to"):
        forecast(severity_distribution=(SeverityBand(0.2, 60.0, 110.0),))
    with pytest.raises(ValueError, match="at least one"):
        forecast(severity_distribution=())


def test_band_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="probability"):
        SeverityBand(1.4, 1.0, 2.0)
    with pytest.raises(ValueError, match="below low"):
        SeverityBand(1.0, 2.0, 1.0)


def test_forecast_round_trips() -> None:
    f = forecast()
    assert HazardForecast.from_dict(f.to_dict()) == f
    assert f.severity_range == (60.0, 300.0)
    assert 60.0 < f.expected_severity < 300.0


def test_quantiles_become_contiguous_bands() -> None:
    bands = bands_from_quantiles([(0.1, 60), (0.5, 110), (0.9, 220)])
    assert sum(b.probability for b in bands) == pytest.approx(1.0)
    for a, b in zip(bands, bands[1:]):
        assert a.high == b.low, "bands must not leave a gap"


# --------------------------------------------------------------- adapters


def test_native_payload_passes_through() -> None:
    f = ingest(forecast(source="IMD").to_dict())
    assert f.hazard_type is HazardType.RIVER_FLOOD
    assert f.source == "IMD"


def test_ensemble_quantiles_keep_the_providers_spread() -> None:
    f = ingest({"quantiles": {"0.1": 60, "0.5": 110, "0.9": 220}, "source": "ECMWF"})
    assert f.source == "ECMWF"
    assert f.severity_range[0] == 60.0
    assert f.severity_range[1] >= 220.0


def test_deterministic_value_is_widened_and_the_widening_is_declared() -> None:
    """A point forecast must not be passed off as certainty."""
    f = ingest({"value": 100.0, "source": "SingleRunModel"})
    low, high = f.severity_range
    assert low < 100.0 < high
    assert len(f.severity_distribution) > 1
    assert f.notes and "our assumption" in f.notes


def test_depth_in_metres_is_normalised_to_the_engines_units() -> None:
    f = ingest({"depth_m": 1.5, "source": "FloodModel"})
    assert all(b.unit == "mm" for b in f.severity_distribution)
    assert f.expected_severity > 50.0


def test_unrecognised_payload_is_refused_loudly() -> None:
    """Guessing at a hazard feed is how you simulate the wrong storm."""
    with pytest.raises(UnsupportedForecast):
        ingest({"wind_speed_kph": 120})


def test_ingest_file_reads_one_or_many(tmp_path) -> None:
    path = tmp_path / "feed.json"
    path.write_text(json.dumps([forecast(id="a").to_dict(), forecast(id="b").to_dict()]))
    assert [f.id for f in ingest_file(path)] == ["a", "b"]


# ------------------------------------------------------------ normalising


def test_normalising_is_idempotent() -> None:
    f = forecast()
    assert normalize(normalize(f)) == normalize(f)


def test_band_allocation_spends_every_scenario() -> None:
    bands = forecast().severity_distribution
    for n in (1, 7, 10, 199, 1000):
        allocation = band_allocation(bands, n)
        assert sum(c for _b, c in allocation) == n


def test_scenarios_follow_the_forecast_not_the_climate(town: Township) -> None:
    """The draw must reflect what the forecaster said, in their proportions."""
    f = forecast()
    scenarios = to_scenarios(f, town, DEFAULT, 100, seed=3)
    assert len(scenarios) == 100
    low, high = f.severity_range
    assert all(low <= s.rain_mm <= high for s in scenarios)
    in_first_band = sum(1 for s in scenarios if s.rain_mm <= 110.0)
    assert 40 <= in_first_band <= 60, "the 50% band should get about half the draws"


def test_scenarios_are_reproducible(town: Township) -> None:
    a = to_scenarios(forecast(), town, DEFAULT, 20, seed=5)
    b = to_scenarios(forecast(), town, DEFAULT, 20, seed=5)
    assert [s.rain_mm for s in a] == [s.rain_mm for s in b]
    assert a[0].asset_draws == b[0].asset_draws


def test_scenario_families_are_declared() -> None:
    assert HazardType.RIVER_FLOOD in SCENARIO_FAMILIES
    assert HazardType.URBAN_FLOOD in SCENARIO_FAMILIES
    assert HazardType.COMPOUND_FLOOD in SCENARIO_FAMILIES
    assert "drainage_capacity" in SCENARIO_FAMILIES[HazardType.URBAN_FLOOD]


# ----------------------------------------------------- forecast to impact


def test_impact_is_a_distribution_not_a_number(engine: Engine) -> None:
    impact = impact_of(forecast(), engine, DEFAULT, n_scenarios=40, seed=3)
    assert impact.person_hours.low <= impact.person_hours.central <= impact.person_hours.high
    assert impact.people_affected_range[0] <= impact.people_affected_range[1]
    assert impact.primary_uncertainty, "the drivers of the spread must be named"
    assert len(impact.bands) == 3


def test_worse_bands_have_worse_impact(engine: Engine) -> None:
    impact = impact_of(forecast(), engine, DEFAULT, n_scenarios=60, seed=3)
    centrals = [b.person_hours.central for b in impact.bands]
    assert centrals == sorted(centrals), f"impact should rise with severity: {centrals}"


def test_a_plan_reduces_the_forecast_impact(engine: Engine) -> None:
    from gotham.engine.contract import Overlay

    base = impact_of(forecast(), engine, DEFAULT, n_scenarios=40, seed=3)
    hardened = impact_of(
        forecast(),
        engine,
        DEFAULT,
        n_scenarios=40,
        seed=3,
        overlay=Overlay(interventions=("harden:S2", "harden:W1")),
    )
    assert hardened.person_hours.central <= base.person_hours.central
