# gotham

> **See the cascade before the disaster.**

An AI-powered critical-infrastructure digital twin. It takes a hazard forecast
from an external model, simulates how that hazard cascades across four
interconnected infrastructure portfolios — energy, water, communications,
transport — quantifies the human and service impact, and recommends where to
spend a limited resilience budget before the disaster arrives.

```
Forecast ─► Exposure ─► Damage ─► Cascade ─► Impact ─► Action
```

Technically it is **a dependency-aware stochastic infrastructure resilience
simulator**. A continuously synchronised real-time twin is the production
direction, not what this is today; the README says so throughout because the
difference matters to anyone deciding how much weight to put on the output.

**It does not forecast weather.** Meteorology, flood extent, cyclone tracks and
satellite observation are solved problems with better solvers than this one.
gotham starts one step later, at the question nobody else is answering:

> *Given that this hazard is predicted or observed, what happens to the
> infrastructure system?*

The claim it exists to test: **asset-by-asset monitoring misses the failures that
matter.** A tower with a redundant twin is not redundant if both sit on the same
substation. A backup fibre is not a backup if it crosses the same bridge as the
primary. A hospital on high ground still loses water if a pump two portfolios
away is under a metre of floodwater.

*The asset was not necessarily the problem. The hidden dependency was.*

## Architecture

```
      EXTERNAL HAZARD MODELS                 scripts/build_township.py
      (rainfall ensembles, flood,                      │
       cyclone, satellite, wildfire)          data/township.json
                  │                                    │
                  ▼                                    │
   ┌──────────────────────────────┐                    │
   │  gotham/hazard            │                    │
   │  ingestion ─► adapters ─►    │                    │
   │  normalisation ─► schemas    │                    │
   └──────────────┬───────────────┘                    │
                  │ HazardForecast (a distribution,    │
                  │ never a single number)             │
   ┌──────────────▼────────────────────────────────┐   │
   │  gotham/engine        INFRASTRUCTURE ONTOLOGY ◄─┘
   │                                               │
   │  hazard ─► damage ─► transport ─► response ─┐ │
   │                                             │ │
   │      energy ─► comms ─► water ─► services   │ │
   │                    │                        │ │
   │                 demand ─► loss ◄────────────┘ │
   └──────────────────────┬────────────────────────┘
                          │  SimResult + operating states
   ┌──────────────────────▼────────────────────────┐
   │  gotham/analysis                           │
   │  montecarlo ─► metrics (EAL, VaR, CVaR)       │
   │  criticality · spof · redundancy (ERS)        │
   │  forecast→impact · uncertainty · ledger       │
   │  optimize (multi-objective) · ensemble · voi  │
   │  hindcast (historical replay)                 │
   └──────────────────────┬────────────────────────┘
                          │  results/*.json
              gotham/api (FastAPI, role-gated)
                          │
                  web/ (React + deck.gl)
```

Each time step runs the layers in a fixed order (`specs/02-ENGINE.md` §2). Where
a layer needs a value produced later in that order — stormwater drainage needs
the energy layer's output — it reads the previous step's value. That lag is
documented, not accidental, and it is what lets the dependency graph contain
cycles (power feeds a pump, water cools a generator) without the step failing to
resolve.

### Fidelity

Three tiers, of which this builds the first two:

| Tier | Question | Built |
|---|---|---|
| 1. System topology | Who depends on whom? | ✅ |
| 2. Simplified engineering | What happens when a dependency degrades or fails? | ✅ |
| 3. High-fidelity physical | Detailed hydraulics, power flow, traffic assignment | Adapters only, for cross-checking |

Tier 3 solvers (pandapower, WNTR, AequilibraE) are used to *verify* the fast
models, not to run them. `results/validation.json` records the agreement, or
records that the tool was not installed.

## Quickstart

```bash
make install          # pip install -e ".[dev]"
make all              # township -> analysis -> demo precompute
make serve            # API on :8000
make web              # frontend on :5173
```

`make test` runs the suite. `make typecheck` runs mypy over the engine and
analysis packages.

