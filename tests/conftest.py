"""Shared fixtures for the analysis test suite."""
from __future__ import annotations

import pytest

from gotham.analysis.montecarlo import LossTable, ScenarioSet, make_scenario_set, run_set
from gotham.config import DEFAULT
from gotham.engine.coordinator import Engine
from gotham.ontology import Township
from gotham.synth.township import generate

#: Small enough to keep the suite quick, large enough for a meaningful tail.
TEST_SCENARIOS = 60


@pytest.fixture(scope="session")
def town() -> Township:
    return generate(42)


@pytest.fixture(scope="session")
def engine(town: Township) -> Engine:
    return Engine(town, DEFAULT)


@pytest.fixture(scope="session")
def train_set(town: Township) -> ScenarioSet:
    return make_scenario_set(town, DEFAULT, "train", TEST_SCENARIOS, DEFAULT.risk.seed_train)


@pytest.fixture(scope="session")
def train_table(engine: Engine, train_set: ScenarioSet) -> LossTable:
    return run_set(engine, train_set)


@pytest.fixture(scope="session")
def catalogue(town: Township):
    from gotham.analysis.interventions import generate_catalogue

    return generate_catalogue(town, DEFAULT)


@pytest.fixture(scope="session")
def ranked(engine: Engine, train_set: ScenarioSet, train_table: LossTable):
    from gotham.analysis.criticality import rank_criticality

    return rank_criticality(engine, train_set, train_table, DEFAULT)


@pytest.fixture(scope="session")
def test_set(town: Township) -> ScenarioSet:
    return make_scenario_set(town, DEFAULT, "test", TEST_SCENARIOS, DEFAULT.risk.seed_test)
