"""Monte Carlo driver: scenario sets and column-oriented loss tables."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from gotham.config import Config
from gotham.engine.contract import HazardScenario, Overlay
from gotham.engine.coordinator import Engine
from gotham.engine.hazard import sample_scenarios
from gotham.ontology import Service, Township

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScenarioSet:
    """A named collection of scenario years drawn from one seed."""

    name: str
    seed: int
    scenarios: list[HazardScenario]

    def __len__(self) -> int:
        return len(self.scenarios)


def make_scenario_set(
    township: Township, cfg: Config, name: str, n: int, seed: int
) -> ScenarioSet:
    return ScenarioSet(name=name, seed=seed, scenarios=sample_scenarios(township, cfg, n, seed))


@dataclass(slots=True)
class LossTable:
    """Losses for one overlay across a scenario set, column-oriented."""

    scenario_ids: list[str]
    weighted: np.ndarray
    by_service: dict[Service, np.ndarray]
    by_zone: dict[str, np.ndarray]
    vulnerable: np.ndarray
    damaged: list[frozenset[str]]
    #: Slowest portfolio recovery in each scenario, `inf` where it never does.
    recovery_h: np.ndarray | None = None
    overlay_hash: str = ""

    def __len__(self) -> int:
        return len(self.scenario_ids)

    def to_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for i, sid in enumerate(self.scenario_ids):
            row: dict[str, object] = {
                "scenario_id": sid,
                "weighted": float(self.weighted[i]),
                "vulnerable": float(self.vulnerable[i]),
            }
            for service, values in self.by_service.items():
                row[f"loss_{service.value}"] = float(values[i])
            for zone, values in self.by_zone.items():
                row[f"zone_{zone}"] = float(values[i])
            rows.append(row)
        return rows

    def save_parquet(self, path: Path) -> bool:
        """Write the table as Parquet. Returns False if pyarrow is missing."""
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:  # pragma: no cover - optional dependency
            logger.warning("pyarrow unavailable; skipping %s", path)
            return False
        rows = self.to_rows()
        columns = {k: [r[k] for r in rows] for k in rows[0]} if rows else {}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table(columns), path)
        return True


def run_set(
    engine: Engine,
    scenario_set: ScenarioSet,
    overlay: Overlay = Overlay(),
    n_jobs: int = 1,
) -> LossTable:
    """Simulate every scenario in the set under one overlay."""
    results = engine.simulate_many(scenario_set.scenarios, overlay, n_jobs=n_jobs)
    n = len(results)
    zones = [z.id for z in engine.township.zones]
    table = LossTable(
        scenario_ids=[r.scenario_id for r in results],
        weighted=np.array([r.weighted_loss_ph for r in results]),
        by_service={
            s: np.array([r.loss_by_service_ph.get(s, 0.0) for r in results])
            for s in Service
        },
        by_zone={
            z: np.array([r.loss_by_zone_ph.get(z, 0.0) for r in results]) for z in zones
        },
        vulnerable=np.array([r.vulnerable_loss_ph for r in results]),
        damaged=[frozenset(r.damaged_assets) for r in results],
        recovery_h=np.array(
            [max(r.recovery_90_h.values(), default=0.0) for r in results]
        ),
        overlay_hash=overlay.hash(),
    )
    assert len(table) == n
    return table


def run_set_subset(
    engine: Engine,
    scenario_set: ScenarioSet,
    overlay: Overlay,
    indices: Sequence[int],
    n_jobs: int = 1,
) -> dict[int, float]:
    """Re-simulate only the given scenario indices. Used by the optimiser."""
    picked = [scenario_set.scenarios[int(i)] for i in indices]
    if n_jobs > 1 and len(picked) >= 2 * n_jobs:
        results = engine.simulate_many(picked, overlay, n_jobs=n_jobs)
        return {int(i): r.weighted_loss_ph for i, r in zip(indices, results)}
    return {
        int(i): engine.simulate(
            scenario_set.scenarios[int(i)], overlay, _skip_amplification=True
        ).weighted_loss_ph
        for i in indices
    }
