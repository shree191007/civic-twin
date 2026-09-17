"""Acceptance tests for spec 03 section 3 — asset criticality."""
from __future__ import annotations

import numpy as np
import pytest

from civictwin.analysis.criticality import (
    AssetCriticality,
    asset_criticality,
    critical_sets,
    explain_chains,
    rank_criticality,
)
from civictwin.analysis.montecarlo import LossTable, ScenarioSet
from civictwin.config import DEFAULT
from civictwin.engine.coordinator import Engine
from civictwin.ontology import LinkKind, Township

CHAIN_KINDS = {LinkKind.POWERS, LinkKind.SUPPLIES_WATER, LinkKind.BACKHAULS}


def test_s2_is_top_systemic(ranked: list[AssetCriticality]) -> None:
    top3 = [c.asset_id for c in ranked[:3]]
    assert "S2" in top3, f"S2 missing from the top three: {top3}"


def test_systemic_ratio_flags_network_assets(ranked: list[AssetCriticality]) -> None:
    by_id = {c.asset_id: c for c in ranked}
    s2 = by_id["S2"]
    peripheral = by_id["T1"]  # a near-bank tower on high ground
    assert s2.systemic_ratio > peripheral.systemic_ratio
    assert s2.tail_criticality_ph > 0.0


def test_zero_prob_assets_skipped(
    engine: Engine, train_set: ScenarioSet, train_table: LossTable, town: Township
) -> None:
    """An asset that never fails costs nothing to analyse."""
    never = [
        aid
        for aid in sorted(town.assets)
        if not any(aid in dmg for dmg in train_table.damaged)
    ]
    assert never, "expected some assets that never fail in the sample"
    calls = {"n": 0}
    original = Engine.simulate

    def counting(self, *a, **kw):
        calls["n"] += 1
        return original(self, *a, **kw)

    Engine.simulate = counting  # type: ignore[method-assign]
    try:
        result = asset_criticality(
            engine, train_set, train_table, DEFAULT, never[0], np.random.default_rng(0)
        )
    finally:
        Engine.simulate = original  # type: ignore[method-assign]

    assert result.annual_failure_prob == 0.0
    assert result.tail_criticality_ph == 0.0
    assert result.systemic_ratio == 0.0
    assert result.scenarios_resimulated == 0
    # only the cached standalone loss may have been simulated, never the set
    assert calls["n"] <= 1


def test_explanations_reference_real_chain(
    ranked: list[AssetCriticality], town: Township
) -> None:
    checked = 0
    for c in ranked[:15]:
        for line in c.explanation:
            chain = line.split(" (")[0].split(" → ")
            assert chain[0] == c.asset_id
            for source, target in zip(chain, chain[1:]):
                if target.startswith("Z") and "," in line.split(" (")[0]:
                    continue
                if target not in town.assets:
                    continue
                assert target in town.dependents(source, CHAIN_KINDS), (
                    f"{source} → {target} is not a real link"
                )
                checked += 1
    assert checked > 0, "no explanation chains were verified"


def test_explanation_endpoints_are_zone_providers(town: Township) -> None:
    lines = explain_chains(town, "P2")
    assert lines
    assert any("K3" in line for line in lines)
    assert any("water" in line for line in lines)


def test_critical_sets_rank_by_synergy(
    engine: Engine, ranked: list[AssetCriticality]
) -> None:
    rows = critical_sets(engine, ranked, candidates=6, keep=10)
    assert rows
    synergies = [float(r["synergy_ph"]) for r in rows]
    assert synergies == sorted(synergies, reverse=True)
    for row in rows:
        assert len(row["assets"]) == 2
        assert float(row["joint_ph"]) >= 0.0
