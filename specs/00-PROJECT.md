# 00 — Project brief, conventions, and build order

> **Read this file first. It governs every other spec.**
> Specs 01–07 are written to be executed in order. Each spec ends with acceptance
> tests that MUST pass before moving to the next spec.

---

## 1. What we are building

A decision-support system that models a small township as **one interconnected
system across four infrastructure portfolios** (energy, water, communications,
transport) plus the critical services they support, simulates how a flood
propagates failures across those portfolios over 72 hours, and recommends where
to invest a limited resilience budget.

**Name:** `civic-twin` (package name: `civictwin`)

**One-sentence pitch:** Portfolio-risk analytics for infrastructure — it finds
the assets and hidden single points of failure that asset-by-asset monitoring
misses, and optimises a budget against tail risk.

**Primary user:** a municipal disaster planner preparing for monsoon season who
must justify spending to a council.

## 2. Non-negotiable product rules

1. **Every number shown to a user comes from the simulator or analytics.** No
   hardcoded impact figures anywhere in the frontend.
2. **Every asset carries provenance** (`observed` / `inferred` / `synthetic`).
   The UI must be able to show it. Never hide synthetic layers.
3. **The LLM copilot never generates numbers.** It calls tools and explains
   their output. It confirms only what actually happened.
4. **Impact is reported in human units** (people, hours) first; risk metrics
   (CVaR) second.
5. **Determinism.** Same seed + same inputs → byte-identical results.
6. **No network calls at runtime.** All data fetching happens in the offline
   pipeline. The API and frontend read local files only.

## 3. Repository layout (authoritative — create exactly this)

```
civic-twin/
├── README.md
├── pyproject.toml
├── Makefile
├── .gitignore
├── specs/                       # these spec files, committed
├── civictwin/
│   ├── __init__.py
│   ├── config.py                # all tunable constants (spec 01 §9)
│   ├── ontology.py              # spec 01
│   ├── provenance.py            # spec 01
│   ├── io.py                    # load/save township + results
│   ├── synth/                   # synthetic township generator (spec 01 §8)
│   │   ├── __init__.py
│   │   └── township.py
│   ├── pipeline/                # real-data ETL (spec 07, optional)
│   │   ├── __init__.py
│   │   ├── fetch.py
│   │   ├── clean.py
│   │   ├── synthesize.py
│   │   └── assemble.py
│   ├── engine/                  # spec 02
│   │   ├── __init__.py
│   │   ├── contract.py          # SharedState, LayerModel protocol
│   │   ├── hazard.py
│   │   ├── damage.py
│   │   ├── coordinator.py
│   │   ├── response.py          # crews, fuel, operator rules
│   │   ├── demand.py
│   │   ├── loss.py
│   │   └── layers/
│   │       ├── __init__.py
│   │       ├── energy.py
│   │       ├── water.py
│   │       ├── comms.py
│   │       ├── transport.py
│   │       └── services.py
│   ├── analysis/                # spec 03
│   │   ├── __init__.py
│   │   ├── montecarlo.py
│   │   ├── metrics.py           # EAL, VaR, CVaR, drawdown
│   │   ├── criticality.py
│   │   ├── spof.py
│   │   ├── interventions.py
│   │   ├── optimize.py
│   │   ├── restoration.py
│   │   ├── ensemble.py
│   │   └── baselines.py
│   ├── surrogate/               # spec 06 (optional, gated)
│   │   ├── __init__.py
│   │   ├── dataset.py
│   │   ├── model.py
│   │   └── train.py
│   └── api/                     # spec 04
│       ├── __init__.py
│       ├── main.py
│       ├── schemas.py
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── township.py
│       │   ├── scenarios.py
│       │   ├── analytics.py
│       │   ├── plans.py
│       │   └── copilot.py
│       └── copilot/
│           ├── __init__.py
│           ├── tools.py
│           └── agent.py
├── web/                         # spec 05
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
├── scripts/
│   ├── build_township.py
│   ├── run_analysis.py
│   └── precompute_demo.py
├── data/
│   ├── township.json            # generated
│   └── SOURCES.md
├── results/                     # generated, gitignored
└── tests/
    ├── test_ontology.py
    ├── test_hazard.py
    ├── test_damage.py
    ├── test_layers.py
    ├── test_coordinator.py
    ├── test_metrics.py
    ├── test_criticality.py
    ├── test_spof.py
    ├── test_optimize.py
    ├── test_restoration.py
    ├── test_api.py
    └── fixtures/
```

## 4. Technology decisions (do not substitute)

