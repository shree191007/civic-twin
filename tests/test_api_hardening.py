"""Tests for the API hardening: determinism, role scoping, and redaction."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gotham.api.state import AppState, set_state
from gotham.io import save_township
from gotham.synth.township import generate


@pytest.fixture(scope="module")
def township_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("hardening") / "township.json"
    save_township(generate(seed=7, scale=0.5), path)
    return path


@pytest.fixture()
def planner(township_path: Path, tmp_path) -> Iterator[TestClient]:
    set_state(AppState(township_path=township_path, results_path=tmp_path))
    from gotham.api.main import app

    with TestClient(app) as c:
        yield c
    set_state(None)


@pytest.fixture()
def public(township_path: Path, tmp_path) -> Iterator[TestClient]:
    set_state(
        AppState(township_path=township_path, results_path=tmp_path, role="public")
    )
    from gotham.api.main import app

    with TestClient(app) as c:
        yield c
    set_state(None)


# ------------------------------------------------------- F17 common randoms


def test_f17_interventions_do_not_reroll_the_storm(planner: TestClient) -> None:
    """The dice belong to the storm, not to the plan being tested against it."""
    base = {"rain_mm": 220.0, "field_seed": 5, "onset_hour": 9, "record": False}
    plain = planner.post("/scenarios", json=base).json()
    hardened = planner.post(
        "/scenarios", json={**base, "interventions": ["harden:S2"]}
    ).json()

    assert plain["scenario"]["seed"] == hardened["scenario"]["seed"], (
        "applying an intervention must not change which storm this is"
    )
    # The intervention protects S2, so the comparison is a true counterfactual:
    # every other asset saw exactly the same water and rolled the same dice.
    assert hardened["result"]["weighted_loss_ph"] <= plain["result"]["weighted_loss_ph"]


def test_f26_seeds_do_not_depend_on_process_hash_randomisation(
    planner: TestClient,
) -> None:
    from gotham.api.routes.scenarios import _stable_seed

    assert _stable_seed("abc") == _stable_seed("abc")
    assert _stable_seed("abc") != _stable_seed("abd")
    # A hash-randomised seed would differ between processes; a sha256 one is
    # a fixed function of its input, so this value is stable forever.
    assert _stable_seed("abc") == int.from_bytes(
        __import__("hashlib").sha256(b"abc").digest()[:6], "big"
    )


def test_forced_failures_still_change_the_scenario_id(planner: TestClient) -> None:
    """The cache key must stay sensitive to everything that changes the answer."""
    base = {"rain_mm": 150.0, "field_seed": 2, "record": False}
    a = planner.post("/scenarios", json=base).json()
    b = planner.post("/scenarios", json={**base, "forced_failures": ["B1"]}).json()
    assert a["scenario_id"] != b["scenario_id"]


# ------------------------------------------------------ F27 public redaction


def test_public_township_hides_the_dependency_map(public: TestClient) -> None:
    body = public.get("/township").json()
    zone = body["zones"]["features"][0]["properties"]
    for leaked in ("substation", "feeder", "tank", "towers"):
        assert leaked not in zone, f"{leaked} reconstructs the dependency graph"
    assert body["links"] == []
    feature = body["layers"]["energy"]["features"][0]["properties"]
    assert "fragility_median_m" not in feature


def test_public_asset_detail_hides_structure(public: TestClient) -> None:
    body = public.get("/assets/S2").json()
    for leaked in ("upstream", "downstream", "criticality", "spofs", "affected_zones"):
        assert leaked not in body, f"{leaked} is dependency structure"
    assert body["asset"]["id"] == "S2"


def test_public_role_is_refused_the_analytics(public: TestClient) -> None:
    for path in ("/criticality", "/spofs", "/critical-sets", "/assets/S2/trace"):
        assert public.get(path).status_code == 403, path


def test_planner_still_sees_everything(planner: TestClient) -> None:
    body = planner.get("/township").json()
    assert body["zones"]["features"][0]["properties"]["substation"]
    assert body["links"]
    detail = planner.get("/assets/S2").json()
    assert detail["upstream"] and detail["affected_zones"]


# --------------------------------------------------------- F22 copilot scope


def test_f22_copilot_refuses_structural_tools_to_the_public_role(
    township_path: Path, tmp_path
) -> None:
    from gotham.api.copilot.tools import RESTRICTED_TOOLS, ToolBox

    public_state = AppState(
        township_path=township_path, results_path=tmp_path, role="public"
    )
    planner_state = AppState(township_path=township_path, results_path=tmp_path)

    public_box = ToolBox(public_state)
    assert public_box.restricted == RESTRICTED_TOOLS
    for name in RESTRICTED_TOOLS:
        assert name not in public_box.registry()
        assert public_box.call(name, {})["error"] == "forbidden_for_role"

    planner_box = ToolBox(planner_state)
    assert planner_box.restricted == frozenset()
    for name in RESTRICTED_TOOLS:
        assert name in planner_box.registry()


# ------------------------------------------------------------ F23 truncation


def test_f23_truncation_always_terminates() -> None:
    from gotham.api.copilot.tools import MAX_TOOL_BYTES, _truncate

    # One enormous string in a list: halving can never make it fit.
    payload = {"items": ["x" * (MAX_TOOL_BYTES * 2)], "summary": "big"}
    result = _truncate(payload)
    assert result["truncated"] is True
    assert len(str(result)) < MAX_TOOL_BYTES * 2

    # A long list of small things shrinks to fit rather than being thrown away.
    payload = {"items": [{"id": i} for i in range(5000)], "count": 5000}
    result = _truncate(payload)
    assert result.get("truncated") is True
    assert len(result.get("items", [])) < 5000
