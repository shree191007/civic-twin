# 08 — Validation, demo, and delivery

**Builds:** `scripts/precompute_demo.py`, `results/validation.json`,
the brief template, the README, and the demo assets.

**Gate:** the demo runs offline three times in a row in under 5 minutes.

---

## 1. Validation levels

| Level | Question | Where implemented |
|---|---|---|
| Component | Does each layer behave correctly in isolation? | `tests/test_layers.py` |
| Cross-model | Do the fast models agree with the established sector tools? | §3 |
| System | Does the whole model reproduce a real event? | spec 07 §6 |
| Statistical | Are the numbers stable? | §4 |
| Decision | Does the systems view beat simpler planning? | spec 03 §9 |

## 2. `results/validation.json` — exact schema

```json
{
  "generated_at": "...",
  "data_version": "...", "model_version": "...",
  "cross_model": {
    "energy":    {"tool": "pandapower", "scenarios": 12,
                  "agreement_deenergised_zones": 0.94, "target": 0.90,
                  "pass": true, "notes": "..."},
    "water":     {"tool": "wntr", "scenarios": 12,
                  "mean_abs_timing_error_h": 2.1, "target": 3.0, "pass": true},
    "transport": {"tool": "aequilibrae", "scenarios": 12,
                  "mean_abs_travel_time_error_pct": 14.3, "target": 20.0,
                  "pass": true}
  },
  "backtest": {
    "event": "flood-2015-12",
    "roads": {"hit_rate": 0.68, "false_alarm_ratio": 0.31,
              "n_observed_flooded": 412, "n_predicted": 455},
    "outages": [{"service": "energy", "area": "...",
                 "observed_start_h": 6, "predicted_start_h": 8,
                 "observed_end_h": 60, "predicted_end_h": 52}],
    "ensemble_members_retained": 11, "of": 20
  },
  "stability": {
    "cvar_train_ph": 1284000, "cvar_test_ph": 1251000,
    "relative_difference": 0.026, "target": 0.05, "pass": true,
    "plan_overlap_on_doubling_scenarios": 0.92
  },
  "decision": {
    "budget_inr": 30000000,
    "cvar_asset_by_asset_ph": 918000,
    "cvar_optimised_ph": 604000,
    "improvement_pct": 34.2,
    "evaluated_on": "held-out test set, 500 scenarios, 20 ensemble members"
  },
  "limitations": [
    "Water mains and distribution feeders are synthetic (see provenance report)",
    "Batteries do not recharge within the 72-hour horizon",
    "Costs are illustrative unless marked schedule_of_rates",
    "Operator behaviour is rule-based, not behavioural"
  ]
}
```

**If any `pass` is false, it stays false in the artefact.** Report honestly.

## 3. Cross-model verification procedure

```
usage: python scripts/verify_models.py --township data/township.json
                                       --scenarios 12 --out results/validation.json
```

For 12 hero-like scenarios spanning the rainfall range:

**Energy.** Build a pandapower net from the same township. For each scenario's
damage state, set damaged elements out of service, run `pp.runpp`, and mark a
feeder as failed if isolated or if any connected line loading exceeds 100%.
Compare the set of zones without power against the fast model.
Metric: Jaccard similarity of de-energised zone sets. Target ≥ 0.90.

**Water.** Build a WNTR model with pressure-dependent demand. Apply the same
pump outages. Compare the hour at which each zone first drops below 50% of
demand satisfied. Metric: mean absolute timing error. Target ≤ 3 h.

**Transport.** Build an AequilibraE network with an OD matrix from zone
populations. Run user-equilibrium assignment with BPR (`alpha=0.15, beta=4`).
Compare zone→nearest-hospital travel times against the fast model's free-flow
detour times. Metric: mean absolute percentage error. Target ≤ 20%.

If any optional package is unavailable, write `{"skipped": true, "reason": ...}`
for that entry rather than failing the run.

## 4. Statistical stability

```
CVaR on the train set vs the held-out test set: relative difference ≤ 5%.
Plan overlap when doubling the scenario count: ≥ 0.85 Jaccard on the
  selected intervention set.
Bootstrap 95% CI on EAL and CVaR reported in results/risk.json.
```

## 5. The one-page brief

Rendered by `/log` → "Generate brief" as a print-styled page. Content, in order:

1. **Header.** Township name, hazard modelled, data versions, generated date.
2. **Provenance.** One line: "34 assets observed, 41 inferred, 58 synthetic",
   with a small stacked bar by portfolio.
3. **Headline risk.** Expected annual loss and the 1-in-20-year loss, in
   person-hours and in "people affected for more than 12 hours". By portfolio.
   Worst-off zone named.
4. **Hidden single points of failure.** Top 3 cards, each one sentence plus
   affected population.
5. **Recommended plan** at the selected budget: a table of interventions with
   cost, CVaR reduction, and confidence percentage. Total cost and total
   reduction in the footer row.
6. **Comparison.** One sentence: optimised versus asset-by-asset planning at the
   same budget, with the percentage.
7. **Restoration priorities** for the 1-in-50 storm: the first five repairs in
   order, with the reason for each.
