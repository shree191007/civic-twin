"""Acceptance tests for spec 04 — the API and the copilot."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from gotham.api.routes import copilot as copilot_route
from gotham.api.state import AppState, set_state
from gotham.io import save_township
from gotham.ontology import Portfolio, Service
from gotham.synth.township import generate

REPO_ROOT = Path(__file__).resolve().parent.parent
GET_ENDPOINTS = [
    "/health",
    "/township",
    "/risk",
    "/criticality?limit=5",
    "/spofs",
    "/critical-sets",
    "/frontier",
    "/baselines",
    "/voi",
    "/sensitivity",
    "/ensemble",
    "/plans?budget=30000000",
    "/scenarios/precomputed",
    "/assets/S2",
    "/assets/S2/trace?direction=down",
    "/decisions",
]


@pytest.fixture(scope="session")
def analysed(tmp_path_factory) -> tuple[Path, Path]:
    """A small township with a smoke analysis run over it."""
    work = tmp_path_factory.mktemp("api")
    township_path = work / "township.json"
    results = work / "results"
    save_township(generate(seed=7, scale=0.5), township_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_analysis.py"),
            "--township", str(township_path),
            "--out", str(results),
            "--smoke",
            "--skip-sensitivity",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=900,
    )
    if completed.returncode != 0:  # pragma: no cover - surfaces a real failure
        pytest.fail(f"run_analysis --smoke failed:\n{completed.stderr[-4000:]}")
    return township_path, results


@pytest.fixture(scope="session")
def client(analysed: tuple[Path, Path]) -> Iterator[TestClient]:
    township_path, results = analysed
    set_state(AppState(township_path=township_path, results_path=results))
    from gotham.api.main import app

    with TestClient(app) as c:
        yield c
    set_state(None)


@pytest.fixture()
def public_client(analysed: tuple[Path, Path]) -> Iterator[TestClient]:
    township_path, results = analysed
    set_state(
        AppState(township_path=township_path, results_path=results, role="public")
    )
    from gotham.api.main import app

    with TestClient(app) as c:
        yield c
    set_state(AppState(township_path=township_path, results_path=results))


def test_health_ok(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert {"risk", "spofs", "frontier", "baselines"} <= set(body["results_loaded"])
    assert body["copilot_available"] is False
    assert body["role"] == "planner"


def test_township_layers_present(client: TestClient) -> None:
    body = client.get("/township").json()
    assert set(body["layers"]) == {p.value for p in Portfolio}
    total = sum(len(layer["features"]) for layer in body["layers"].values())
    assert total == 75
    for layer in body["layers"].values():
        for feature in layer["features"]:
            assert "layer_altitude_m" in feature["properties"]
    assert len(body["zones"]["features"]) == 16
    assert body["roads"]["features"]
    assert sum(body["provenance_summary"].values()) == 75


def test_township_geojson_valid(client: TestClient) -> None:
    body = client.get("/township").json()
    min_lon, min_lat, max_lon, max_lat = body["bbox"]
    assert min_lon < max_lon and min_lat < max_lat

    def check(lon: float, lat: float) -> None:
        assert min_lon - 1e-6 <= lon <= max_lon + 1e-6
        assert min_lat - 1e-6 <= lat <= max_lat + 1e-6

    for layer in body["layers"].values():
        for feature in layer["features"]:
            assert feature["geometry"]["type"] == "Point"
            check(*feature["geometry"]["coordinates"])
    for feature in body["roads"]["features"]:
        assert feature["geometry"]["type"] == "LineString"
        assert len(feature["geometry"]["coordinates"]) == 2
        for lon, lat in feature["geometry"]["coordinates"]:
            check(lon, lat)


def test_asset_detail_includes_criticality(client: TestClient) -> None:
    body = client.get("/assets/S2").json()
    assert body["asset"]["id"] == "S2"
    assert "criticality" in body
    assert body["upstream"] and body["downstream"]
    assert isinstance(body["affected_zones"], list)
    assert body["explanations"]


def test_asset_404(client: TestClient) -> None:
    response = client.get("/assets/NOPE")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "unknown_asset"


def test_trace_up_includes_s2_for_h2(client: TestClient) -> None:
    """Trap 3 must be visible through the API, not just in the engine."""
    body = client.get("/assets/H2/trace?direction=up").json()
    ids = {n["id"] for n in body["nodes"]}
    assert {"S2", "P2", "K3"} <= ids
    edges = {(e["source"], e["target"]) for e in body["edges"]}
    assert ("P2", "K3") in edges and ("K3", "H2") in edges


def test_run_scenario_deterministic(client: TestClient) -> None:
    from gotham.api.state import get_state

    payload = {"rain_mm": 210.0, "field_seed": 7, "onset_hour": 14, "record": True}
    a = client.post("/scenarios", json=payload).json()
    get_state().clear_scenario_cache()  # measure the model, not the cache
    b = client.post("/scenarios", json=payload).json()
    assert b["cached"] is False
    assert a["scenario_id"] == b["scenario_id"]
    assert a["result"] == b["result"]


def test_run_scenario_timeline_schema(client: TestClient) -> None:
    body = client.post(
        "/scenarios", json={"rain_mm": 240.0, "field_seed": 3, "record": True}
    ).json()
    timeline = body["result"]["timeline"]
    assert timeline
    previous = -1.0
    for frame in timeline:
        assert set(frame) == {
            "t", "flood", "func", "damage", "state", "reason",
            "closed_roads", "crews", "zones", "totals",
        }
        assert frame["t"] > previous
        previous = frame["t"]
        for services in frame["zones"].values():
            assert set(services) == {s.value for s in Service}
    summary = body["people_summary"]
    assert set(summary) == {
        "peak_no_power", "peak_no_water", "peak_no_comms",
        "peak_no_health", "person_hours_lost",
    }
    # a 240 mm storm must leave somebody without something
    assert summary["person_hours_lost"] > 0
    assert max(summary[k] for k in summary if k.startswith("peak_")) > 0
    worst = max(
        max(frame["totals"].values()) for frame in timeline
    )
    assert max(summary[k] for k in summary if k.startswith("peak_")) == worst


def test_run_scenario_cached(client: TestClient) -> None:
    payload = {"rain_mm": 187.5, "field_seed": 21, "record": True}
    start = time.perf_counter()
    first = client.post("/scenarios", json=payload).json()
    cold = time.perf_counter() - start
    start = time.perf_counter()
    second = client.post("/scenarios", json=payload).json()
    warm = time.perf_counter() - start
    assert first["cached"] is False
    assert second["cached"] is True
    assert warm * 5 <= cold, f"cold {cold:.4f}s vs warm {warm:.4f}s"


def test_run_scenario_rejects_unknown_ids(client: TestClient) -> None:
    assert client.post(
        "/scenarios", json={"rain_mm": 100.0, "forced_failures": ["NOPE"]}
    ).status_code == 404
    assert client.post(
        "/scenarios", json={"rain_mm": 100.0, "interventions": ["harden:NOPE"]}
    ).status_code == 422
    assert client.post("/scenarios", json={"rain_mm": -5.0}).status_code == 422
    assert client.post(
        "/scenarios", json={"rain_mm": 100.0, "unexpected": 1}
    ).status_code == 422


def test_hero_and_adhoc_share_one_shape(client: TestClient) -> None:
    """A precomputed scenario and a live one must look the same to a client."""
    hero = client.get("/scenarios/storm_50y?plan=baseline").json()
    live = client.post("/scenarios", json={"rain_mm": 150.0, "record": True}).json()
    assert set(hero) >= {"scenario_id", "scenario", "result", "people_summary"}
    assert set(live) >= {"scenario_id", "scenario", "result", "people_summary"}
    assert hero["result"]["timeline"]
    assert set(hero["people_summary"]) == set(live["people_summary"])
    assert hero["plan_id"] == "baseline"
    assert hero["people_summary"]["peak_no_power"] == max(
        f["totals"]["people_no_power"] for f in hero["result"]["timeline"]
    )


def test_precomputed_list_has_four_storms(client: TestClient) -> None:
    body = client.get("/scenarios/precomputed").json()
    assert {row["scenario_id"] for row in body} == {
        "storm_10y", "storm_25y", "storm_50y", "storm_100y"
    }
    for row in body:
        assert set(row["plans"]) == {"baseline", "asset_by_asset", "optimised"}
        assert row["rain_mm"] > 0


def test_compare_returns_negative_delta(client: TestClient) -> None:
    body = client.get(
        "/scenarios/compare?a=storm_50y:baseline&b=storm_50y:optimised"
    ).json()
    assert body["delta"]["person_hours_lost"] <= 0.0
    assert isinstance(body["assets_changed"], list)


def test_plans_respects_budget(client: TestClient) -> None:
    body = client.get("/plans?budget=30000000").json()
    assert body["plan"]["cost_inr"] <= body["plan"]["budget_inr"]
    assert body["plan_budget_inr"] <= 30_000_000
    assert body["frontier_position"]["of"] >= body["frontier_position"]["index"]


def test_plans_includes_selection_frequency(client: TestClient) -> None:
    body = client.get("/plans?budget=30000000").json()
    assert body["interventions"]
    for item in body["interventions"]:
        assert {"id", "label", "cost_inr", "selection_frequency", "why"} <= set(item)
        assert item["why"]


def test_decisions_roundtrip(client: TestClient) -> None:
    payload = {
        "plan_id": "budget_30000000",
        "rationale": "Council approved monsoon package",
        "scenarios_considered": ["storm_50y", "storm_100y"],
        "author": "planner",
    }
    stored = client.post("/decisions", json=payload).json()
    assert stored["plan_id"] == payload["plan_id"]
    assert stored["recorded_at"]
    assert stored["data_version"]
    listed = client.get("/decisions").json()
    assert any(r["rationale"] == payload["rationale"] for r in listed["items"])


def test_copilot_unconfigured_returns_available_false(client: TestClient) -> None:
    response = client.post("/copilot", json={"question": "what is at risk?"})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["answer"] is None
    assert body["reason"] == "Copilot not configured"


class _Block:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _StubResponse:
    def __init__(self, content: list[_Block]) -> None:
        self.content = content


class _StubClient:
    """Calls get_spofs once, then answers from the tool result."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, model, system, messages, tools, max_tokens):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            assert "answer ONLY from tool results" in system
            return _StubResponse(
                [
                    _Block(
                        type="tool_use",
                        id="t1",
                        name="get_spofs",
                        input={"limit": 3},
                    )
                ]
            )
        payload = json.loads(messages[-1]["content"][0]["content"])
        top = payload["items"][0]
        return _StubResponse(
            [
                _Block(
                    type="text",
                    text=(
                        f"The worst shared dependency is {top['shared_asset']}, "
                        f"affecting {top['affected_population']:,} people."
                    ),
                )
            ]
        )


