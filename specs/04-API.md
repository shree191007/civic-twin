# 04 — API and copilot

**Builds:** `civictwin/api/*`, `tests/test_api.py`
**Gate:** `pytest tests/test_api.py` passes; every endpoint below responds
correctly against a `results/` directory produced by spec 03.

---

## 1. Principles

1. The API is a **read-through cache over `results/`** plus a live simulator for
   ad-hoc scenarios. It never runs the optimiser on request (too slow).
2. Every response includes `data_version` and `model_version`.
3. Coordinates are returned in **WGS84 lon/lat** for the map, with projected
   metres also available. Conversion happens once at startup.
4. No endpoint takes more than 2 s. Ad-hoc scenarios (one simulation) are ~25 ms;
   anything slower must come from precomputed files.
5. CORS: allow `http://localhost:5173` in dev.

## 2. Startup

`civictwin/api/main.py` reads environment variables:

| Var | Default | Meaning |
|---|---|---|
| `CIVICTWIN_TOWNSHIP` | `data/township.json` | |
| `CIVICTWIN_RESULTS` | `results/` | |
| `CIVICTWIN_LLM_KEY` | unset | If unset, `/copilot` returns `{"available": false}` gracefully |
| `CIVICTWIN_LLM_MODEL` | `claude-sonnet-4-6` | |
| `CIVICTWIN_ROLE` | `planner` | `planner` or `public` — see §7 |

On startup: load the township, build one `Engine`, load all JSON under
`results/` into memory (it is a few MB), and precompute the GeoJSON payloads.
Fail fast with a clear message if `results/` is missing required files.

## 3. Endpoints

### 3.1 `GET /health`
```json
{"status": "ok", "data_version": "3f2a1c9d", "model_version": "0.1.0+ab12cd34",
 "results_loaded": ["risk", "criticality", "spofs", "frontier", "baselines"],
 "copilot_available": true}
```

### 3.2 `GET /township`
Returns the full model as GeoJSON feature collections, one per portfolio, plus
zones and roads.

```json
{
  "name": "Synthetic Township",
  "bbox": [80.16, 13.01, 80.24, 13.09],
  "layers": {
    "energy":    {"type": "FeatureCollection", "features": [...]},
    "water":     {...}, "comms": {...}, "transport": {...}, "services": {...}
  },
  "zones": {"type": "FeatureCollection", "features": [...]},
  "roads": {"type": "FeatureCollection", "features": [...]},
  "links": [{"source": "S2", "target": "P2", "kind": "powers",
             "source_lonlat": [...], "target_lonlat": [...]}],
  "provenance_summary": {"observed": 31, "inferred": 22, "synthetic": 22}
}
```

Asset feature `properties`: `id, kind, portfolio, name, provenance, capacity,
backup_hours, fuel_hours, fragility_median_m, hand_m, served_population,
layer_altitude_m`.

**`layer_altitude_m` is required by the frontend's layer-cake view:**
transport 0, water 220, energy 440, comms 660, services 880.

### 3.3 `GET /assets/{id}`
```json
{"asset": {...properties...},
 "criticality": {...AssetCriticality...},
 "upstream": ["S2", "GS1"], "downstream": ["TX10", "T5"],
 "affected_zones": [{"id": "Z10", "population": 8400, "services": ["energy"]}],
 "explanations": ["S2 → F4 → TX10 → Z10 (8,400 people, energy)"],
 "spofs": [...any SPOF naming this asset...]}
```

### 3.4 `GET /assets/{id}/trace?direction=down&kinds=powers,supplies_water`
Returns the dependency subgraph for the Cytoscape panel:
```json
{"nodes": [{"id": "S2", "kind": "substation", "portfolio": "energy",
            "functionality": 1.0}],
 "edges": [{"source": "S2", "target": "P2", "kind": "powers"}],
 "affected_population": 31000}
```

### 3.5 `POST /scenarios`
Runs an ad-hoc scenario **live**.

