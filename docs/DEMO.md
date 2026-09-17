# Five-minute demo script

Everything except the judge-picked failure is precomputed. Run
`make all` beforehand, then `make serve` and `make web`.

The arc, in one line: **a forecast arrives, the cascade is simulated, a hidden
dependency is found, the impact is quantified, and a package is recommended.**
The reveal is that *the asset was not the problem — the hidden dependency was.*

| Time | Beat | Screen | What it proves |
|---|---|---|---|
| 0:00–0:25 | Open on a documented local incident where one failure cascaded across sectors. Cite the source on the slide. | Slide | The problem is real, not hypothetical |
| 0:25–0:55 | `/scenario` → **Forecast to impact**. Paste an ensemble forecast from an external model. Say plainly: *we do not forecast weather, we start here.* Out comes a range — people affected, with a confidence level and the reasons it is uncertain. | Forecast panel | Hazard ingestion, and honesty about precision |
| 0:55–1:35 | `/explore`. The township as four stacked layers. Click **H2** and trace **up**: a hospital on high ground that fails because of a substation two portfolios away. | Layer cake + dependency graph | The systems view |
| 1:35–2:25 | `/scenario`. Rainfall to the 1-in-50 storm, scrub 0→72 h. Switch to **state** mode (`A`) and narrate the cascade in an operator's words: the tower goes *operational → backup → critical → failed* as its battery runs down; the pump enters backup mode; the generator runs dry because the fuel truck cannot cross. | Map + portfolio state plot | Cross-sector propagation over time |
| 2:25–3:00 | `/risk`. Effective redundancy: T5 and T6 are two paths on paper and one in practice — *they share S2 for power and FIBRE_4 for backhaul*. Then the SPOF cards, each with a **measured** counterfactual: this is what it costs, not what it looks like. | Redundancy panel + SPOF cards | What asset-by-asset monitoring misses |
| 3:00–3:50 | `/plan`. Pick a priority — **protect human life** buys different things from **minimise economic loss**; show it. Drag the budget along the frontier, then split screen: same storm, asset-by-asset left, optimised right, counters ticking. | Priority modes + frontier + split | Decision value, and the proof |
| 3:50–4:25 | Provenance mode (`R`); the **assumption ledger** — "why does Civic-Twin think this?" — with its weakest-link standing; confidence badges on each recommendation. | Map + ledger | Rigour and honesty |
| 4:25–5:00 | Hand over the printed brief. Close on one number, stated as a range. | `/log` → print | A tangible output |

## Rules for the presenter

- Speak in **people and hours**. Never say CVaR without immediately translating it.
- Never apologise for the synthetic layers. Present them as a modelled assumption
  carrying a measured confidence, and point at the value-of-information list.
- Never read out a point estimate. Every headline number is a range with a
  confidence; saying "about eighty-five to a hundred and twenty thousand people,
  medium confidence" is both more honest and more persuasive than a false
  decimal.
- Do not claim the model predicts disasters. The claim is narrower and stronger:
  *given uncertain information about a hazard, this shows how infrastructure
  dependencies turn it into a systemic crisis, and where intervention reduces
  catastrophic risk.*
- The copilot is shown with its **tool trace expanded**, so the audience sees it
  calling the simulator rather than talking.

## Keyboard

| Key | Does |
|---|---|
| `1`–`5` | Switch view |
| `Q A W E R T` | Analysis mode: functionality, operating state, flood, criticality, provenance, risk |
| `Space` | Play / pause the timeline |
| `C` | Copilot drawer |
| `G` | Restore the previous camera |

## After the script

- Hand over the mouse. A judge picks any asset to fail; `/scenario`'s
  "Fail an asset" toggle runs it live (about 40 ms plus the round trip).
- Ask the copilot "what happens if the east bridge and S2 both fail?" with the
  trace expanded.

## Engineering checklist

- [ ] `make all` completed; `results/` populated and `web/public/fixtures/` mirrored
- [ ] Data and model versions frozen two hours before presenting
- [ ] Tested at the projector's actual resolution
- [ ] 2D fallback verified (it engages automatically below 15 fps; the banner is
      a button that forces 3D back on)
- [ ] Fixture fallback verified with the API stopped
- [ ] Backup video recorded
- [ ] Three clean rehearsals under 4:30

## Anticipated questions

| Question | Answer |
|---|---|
| Your water and fibre layers are synthetic — why trust it? | We optimise across 20 plausible versions of the township and report how often each recommendation survives. We also publish which data would change the answer most. |
| How is this different from a GIS risk map? | A risk map ranks assets independently. This finds fake redundancy, cross-sector cascades and failure combinations — none of which are visible asset by asset. That is exactly what the split screen compares. |
| How did you validate it? | Each fast layer is checked against the established sector tool where it is installed; every recommendation is tested on held-out scenarios; `results/validation.json` reports what passed and what did not, including the checks that are skipped for want of data. |
| Have you tested it against a real flood? | Not yet, and the artefact says so rather than implying otherwise. The replay harness is built and tested — hazard footprint by IoU, exposure, cascade sectors, and a decision counterfactual — and needs a documented event to run against. |
| Isn't a "digital twin" a real-time thing? | A continuously synchronised twin is the production direction. What this is today is a dependency-aware stochastic resilience simulator, which is the part that makes the decision. |
| Why CVaR and not expected loss? | Planners have to survive the bad years. Expected loss averages away the events that matter. |
| Does this scale beyond one township? | The pipeline builds a township from any bounding box of open data. |
| Could this be misused? | A dependency map is a list of weak points. The API has a role gate: the public role never sees criticality rankings or SPOFs. |