## What is in the box

| Path | What it is |
|---|---|
| `gotham/ontology.py` | The typed model: assets, links, zones, roads, crews |
| `gotham/hazard/` | Hazard ingestion: adapters, normalisation, one internal schema |
| `gotham/engine/` | The simulator. Imports only numpy and the standard library |
| `gotham/analysis/` | Monte Carlo, risk metrics, criticality, SPOF, ERS, the optimiser, hindcasting |
| `gotham/api/` | FastAPI read-through cache over `results/`, plus a live simulator |
| `web/` | React + deck.gl operations console, five views |
| `specs/` | The build specifications this repository implements, and the patch that amended them |

### What it does that a risk map does not

| Capability | Where |
|---|---|
| **Hazard ingestion** — any provider's forecast, normalised into one schema, uncertainty intact | `gotham/hazard/` |
| **Forecast to impact** — a hazard distribution in, an impact distribution out | `analysis/forecast.py` |
| **Operating states** — operational, degraded, on backup, critical, failed; distinct from damage | `engine/states.py` |
| **Time-based dependencies** — thresholds, delays and per-link reserves, not just edges | `engine/dependency.py` |
| **Effective Redundancy Score** — how much of the redundancy on paper is real | `analysis/redundancy.py` |
| **Systemic Dependency Risk** — probability × impact × concentration × recovery difficulty | `analysis/criticality.py` |
| **Counterfactual SPOFs** — measured by simulation, not asserted from topology | `analysis/spof.py` |
| **Multi-objective optimisation** — protect life, minimise economic loss, restore fast, balanced | `analysis/objectives.py` |
| **Assumption ledger** — every result traceable to what it rests on | `analysis/ledger.py` |
| **Historical replay** — IoU, precision and recall against a documented event | `analysis/hindcast.py` |

## The four structural traps

The synthetic township deliberately contains six structural weaknesses; four of
them drive the demo, and each has a named test that must pass:

1. **Fake redundancy.** Towers T5 and T6 both cover zones Z9–Z12, and both are
   powered by S2, the low-lying east substation.
2. **Co-location.** The primary fibre and its backup are both carried by bridge B1.
3. **Cross-portfolio.** Hospital H2 sits on high ground and rarely floods, but
   its water comes from tank K3, refilled only by pump P2, powered by S2.
4. **Recovery.** Every repair depot is on the near bank. Reaching anything across
   the river needs one of the two bridges.

`pytest tests/` covers all four.

## Design points worth knowing

**Role gating.** A dependency map is a list of weak points. With
`GOTHAM_ROLE=public`, `/criticality`, `/spofs` and `/critical-sets` return
403, `/township` omits fragility parameters and link detail, and `/assets/{id}`
omits the dependency lists. The default role is `planner`.

**The copilot never generates numbers.** It calls tools and explains their
output; the response carries the tool trace so a reader can check it. With
`GOTHAM_LLM_KEY` unset it returns `{"available": false}` and the interface
says so plainly.

**Provenance is never hidden.** Every asset is marked `observed`, `inferred` or
`synthetic`, and the map has a mode (`R`) that colours by it. Inputs to a
*result* carry a wider vocabulary — observed, verified, estimated, assumed,
simulated, or derived from an external model — and a claim is reported as being
only as firm as the weakest thing under it.

**No false precision.** Nothing is reported as `CVaR = ₹42,731,829.42`. Every
headline figure is a range, a confidence level, and the named reasons it is
uncertain, because that is what the inputs support.

**Determinism.** The same seed and inputs give byte-identical results, whether a
batch runs on one process or ten.

## Known limitations

- The water mains and distribution feeders of the synthetic township are
  **synthetic**; the ensemble exists precisely because of that, and the
  value-of-information report says which of those assumptions would change the
  answer most.
- Batteries do not recharge inside the 72-hour horizon.
- Costs are illustrative unit rates, not tendered prices.
- Operator behaviour is rule-based, not behavioural.
- Hospital capability is modelled as availability, not beds or staffing.
- See `results/validation.json` for the measured checks, including any that fail.