def test_copilot_tool_trace_present(client: TestClient) -> None:
    stub = _StubClient()
    copilot_route.CLIENT_OVERRIDE = stub
    try:
        body = client.post(
            "/copilot",
            json={
                "question": "what hidden failure should I worry about?",
                "scene": {"selected_asset": "S2", "active_layers": ["energy"]},
            },
        ).json()
    finally:
        copilot_route.CLIENT_OVERRIDE = None
    assert body["available"] is True
    assert body["answer"]
    assert len(body["tool_calls"]) == 1
    call = body["tool_calls"][0]
    assert call["tool"] == "get_spofs"
    assert call["args"] == {"limit": 3}
    assert "people" in call["summary"]


def test_role_public_blocks_criticality(public_client: TestClient) -> None:
    for path in ("/criticality", "/spofs", "/critical-sets"):
        assert public_client.get(path).status_code == 403, path
    assert public_client.get("/assets/S2/trace").status_code == 403
    body = public_client.get("/township").json()
    feature = body["layers"]["energy"]["features"][0]
    assert "fragility_median_m" not in feature["properties"]
    assert body["links"] == []
    asset = public_client.get("/assets/S2").json()
    assert "upstream" not in asset and "downstream" not in asset
    assert public_client.get("/risk").status_code == 200


