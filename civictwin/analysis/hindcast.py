"""Replaying a real flood against the model.

The hardest test of a model like this is not whether it is internally
consistent but whether it would have told you something true about an event
that actually happened. This module replays a documented flood: it takes the
hazard as recorded, freezes everything known before the event, runs the model,
and compares what it predicted against what was reported.

Without a documented event the honest output is a `skipped` record, not a
passing score.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from civictwin.config import Config
from civictwin.engine.contract import Overlay
from civictwin.engine.coordinator import Engine
from civictwin.hazard.normalization.normalizer import to_scenarios
from civictwin.hazard.schemas.forecast import HazardForecast
from civictwin.ontology import Service, Township

logger = logging.getLogger(__name__)

#: A zone counts as disrupted once a service drops below this.
DISRUPTED_BELOW = 0.5


@dataclass(slots=True)
class ObservedEvent:
    """What was recorded about a real flood, as reported at the time."""

    id: str
    label: str
    hazard: HazardForecast
    flooded_edges: tuple[str, ...] = ()
    disrupted_sectors: tuple[str, ...] = ()
    disrupted_assets: tuple[str, ...] = ()
    outages: tuple[dict[str, Any], ...] = ()
    source: str = "unknown"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ObservedEvent:
        return ObservedEvent(
            id=str(d["id"]),
            label=str(d.get("label", d["id"])),
            hazard=HazardForecast.from_dict(d["hazard"]),
            flooded_edges=tuple(d.get("flooded_edges", ())),
            disrupted_sectors=tuple(d.get("disrupted_sectors", ())),
            disrupted_assets=tuple(d.get("disrupted_assets", ())),
            outages=tuple(d.get("outages", ())),
            source=str(d.get("source", "unknown")),
        )


@dataclass(slots=True)
class ConfusionScore:
    """Overlap between what was predicted and what was observed."""

    observed: int
    predicted: int
    hits: int

    @property
    def precision(self) -> float:
        return self.hits / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.hits / self.observed if self.observed else 0.0

    @property
    def iou(self) -> float:
        union = self.observed + self.predicted - self.hits
        return self.hits / union if union else 0.0

    @property
    def false_alarm_ratio(self) -> float:
        return 1.0 - self.precision if self.predicted else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_observed": self.observed,
            "n_predicted": self.predicted,
            "hits": self.hits,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "iou": round(self.iou, 3),
            "false_alarm_ratio": round(self.false_alarm_ratio, 3),
        }


def compare_sets(observed: Sequence[str], predicted: Sequence[str]) -> ConfusionScore:
    obs, pred = set(observed), set(predicted)
    return ConfusionScore(len(obs), len(pred), len(obs & pred))


def replay(
    event: ObservedEvent,
    engine: Engine,
    cfg: Config,
    n_scenarios: int = 60,
    seed: int = 19,
) -> dict[str, Any]:
    """Run a documented event through the model and score it against the record.

    Four checks, following spec-patch section 18:
      A. hazard footprint against the observed flood extent
      B. infrastructure exposure against what was reported damaged
      C. cascade against the sectors documented as disrupted
      D. decision counterfactual: the same event with a plan in place
    """
    township = engine.township
    scenarios = to_scenarios(event.hazard, township, cfg, n_scenarios, seed)
    results = [
        engine.simulate(s, Overlay(), record=True, _skip_amplification=True)
        for s in scenarios
    ]

    # A. hazard footprint: road segments the model puts under water
    predicted_edges: set[str] = set()
    for result in results:
        for frame in result.timeline or ():
            predicted_edges.update(frame.closed_roads)
    hazard_score = (
        compare_sets(event.flooded_edges, sorted(predicted_edges)).to_dict()
        if event.flooded_edges
        else {"skipped": True, "reason": "no observed flood extent in the record"}
    )

    # B. exposure: assets the model damages
    predicted_assets: set[str] = set()
    for result in results:
        predicted_assets.update(result.damaged_assets)
    exposure_score = (
        compare_sets(event.disrupted_assets, sorted(predicted_assets)).to_dict()
        if event.disrupted_assets
        else {"skipped": True, "reason": "no observed asset damage in the record"}
    )

    # C. cascade: which services the model takes down
    predicted_sectors: set[str] = set()
    for result in results:
        for frame in result.timeline or ():
            for services in frame.zones.values():
                for service in Service:
                    if services.get(service.value, 1.0) < DISRUPTED_BELOW:
                        predicted_sectors.add(service.value)
    cascade_score = (
        compare_sets(event.disrupted_sectors, sorted(predicted_sectors)).to_dict()
        if event.disrupted_sectors
        else {"skipped": True, "reason": "no documented disruption in the record"}
    )

    losses = [r.weighted_loss_ph for r in results]
    return {
        "event": event.id,
        "label": event.label,
        "source": event.source,
        "scenarios": len(scenarios),
        "hazard": hazard_score,
        "exposure": exposure_score,
        "cascade": cascade_score,
        "predicted_sectors": sorted(predicted_sectors),
        "modelled_loss_ph": {
            "low": round(min(losses), 1),
            "central": round(sum(losses) / len(losses), 1),
            "high": round(max(losses), 1),
        },
    }


def counterfactual(
    event: ObservedEvent,
    engine: Engine,
    cfg: Config,
    interventions: Sequence[str],
    n_scenarios: int = 60,
    seed: int = 19,
) -> dict[str, Any]:
    """The same event, with a plan in place. Reported as an estimate, not a fact.

    This is the "what if we had already done this" number. It is a model
    output about a world that did not happen, and it is labelled as such.
    """
    scenarios = to_scenarios(event.hazard, engine.township, cfg, n_scenarios, seed)
    before = [
        engine.simulate(s, Overlay(), _skip_amplification=True).weighted_loss_ph
        for s in scenarios
    ]
    after = [
        engine.simulate(
            s, Overlay(interventions=tuple(interventions)), _skip_amplification=True
        ).weighted_loss_ph
        for s in scenarios
    ]
    mean_before = sum(before) / len(before)
    mean_after = sum(after) / len(after)
    return {
        "event": event.id,
        "interventions": list(interventions),
        "baseline_ph": round(mean_before, 1),
        "with_plan_ph": round(mean_after, 1),
        "avoided_ph": round(mean_before - mean_after, 1),
        "avoided_pct": (
            0.0 if mean_before <= 0 else round(100.0 * (mean_before - mean_after) / mean_before, 1)
        ),
        "label": "model-estimated counterfactual impact",
        "caveat": (
            "This compares two model runs of an event that happened once. It is "
            "an estimate of what the plan would have changed, not a measurement."
        ),
    }


def load_events(path: Path) -> list[ObservedEvent]:
    """Read documented events from a JSON file, if one has been supplied."""
    if not Path(path).exists():
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    payloads = data if isinstance(data, list) else [data]
    return [ObservedEvent.from_dict(p) for p in payloads]