| Concern | Choice | Notes |
|---|---|---|
| Language (backend) | Python 3.11+ | |
| Typed models | `dataclasses` in `civictwin/`, `pydantic` v2 only at the API boundary | Keeps the engine dependency-light and fast |
| Numerics | `numpy` | No pandas in the engine hot path |
| Graphs | `igraph` if available, else `networkx` | Wrap behind `civictwin/engine/layers/_graph.py` |
| Tabular results | `pyarrow` (Parquet) for scenario tables; JSON for everything the UI reads | |
| API | `fastapi` + `uvicorn` | |
| Tests | `pytest` | |
| Frontend | React + TypeScript + Vite + deck.gl + MapLibre | Spec 05 |
| Charts | `@observablehq/plot` | Spec 05 |
| Graph panel | `cytoscape` | Spec 05 |
| Optional detailed models | `pandapower`, `wntr`, `aequilibrae` | Optional imports, guarded (spec 02 §10) |
| Optional surrogate | `torch` + `torch_geometric` | Optional, gated behind spec 06 |

**Dependency rule:** the core engine (`civictwin/engine`) must import only
`numpy` and the standard library. Everything else is optional and guarded by
`try/except ImportError` with a clear fallback.

## 5. Coding conventions

- `from __future__ import annotations` at the top of every module.
- Full type hints on every public function. `mypy --strict` should pass on
  `civictwin/engine` and `civictwin/analysis` (not enforced on `api/`).
- Docstrings: one-line summary, then Args/Returns for anything non-obvious.
- No global mutable state. Randomness flows through an explicit
  `numpy.random.Generator` created from an integer seed.
- Units are explicit in names: `_m` metres, `_h` hours, `_mm` millimetres,
  `_ph` person-hours. Money in `_inr`.
- Asset IDs are short uppercase strings: `S1` substation, `F3` feeder,
  `X1` exchange, `T7` tower, `P2` pump, `K1` tank, `W1` treatment works,
  `B4` bridge, `H1` hospital, `D1` depot, `G1` fuel station, `Z12` zone,
  `e412` road edge (lowercase `e` + integer).
- Time is hours since event start (float), `t=0` is the start of the storm.
- Never use `print`. Use `logging` with module-level loggers.

## 6. Definition of key quantities (used across all specs)

| Term | Definition |
|---|---|
| **Functionality** `f ∈ [0,1]` | Fraction of normal service an asset can deliver right now |
| **Service level** `s ∈ [0,1]` | Fraction of a zone's demand for one service being met |
| **Service loss (person-hours)** | `Σ_zones Σ_steps population × (1 − s) × Δt` |
| **Weighted loss** | `Σ_services w_service × loss_service` (weights in `config.py`) |
| **Scenario year** | One Monte Carlo draw = one year = one storm event |
| **EAL** | Mean weighted loss across scenario years |
| **VaR95** | 95th percentile of weighted loss |
| **CVaR95** | Mean of the worst 5% of scenario years |
| **Ensemble member** | One plausible version of the township (epistemic uncertainty) |

## 7. Build order and gates

| Order | Spec | Gate before moving on |
|---|---|---|
| 1 | `01-ONTOLOGY.md` | `pytest tests/test_ontology.py` passes; `make township` writes `data/township.json` |
| 2 | `02-ENGINE.md` | `pytest tests/test_hazard.py test_damage.py test_layers.py test_coordinator.py` passes; `scripts/run_analysis.py --smoke` runs one scenario |
| 3 | `03-ANALYSIS.md` | `pytest tests/test_metrics.py test_criticality.py test_spof.py test_optimize.py test_restoration.py` passes; `make analyze` writes all files in `results/` |
| 4 | `04-API.md` | `pytest tests/test_api.py` passes; `make serve` responds on all documented endpoints |
| 5 | `05-FRONTEND.md` | `npm run build` succeeds; all five views render against a live API |
| 6 | `06-SURROGATE.md` | Optional. Only start if specs 1–5 are green |
| 7 | `07-REAL-DATA.md` | Optional. Only start if specs 1–5 are green |

**Rule for the implementing agent:** do not begin a spec until the previous
gate passes. If a gate cannot be met, stop and report which acceptance test
fails and why, rather than proceeding.

## 8. Makefile targets (create these exactly)

```make
install:    pip install -e ".[dev]"
township:   python scripts/build_township.py --seed 42 --out data/township.json
analyze:    python scripts/run_analysis.py --township data/township.json --out results/
demo:       python scripts/precompute_demo.py --township data/township.json --out results/
serve:      uvicorn civictwin.api.main:app --reload --port 8000
web:        cd web && npm run dev
test:       pytest -q
typecheck:  mypy civictwin/engine civictwin/analysis
all:        township analyze demo
```

## 9. What "done" means

- `make all && make test` runs clean from a fresh clone in under 10 minutes.
- The API serves every endpoint in spec 04 from files in `results/`.
- The frontend runs the full demo offline against those files.
- `results/validation.json` reports the acceptance metrics from spec 08.