Request:
```json
{"rain_mm": 210, "field_seed": 7, "onset_hour": 14,
 "forced_failures": ["B1"], "interventions": ["harden:S2"],
 "record": true}
```
Response: a `SimResult` with the timeline (schema in spec 02 §14.1) plus
`scenario_id` (a deterministic hash) and `people_summary`:
```json
{"people_summary": {"peak_no_power": 41200, "peak_no_water": 33900,
                    "peak_no_health": 31000, "person_hours_lost": 1284000}}
```

Caching: hash the request body; serve from an LRU cache of 256 entries.

### 3.6 `GET /scenarios/precomputed`
Lists the hero scenarios and available plan variants:
```json
[{"scenario_id": "storm_50y", "rain_mm": 231.4, "return_period_y": 48.2,
  "plans": ["baseline", "asset_by_asset", "optimised"]}]
```

### 3.7 `GET /scenarios/{scenario_id}?plan=optimised`
Returns the precomputed `SimResult` with timeline from
`results/hero/{scenario_id}__{plan}.json`.

### 3.8 `GET /scenarios/compare?a=storm_50y:baseline&b=storm_50y:optimised`
```json
{"a": {...summary...}, "b": {...summary...},
 "delta": {"person_hours_lost": -412000, "peak_no_water": -18100,
           "recovery_90_h": {"water": -9.0}},
 "assets_changed": ["S2", "T5", "T6"]}
```

### 3.9 `GET /risk`, `GET /criticality`, `GET /spofs`, `GET /critical-sets`,
`GET /frontier`, `GET /baselines`, `GET /voi`, `GET /sensitivity`,
`GET /ensemble`

Each serves the corresponding `results/*.json` verbatim, with `data_version`
and `model_version` injected. `GET /criticality?portfolio=energy&limit=20`
supports filtering and limiting.

### 3.10 `GET /plans?budget=30000000`
Returns the nearest precomputed plan at or below the requested budget, plus its
position on the frontier and per-intervention `selection_frequency`.
```json
{"budget_inr": 30000000, "plan": {...Plan...},
 "interventions": [
   {"id": "harden:S2", "label": "Flood wall at east substation",
    "cost_inr": 4500000, "selection_frequency": 0.95,
    "cvar_reduction_ph": 182000, "target_asset": "S2",
    "why": "S2 is upstream of water for 31,000 people"}],
 "frontier_position": {"index": 4, "of": 22}}
```

### 3.11 `GET /restoration/{scenario_id}`
Returns the three restoration policies and their metrics from
`results/hero/`.

### 3.12 `POST /decisions`
```json
{"plan_id": "budget_30000000", "rationale": "Council approved monsoon package",
 "scenarios_considered": ["storm_50y", "storm_100y"], "author": "planner"}
```
Appends to `results/decisions.jsonl` with a server timestamp and the current
`data_version`/`model_version`. Returns the stored record.
`GET /decisions` lists them.

### 3.13 `POST /copilot`
See §5.

## 4. `civictwin/api/schemas.py`

Pydantic v2 models mirroring every response above. Rules:
- `model_config = ConfigDict(extra="forbid")` on request models.
- All floats that represent person-hours end in `_ph`; money in `_inr`.
- Enums are reused from `civictwin.ontology`, not redefined.

## 5. Copilot

### 5.1 Tools (exact signatures exposed to the LLM)

```python
get_asset(asset_id: str) -> dict
trace_dependencies(asset_id: str, direction: Literal["up","down"]) -> dict
run_scenario(rain_mm: float, forced_failures: list[str] = [],
             interventions: list[str] = []) -> dict
compare_scenarios(a: str, b: str) -> dict
get_criticality(portfolio: str | None = None, limit: int = 10) -> dict
get_spofs(limit: int = 5) -> dict
get_plan(budget_inr: float) -> dict
list_assets(kind: str | None = None, portfolio: str | None = None) -> dict
```

Each tool is a thin wrapper over the corresponding endpoint logic.
**No tool may return more than 8 KB**; truncate lists and say so in the payload.