def test_all_endpoints_under_2s(client: TestClient) -> None:
    for path in GET_ENDPOINTS:
        start = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - start
        assert response.status_code == 200, (path, response.status_code)
        assert elapsed < 2.0, f"{path} took {elapsed:.2f}s"


def test_versions_in_every_response(client: TestClient) -> None:
    for path in GET_ENDPOINTS:
        body = client.get(path).json()
        payload = body[0] if isinstance(body, list) else body
        assert payload["data_version"], path
        assert payload["model_version"], path
    scenario = client.post("/scenarios", json={"rain_mm": 90.0}).json()
    assert scenario["data_version"] and scenario["model_version"]


def test_frontier_steps_can_be_listed_as_a_package(client: TestClient) -> None:
    """Each frontier step carries enough to show it without the catalogue."""
    steps = client.get("/frontier").json()["steps"]
    assert steps
    for step in steps:
        assert {"kind", "cost_inr", "target_asset", "why"} <= set(step)
        assert step["why"], step["intervention_id"]
    costs = [s["cumulative_cost_inr"] for s in steps]
    assert costs == sorted(costs)


def test_network_wide_measures_are_not_described_as_feeder_ties(client: TestClient) -> None:
    body = client.get("/plans?budget=30000000").json()
    for item in body["interventions"]:
        if item["kind"] == "operational":
            assert "feeder" not in item["why"].lower(), item


def test_decision_status_and_plan_snapshot_round_trip(client: TestClient) -> None:
    payload = {
        "plan_id": "budget_30000000",
        "rationale": "Deferred until the east bridge survey is back",
        "author": "planner",
        "status": "deferred",
        "plan_summary": {"cost_inr": 28_000_000, "measures": 17},
    }
    stored = client.post("/decisions", json=payload).json()
    assert stored["status"] == "deferred"
    assert stored["plan_summary"]["measures"] == 17
    assert client.post(
        "/decisions", json={**payload, "status": "maybe"}
    ).status_code == 422
