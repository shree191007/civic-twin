# 06 — Surrogate model (OPTIONAL)

**Only start this if specs 01–05 are green.** The system must be complete and
demonstrable without it.

**Builds:** `civictwin/surrogate/*`
**Gate:** the surrogate is used only as a *screening filter*; every reported
number is verified by the engine.

---

## 1. Why this exists (the only acceptable justification)

The engine runs one scenario in ~25 ms. Exhaustive searches are therefore
out of reach:

| Search | Simulations | Engine time |
|---|---|---|
| All asset pairs (75 assets) | 2,775 | ~70 s (fine) |
| All asset triples | 67,525 | ~28 min (borderline) |
| All intervention subsets of size 5 from 70 | 12.1 M | impossible |
| Restoration orderings | combinatorial | impossible |

The surrogate makes triples and subset search feasible. **Do not frame it as
"we used a GNN". Frame it as "we searched 67,525 failure combinations instead
of sampling 500."**

## 2. Task definition

**Input:** township graph + a failure/intervention configuration + a hazard
summary.
**Output:** per-zone service loss (person-hours) for the five services, and the
weighted total.

Formally: `f(G, x_asset, x_hazard) -> y ∈ R^{|Z| × 5}`

## 3. Training data — `surrogate/dataset.py`

```python
def generate(engine, township, n_samples: int, seed: int, out: Path) -> None
```

Sample each record as:
- With probability 0.5: a real hazard scenario (sampled as in spec 02 §4.1).
- With probability 0.3: no hazard, with `k ~ {1,2,3}` random forced failures.
- With probability 0.2: a hazard scenario plus a random intervention subset of
  size `~ {0..6}`.

Record per sample: node features, the configuration, and the engine's output.
Target size: **50,000 samples** (~25 minutes of engine time single-threaded,
parallelise with `ProcessPoolExecutor`).

Split 80/10/10 into train/val/test **by hazard scenario**, never by sample, so
the test set contains storms the model has never seen.

### 3.1 Node features (per asset, 14 dims)

`[one-hot portfolio (5), normalised served_population, hand_m/10,
fragility_median_m/3, backup_hours/48, fuel_hours/72, capacity (normalised),
flood_depth_peak/3, is_forced_failed, is_invulnerable, is_hardened]`

### 3.2 Zone features (per zone, 6 dims)

`[population/10000, vulnerable_fraction, hand_m/10, n_towers,
water_storage_hours/24, peak_depth/3]`

### 3.3 Edge types

One relation per `LinkKind` plus `serves` (asset → zone) and `road_adjacent`
(zone → zone). Store as a `HeteroData` object.

## 4. Model — `surrogate/model.py`

```python
class CascadeGNN(torch.nn.Module):
    """Heterogeneous message-passing over the ontology graph.

    Message passing along dependency edges mirrors how cascades propagate,
    so depth is set to the longest dependency chain in the township (+1).
    """
```

- `HeteroConv` with `SAGEConv` per relation, 4 layers, hidden 128, ReLU,
  layer norm, residual connections.
- **Edge direction matters:** propagate along provider → dependent, and add a
  reverse relation so zones can inform upstream representations.
- Readout: per-zone MLP head producing 5 outputs, `softplus` activation
  (losses are non-negative).
- Loss: Huber on `log1p(person_hours)` per zone-service, plus an auxiliary MSE
  term on the weighted total. Weight the auxiliary term at 0.3.

Parameter count target: under 1 M.

## 5. Training — `surrogate/train.py`

```
usage: train.py --data PATH --out PATH [--epochs 60] [--batch 64]
                [--lr 1e-3] [--seed 0] [--device auto]
```

- AdamW, cosine schedule, early stopping on validation weighted-total MAE
  with patience 8.
- Log per-epoch: train loss, val MAE (person-hours), val Spearman correlation
  on the weighted total, val top-20 recall (below).
- Save the best checkpoint plus a `metrics.json`.

## 6. Evaluation (the metrics that actually matter)

Absolute accuracy is secondary. What matters is **ranking**, because the
surrogate is a filter.

| Metric | Target | Meaning |
|---|---|---|
| Spearman ρ on weighted total (test) | ≥ 0.92 | ranks configurations correctly |
| **Top-20 recall @ 200** | ≥ 0.95 | of the true 20 worst configurations, how many appear in the surrogate's top 200 |
| MAE / mean loss | ≤ 0.15 | rough calibration |
| Inference time per config | ≤ 1 ms batched | the point of the exercise |

**Top-20 recall is the gate.** If it is below 0.95, the surrogate is not safe to
use as a filter and must not be used — report that and fall back to sampling.

## 7. Integration (strict rules)

```python
class ScreenedSearch:
    def __init__(self, engine, surrogate, verify_top_k: int = 200): ...
    def worst_combinations(self, k: int, size: int) -> list[CriticalSet]:
        """1. Enumerate all combinations of the given size.
           2. Score every one with the surrogate.
           3. Take the top `verify_top_k`.
           4. Re-simulate those with the ENGINE.
           5. Return the true top `k`, ranked by engine values."""
```

**Non-negotiable:**
1. No surrogate number is ever written to `results/` or shown in the UI.
2. Every reported combination carries `verified_by: "engine"`.
3. The output records `screened_n` and `verified_n` so the method is auditable.
4. If `torch` is unavailable, `ScreenedSearch` falls back to sampling and sets
   `method: "sampled"` in the output.

## 8. Where it is used

| Use | Without surrogate | With surrogate |
|---|---|---|
| N-2 critical pairs | top 25 assets → 300 pairs | all 2,775 pairs |
| N-3 critical triples | not attempted | all 67,525, top 200 verified |
| Optimiser candidate screening | 40 candidates per greedy step | all candidates screened, top 40 verified |
| Restoration local search | 200 perturbations | 20,000 perturbations, top 50 verified |

Record in `results/meta.json` which searches used screening.

## 9. Reporting language (for slides and README)

Correct: "We trained a surrogate on 50,000 simulations so we could search all
67,525 three-asset failure combinations, then verified the worst 200 with the
full simulator."

Incorrect: "We used a graph neural network to predict infrastructure failures."

## 10. Acceptance tests

1. `test_dataset_split_by_scenario`: no hazard scenario appears in both train
   and test.
2. `test_model_forward_shape`: output shape is `(n_zones, 5)`.
3. `test_inference_speed`: 1000 configurations scored in under 1 s batched.
4. `test_top20_recall_gate`: loads the trained checkpoint and asserts recall
   ≥ 0.95; the test is skipped (not failed) if no checkpoint exists.
5. `test_screened_search_verifies`: every returned combination has
   `verified_by == "engine"` and its loss matches a fresh engine run.
6. `test_fallback_without_torch`: with `torch` monkeypatched to raise on import,
   `ScreenedSearch` still returns results with `method == "sampled"`.