### 5.2 System prompt (use verbatim as the base)

```
You are a planning assistant for a municipal infrastructure resilience model of
{township_name}. You answer ONLY from tool results.

Rules:
- Never state a number that did not come from a tool result in this conversation.
- If a tool has not been called for a claim, call it or say you do not know.
- Confirm only what actually happened. If a tool returned no result, say so.
- Always name the dependency chain when explaining an impact.
- Report impact in people and hours first; technical risk metrics second.
- Distinguish data provenance when relevant: some layers are synthetic.
- You cannot make decisions or commit spending. You present options.
```

### 5.3 Behaviour

- Max 6 tool calls per user turn; then answer with what it has.
- Return the tool trace in the response so the UI can display it:
```json
{"answer": "...", "tool_calls": [{"tool": "run_scenario", "args": {...},
  "summary": "31,000 people lost hospital access for 14 h"}],
 "available": true}
```
- If `CIVICTWIN_LLM_KEY` is unset: `{"available": false, "answer": null,
  "reason": "Copilot not configured"}` with HTTP 200 (never an error).

### 5.4 Scene context

The request may include the current UI state, which is injected into the prompt:
```json
{"question": "what happens if this fails?",
 "scene": {"selected_asset": "S2", "scenario_id": "storm_50y", "t": 14.0,
           "active_layers": ["energy", "water"], "plan": "baseline"}}
```
"this" resolves to `scene.selected_asset`.

## 6. Errors

| Situation | Status | Body |
|---|---|---|
| Unknown asset id | 404 | `{"error": "unknown_asset", "id": "..."}` |
| Missing results file | 503 | `{"error": "results_missing", "file": "..."}` |
| Invalid scenario request | 422 | FastAPI validation body |
| Simulation failure | 500 | `{"error": "simulation_failed", "detail": "..."}` |

Never return a 500 with a stack trace in production mode.

## 7. Role gating

Dependency maps are sensitive. With `CIVICTWIN_ROLE=public`:
- `/criticality`, `/spofs`, `/critical-sets` return 403.
- `/township` omits `fragility_median_m` and link details.
- `/assets/{id}` omits `upstream`/`downstream`.

Default is `planner` (everything visible). Implement as a FastAPI dependency,
one decorator per route. Document this in the README as a design point.

## 8. Acceptance tests — `tests/test_api.py`

Use `fastapi.testclient.TestClient` with a fixture that builds a small township
(`generate(seed=7, scale=0.5)`) and runs `run_analysis.py --smoke` into a
temp dir.

1. `test_health_ok`
2. `test_township_layers_present`: all five portfolios, correct feature counts,
   `layer_altitude_m` present on every asset.
3. `test_township_geojson_valid`: every geometry parses; lon/lat within bbox.
4. `test_asset_detail_includes_criticality`
5. `test_asset_404`
6. `test_trace_up_includes_s2_for_h2`: trap #3 visible through the API.
7. `test_run_scenario_deterministic`: identical requests return identical
   `scenario_id` and losses.
8. `test_run_scenario_timeline_schema`: validates spec 02 §14.1.
9. `test_run_scenario_cached`: second identical call is ≥ 5× faster.
10. `test_precomputed_list_has_four_storms`
11. `test_compare_returns_negative_delta`: optimised beats baseline on
    `person_hours_lost`.
12. `test_plans_respects_budget`
13. `test_plans_includes_selection_frequency`
14. `test_decisions_roundtrip`: POST then GET returns the record.
15. `test_copilot_unconfigured_returns_available_false`
16. `test_copilot_tool_trace_present` (with a stubbed LLM client that always
    calls `get_spofs` then answers).
17. `test_role_public_blocks_criticality`: 403 with `CIVICTWIN_ROLE=public`.
18. `test_all_endpoints_under_2s`: time every GET endpoint.
19. `test_versions_in_every_response`: `data_version` and `model_version`
    present on all JSON responses.