## Deviations from the specifications

These are recorded rather than papered over; each is explained where it lives.

| Spec | What differs | Why |
|---|---|---|
| 02 §15 | One 72-hour simulation takes ~40 ms, not ≤25 ms | ~20 ms of it is the per-zone mobility Dijkstra in pure Python. The throughput target (200 scenarios < 8 s) is met via `simulate_many(n_jobs=…)`, which returns identical results. |
| 02 §16 test 30 | `amplification_ratio > 1` does not hold; the test is `xfail` with its reasoning | The ratio divides joint loss by the **sum** of standalone losses, and each standalone loss already contains that asset's full cascade. Assets sharing one flood basin have overlapping cascades, so the sum over-counts and the ratio is structurally sub-additive. The metric is implemented exactly as written. |
| 03 §6 | The optimiser screens on a configurable subset of the training set (`--opt-scenarios`, default 250) | One greedy step over 1,000 scenarios is ~10,650 simulations; the full default would take hours and break the "under ten minutes" rule in spec 00 §9. Risk, criticality and SPOF detection still use the full set, and every accepted step is verified by re-simulating that subset in full. |
| 03 §3.2 | The `systemic` flag also requires the ratio's denominator not to have been clamped | With `max(expected_direct, 1.0)`, an asset with almost no standalone loss — a stormwater pump does nothing when it is not raining — shows an unbounded ratio purely from the clamp. |
| 04 §3.7 | `GET /scenarios/{id}` returns the same envelope as `POST /scenarios` | The specification gives the two endpoints different shapes for the same object; one shape means clients need one code path. |
| patch §12 | Universal dependencies are excluded from the Effective Redundancy Score and reported separately as common roots | Every group in a single-source town shares the grid supply, so naming it as the reason nothing is redundant is true and useless. The score names the nearest shared cause per dependency kind instead — "they share S2 for power and FIBRE_4 for backhaul". |
| patch §13 | The `systemic` flag additionally requires the ratio's denominator not to have been clamped | With `max(expected_direct, 1.0)`, an asset with almost no standalone loss shows an unbounded ratio purely from the clamp. A stormwater pump does nothing when it is not raining; that makes it invisible to the standalone measure, not systemically critical. |
| patch §14 | The design-storm counterfactual measures *protecting* the asset, not failing it | Under the design storm the candidate is already failing, so forcing it to fail again measures nothing. The question with an answer is how much damage goes away if it holds. |
| patch §12 | Common-cause probability is the conditional rate P(all fail │ any fails) | The unconditional joint rate is dominated by how often it storms at all, and no group cleared a threshold calibrated for design-storm conditions. The conditional form answers the question a common-cause finding asks, and discriminates: co-located towers score 0.55, towers on opposite banks 0.00. |
| patch §19 (optimiser) | Candidates that gain nothing survive three barren rounds before being set aside | Retaining every zero-gain candidate forever, as the remediation plan proposed, makes a greedy step unaffordable at full scenario counts — which is the constraint that forced pruning in the first place. Three rounds is enough for a tie to become valuable after the feeder it backs up has been hardened. |
| 06 | Not built | The spec patch (§21) puts a GNN surrogate on the "do not build" list; it is out of scope by decision, not by omission. |
| 07 | Not built | The real-data pipeline. Its absence is why `validation.json` reports the backtest as `skipped`: the replay harness exists and is tested, but there is no observed event to replay. |
| patch §18, §19 | Harness built, no real event | Historical validation needs a documented flood. The harness scores hazard footprint, exposure, cascade and decision counterfactual; with no event file it says `skipped` rather than returning a score. |

## Credits

Method inspiration from Hazus and IN-CORE; cross-model verification against
pandapower, WNTR and AequilibraE. Basemap tiles by CARTO, map data by
OpenStreetMap contributors. See `data/SOURCES.md`.

## Licence

Code under the MIT licence. Any published data derived from OpenStreetMap
carries ODbL obligations; see `data/SOURCES.md`.
