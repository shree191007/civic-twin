"""Tests for spec-patch sections 18 and 19 — historical replay and hindcasting.

The township is synthetic, so there is no real flood to validate against. What
is tested here is the harness: that it scores a replay correctly, that it
reports a counterfactual as an estimate rather than a fact, and that with no
documented event it says so instead of returning a score.
"""
from __future__ import annotations

import json

import pytest

from gotham.analysis.hindcast import (
    ConfusionScore,
    ObservedEvent,
    compare_sets,
    counterfactual,
    load_events,
    replay,
)
from gotham.config import DEFAULT
from gotham.engine.coordinator import Engine
from gotham.hazard.schemas.forecast import HazardForecast, HazardType, SeverityBand
from gotham.ontology import Township


@pytest.fixture(scope="module")
def event() -> ObservedEvent:
    """A stand-in for a documented flood, used only to exercise the harness."""
    return ObservedEvent(
        id="synthetic-2015-12",
        label="Synthetic December flood (harness fixture, not a real event)",
        hazard=HazardForecast(
            id="synthetic-2015-12-hazard",
            hazard_type=HazardType.RIVER_FLOOD,
            probability=1.0,
            start_time_h=0.0,
            duration_h=36.0,
            severity_distribution=(SeverityBand(1.0, 210.0, 260.0),),
            source="event record",
        ),
        disrupted_sectors=("energy", "water", "comms"),
        disrupted_assets=("S2", "P2", "W1"),
        source="fixture",
    )


def test_confusion_score_arithmetic() -> None:
    score = ConfusionScore(observed=10, predicted=8, hits=6)
    assert score.precision == pytest.approx(0.75)
    assert score.recall == pytest.approx(0.6)
    assert score.iou == pytest.approx(6 / 12)
    assert score.false_alarm_ratio == pytest.approx(0.25)


def test_confusion_score_handles_nothing_predicted() -> None:
    score = compare_sets(["a", "b"], [])
    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.false_alarm_ratio == 0.0


def test_replay_scores_exposure_and_cascade(
    event: ObservedEvent, engine: Engine
) -> None:
    record = replay(event, engine, DEFAULT, n_scenarios=12, seed=4)
    assert record["event"] == event.id
    assert record["scenarios"] == 12
    assert record["exposure"]["n_observed"] == 3
    assert record["exposure"]["recall"] > 0.0, "the model should find the flooded assets"
    assert record["cascade"]["recall"] > 0.0
    assert record["modelled_loss_ph"]["low"] <= record["modelled_loss_ph"]["high"]


def test_replay_reports_missing_evidence_rather_than_scoring_it(
    event: ObservedEvent, engine: Engine
) -> None:
    """No observed flood extent means no footprint score, not a perfect one."""
    record = replay(event, engine, DEFAULT, n_scenarios=8, seed=4)
    assert record["hazard"]["skipped"] is True
    assert "no observed flood extent" in record["hazard"]["reason"]


def test_counterfactual_is_labelled_as_an_estimate(
    event: ObservedEvent, engine: Engine
) -> None:
    record = counterfactual(
        event, engine, DEFAULT, ["harden:S2", "harden:W1"], n_scenarios=10, seed=4
    )
    assert record["label"] == "model-estimated counterfactual impact"
    assert "not a measurement" in record["caveat"]
    assert record["with_plan_ph"] <= record["baseline_ph"]
    assert record["avoided_ph"] >= 0.0


def test_no_event_file_means_no_events(tmp_path) -> None:
    assert load_events(tmp_path / "absent.json") == []


def test_events_round_trip_through_json(tmp_path, event: ObservedEvent) -> None:
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": event.id,
                    "label": event.label,
                    "hazard": event.hazard.to_dict(),
                    "disrupted_assets": list(event.disrupted_assets),
                    "disrupted_sectors": list(event.disrupted_sectors),
                }
            ]
        )
    )
    loaded = load_events(path)
    assert len(loaded) == 1
    assert loaded[0].id == event.id
    assert loaded[0].hazard.hazard_type is HazardType.RIVER_FLOOD