8. **Data worth collecting next.** The top 3 from the value-of-information list.
9. **Validation and limitations.** Backtest hit rate, stability figure, and the
   limitations list verbatim from `validation.json`.

Print rules: A4, 10 pt body, no background fills heavier than 5% grey, all
charts render in greyscale legibly.

## 6. `scripts/precompute_demo.py`

Generates everything the demo needs so it runs with the network off:

```
usage: precompute_demo.py --township PATH --out results/ [--fixtures web/public/fixtures]
```

Produces:
- The four hero storms × three plans, with timelines (12 files).
- Ad-hoc rainfall slider stops every 20 mm from 60 to 320 mm at the baseline
  plan (14 files), so the slider never waits on computation.
- The SPOF "Show me" targets with their camera positions.
- A copy of everything into `web/public/fixtures/` for the API-down fallback.

Print a summary table of file sizes; total must stay under 40 MB.

## 7. Five-minute demo script

| Time | Beat | Screen | Proves |
|---|---|---|---|
| 0:00–0:30 | A documented local incident where one failure cascaded (cite the source on the slide) | Slide | The problem is real |
| 0:30–1:10 | `/explore`: the township as four layers; click `H2` and trace up to `S2` — a hospital on high ground that fails because of a substation two portfolios away | Layer cake + graph | Systems view |
| 1:10–2:10 | `/scenario`: rainfall to the 1-in-50 storm; scrub 0→72 h; call out battery exhaustion at H+4, pump failure, generator running dry because the fuel truck can't cross | Map + portfolio timeline | Cross-sector propagation and recovery |
| 2:10–2:40 | `/risk`: the SPOF card — T5 and T6 look redundant, both sit on S2; the backup fibre crosses the same bridge as the primary | SPOF cards | The insight asset-by-asset monitoring misses |
| 2:40–3:40 | `/plan`: drag the budget along the frontier; then split screen, same storm, asset-by-asset versus optimised, counters ticking | Frontier + split | Decision value and proof |
| 3:40–4:20 | Backtest overlay, confidence badges, provenance mode (`R`) showing what is real and what is synthetic | Map + validation | Rigour and honesty |
| 4:20–5:00 | Hand over the printed brief; close on one number | Brief | Tangible output |

**Rules for the presenter:** speak in people and hours; never say CVaR without
immediately translating it; never apologise for synthetic layers, present them
as a modelled assumption with a confidence measure.

## 8. Judge interaction (only after the script)

- Hand over the mouse: a judge picks any asset to fail; `/scenario` runs it live.
- Copilot: ask "what happens if the east bridge and S2 both fail?" with the tool
  trace expanded so the judge sees it calling the simulator.

## 9. Anticipated questions and answers

| Question | Answer |
|---|---|
| Your water and fibre layers are synthetic — why trust it? | We optimise across 20 plausible versions of the township and report how often each recommendation is selected. We also publish which data would change the answer most. |
| How is this different from a GIS risk map? | A risk map ranks assets independently. We find fake redundancy, cross-sector cascades, and failure combinations — none of which are visible asset by asset. That's the comparison in the split screen. |
| How did you validate it? | Each fast layer is checked against pandapower, WNTR, and AequilibraE; the whole model is backtested against a real flood; and every recommendation is tested on held-out scenarios. |
| Why CVaR and not expected loss? | Planners must survive the bad years. Expected loss hides exactly the events that matter. |
| Does this scale beyond one township? | The pipeline builds a township from any bounding box with open data. |
| Could this be misused? | Dependency maps are a list of weak points. The API has a role gate: the public role never sees criticality rankings. |

## 10. Demo engineering checklist

- [ ] Everything precomputed; only the judge-picked failure runs live.
- [ ] Data and model versions frozen two hours before presenting.
- [ ] Tested on the presentation laptop and projector at its actual resolution.
- [ ] 2D fallback verified by forcing low fps.
- [ ] Fixture fallback verified with the API stopped.
- [ ] Full-run backup video recorded.
- [ ] Three clean rehearsals finishing under 4:30.

## 11. Delivery checklist

- [ ] Public repo, MIT or Apache-2.0 for code; note ODbL/CC BY-SA obligations
      for any published derived data.
- [ ] `README.md`: what it does, one architecture diagram, quickstart
      (`make install && make all && make serve && make web`), the limitations
      list, and credits to OSM, OpenCelliD, Copernicus, WNTR, pandapower,
      AequilibraE, Hazus, and IN-CORE for method inspiration.
- [ ] `data/SOURCES.md` with every source, licence, and access date.
- [ ] `results/validation.json` committed.
- [ ] CI running `make test` on push.
- [ ] Slides: problem, approach, one screenshot per view, validation table,
      limitations, roadmap.
- [ ] Printed brief in hand.

## 12. Roadmap slide (what comes after)

- Replace synthetic water and fibre layers through utility partnerships.
- Additional hazards: cyclone wind, heat-driven demand surge, earthquake.
- Live feeds (rain gauges, outage reports) to move from planning to operations.
- Surrogate-accelerated city-scale search (spec 06).
- Multi-township comparison for state-level prioritisation.
