# 02 — Simulation engine

**Builds:** `civictwin/engine/*`, `tests/test_hazard.py`, `test_damage.py`,
`test_layers.py`, `test_coordinator.py`

**Gate:** those tests pass; a single 72-hour scenario runs in < 25 ms.

---

## 1. Core types — `civictwin/engine/contract.py`

```python
@dataclass(frozen=True, slots=True)
class HazardScenario:
    id: str
    seed: int
    rain_mm: float
    field_seed: int
    onset_hour: int            # 0-23, hour of day the storm starts
    asset_draws: dict[str, float]   # asset_id -> U(0,1), fixed per scenario
    return_period_y: float | None = None
    label: str | None = None        # e.g. "historical-2015"

@dataclass(frozen=True, slots=True)
class Overlay:
    """A scenario branch: everything that differs from the baseline township."""
    interventions: tuple[str, ...] = ()      # intervention ids (spec 03 §5)
    forced_failures: tuple[str, ...] = ()    # assets forced to COMPLETE at t=0
    invulnerable: tuple[str, ...] = ()       # assets that cannot be damaged
    param_overrides: Mapping[str, float] = field(default_factory=dict)
    flood_enabled: bool = True

@dataclass(slots=True)
class SharedState:
    t: float
    flood_depth: dict[str, float]        # asset_id -> depth (m)
    road_depth: dict[str, float]         # edge_id -> depth (m)
    damage: dict[str, DamageState]
    functionality: dict[str, float]      # asset_id -> [0,1]
    power_available: dict[str, float]
    comms_available: dict[str, float]
    water_supply: dict[str, float]
    road_open: dict[str, bool]
    travel_time_h: dict[tuple[int, int], float]   # lazily filled cache
    zone_service: dict[str, dict[Service, float]]
    demand_multiplier: dict[Service, float]
    backup_remaining_h: dict[str, float]
    fuel_remaining_h: dict[str, float]
    tank_level_h: dict[str, float]       # hours of supply left in each tank
    manual_until_h: dict[str, float]     # SCADA-lost assets awaiting a crew

class LayerModel(Protocol):
    portfolio: Portfolio
    def reset(self, township: Township, cfg: Config, rng: Generator) -> None: ...
    def step(self, state: SharedState, cfg: Config) -> None:
        """Read inputs from `state`, write this layer's outputs into `state`.
        MUST NOT mutate fields owned by other layers (see §3 ownership table)."""
```

```python
@dataclass(slots=True)
class SimResult:
    scenario_id: str
    overlay_hash: str
    weighted_loss_ph: float
    loss_by_service_ph: dict[Service, float]
    loss_by_zone_ph: dict[str, float]
    vulnerable_loss_ph: float
    peak_functionality_loss: dict[Portfolio, float]
    recovery_90_h: dict[Portfolio, float]     # inf if never recovers in horizon
    damaged_assets: dict[str, DamageState]
    amplification_ratio: float
    timeline: list[TimelineFrame] | None
```

## 2. Execution order (authoritative)

Per time step `t`, the coordinator executes **exactly** this sequence:

```
1. hazard.update(state, t)           # flood depths for assets and roads
2. damage.update(state, t)           # new damage from rising water
3. transport.step(state)             # road_open, travel times
4. response.step(state)              # crews move/repair, fuel trucks, refuel
5. energy.step(state)                # incl. operator de-energisation
6. comms.step(state)                 # needs power
7. water.step(state)                 # needs power + comms (SCADA)
8. services.step(state)              # needs power + water + roads + comms
9. demand.step(state)                # updates demand multipliers for next step
10. loss.accumulate(state, dt)       # zone services -> person-hours
```

**Feedback rule:** where a layer needs a value produced later in the order
(e.g. stormwater drainage depends on energy), it reads the **previous step's**
value from `state`. This is documented, not accidental. At `t=0` the previous
value is the pre-event (fully functional) value.

## 3. Field ownership (a layer may only write the fields it owns)

| Field | Owner |
|---|---|
| `flood_depth`, `road_depth` | `hazard` |
| `damage` | `damage`, `response` (repairs only) |
| `road_open`, `travel_time_h` | `transport` |
| `power_available`, `functionality[energy assets]` | `energy` |
| `comms_available`, `functionality[comms assets]` | `comms` |
| `water_supply`, `tank_level_h`, `functionality[water assets]` | `water` |
| `functionality[service assets]` | `services` |
| `zone_service` | each layer writes its own `Service` key; `services` writes HEALTH; `transport` writes MOBILITY |
| `backup_remaining_h`, `fuel_remaining_h` | the layer owning the asset; refuelling by `response` |
| `manual_until_h` | `response` |
| `demand_multiplier` | `demand` |

Enforce with an assertion in debug mode (`CIVICTWIN_STRICT=1`): wrap `state`
in a guard object that raises on illegal writes. Ship it disabled by default.

## 4. Hazard — `civictwin/engine/hazard.py`

### 4.1 Scenario sampling

```python
def sample_scenarios(township, cfg, n: int, seed: int) -> list[HazardScenario]
```

- `rain_mm ~ Gumbel(loc=cfg.hazard.gumbel_loc_mm, scale=cfg.hazard.gumbel_scale_mm)`,
  clipped to `[0, 600]`.
- `field_seed ~ randint(0, 2**31)`.
- `onset_hour ~ randint(0, 24)`.
- `asset_draws[a] ~ U(0,1)` for every asset id, **generated in sorted asset-id
  order** so that adding an asset later does not perturb existing draws
  (use a per-asset `Generator(PCG64(hash))` derived from `(seed, asset_id)`).
- `return_period_y = 1 / (1 - gumbel_cdf(rain_mm))`.

**Common random numbers:** `asset_draws` are the only source of failure
randomness. Two runs with the same scenario and different overlays share draws.

### 4.2 Spatial field

```python
def spatial_field(x: float, y: float, field_seed: int, cfg, extent_m: float) -> float
```

Sum of `cfg.hazard.field_bumps` Gaussian bumps with centres drawn uniformly in
the extent, width `field_sigma_frac * extent_m`, signed amplitudes in
`±cfg.hazard.field_amplitude`. Return `clip(1.0 + Σ bumps, 0.3, 1.8)`.
Precompute bump centres/amplitudes once per scenario; the function must be
vectorisable over arrays of x, y.

### 4.3 Fluvial depth

```
W      = max(0, (rain_mm - rain_threshold_mm) / rain_scale_mm)     # "water level index"
peak_m = W * spatial_field(x, y) * 3.0                              # metres of river rise
depth(x, y, t) = max(0, peak_m * profile(t) - hand_m(x, y))
```

`profile(t)`: linear rise from 0 to 1 over `cfg.hazard.rise_hours`, then linear
decay to 0 at `rise_hours + recession_base_h + recession_per_w_h * W`.

### 4.4 Pluvial depth (with the stormwater feedback)

```
excess_mm_h = max(0, rain_intensity_mm_h - effective_drain_capacity)
pond_m      = excess_mm_h * pluvial_pond_factor * basin_factor(x, y)
```

- `rain_intensity_mm_h = rain_mm / (rise_hours * 2)` during the storm, 0 after.
- `effective_drain_capacity = pluvial_drain_capacity_mm_h * drain_factor`,
  where `drain_factor` = mean functionality of the `STORMWATER_PUMP` assets
  whose basin contains the point, read from the **previous step**. If those
  pumps are down, capacity falls to 40% (`0.4 + 0.6 * mean_functionality`).
- `basin_factor(x, y)` = 1.0 in the two designated low basins, 0.35 elsewhere.
- Total depth = `max(fluvial, pluvial)` — not a sum.

### 4.5 Required API

```python
class HazardModel:
    def __init__(self, township: Township, scenario: HazardScenario, cfg: Config): ...
    def update(self, state: SharedState, t: float) -> None
    def max_depth(self, asset_id: str) -> float      # over the whole horizon
    def depth_series(self, asset_id: str, times) -> np.ndarray
```

Precompute per-asset and per-edge `peak_m` at construction. `update()` must be
O(|assets| + |edges|) with no allocation in the loop.

## 5. Damage — `civictwin/engine/damage.py`

### 5.1 Fragility

For asset `a` with median `m = a.fragility_median_m` and `β = a.fragility_beta`,
the probability of reaching **at least** damage state `k` at depth `d`:

```
P_k(d) = Φ( ln(d / (m * state_multipliers[k])) / β )        for d > 0, else 0
```

where `state_multipliers` indexes SLIGHT..COMPLETE from `DamageConfig`.

### 5.2 Damage state assignment

Using the scenario's fixed draw `u = asset_draws[a.id]`:

```
state = the highest k such that P_k(depth) > u   (NONE if none exceed u)
```

Damage is **monotonic in time**: once assigned, a state may only increase
(as water rises) and may only decrease through repair (`response`).

Assets with `fragility_median_m >= 99.0` never take direct damage.

### 5.3 `HOSTED_ON` propagation

After direct damage, for every `HOSTED_ON` link `host → hosted`:
`damage[hosted] = max(damage[hosted], damage[host])`.
Apply transitively (hosts may themselves be hosted).

### 5.4 Roads

Roads are not damaged, they are **impassable**:

```
open = depth < cfg.road.impassable_depth_m  AND  host asset (if any) damage < EXTENSIVE
speed_factor = 1.0                    if depth <= 0.05
             = clip(1 - depth/0.30, 0.15, 1.0)   for 0.05 < depth < 0.30
```

Travel time on an edge = `length_m / (free_flow_kph * speed_factor * 1000) `
converted to hours; `inf` when closed.

### 5.5 Repair times

```
repair_h = a.repair_hours_base * repair_multipliers[damage_index(state)]
```
Sampled deterministically: multiply by `0.7 + 0.6 * u_repair` where
`u_repair` is derived from `(scenario.seed, asset_id, "repair")`.

### 5.6 Required API

```python
class DamageModel:
    def __init__(self, township, scenario, overlay, cfg): ...
    def update(self, state: SharedState, t: float) -> None
    def repair_hours(self, asset_id: str, state: DamageState) -> float
```

`overlay.invulnerable` assets are skipped entirely.
`overlay.forced_failures` assets are set to `COMPLETE` at `t = 0` and are not
subject to fragility.

## 6. Energy layer — `civictwin/engine/layers/energy.py`

**Inputs:** `damage`, `flood_depth`, `road_depth` (for de-energisation),
`comms_available` (previous step, for SCADA), `fuel_remaining_h`.
**Outputs:** `power_available[asset]` for every asset, `functionality` for
energy assets, `zone_service[z][ENERGY]`.

### 6.1 Algorithm (fast model)

```
1. base_f[a] = residual_functionality[damage[a]] for energy assets.
2. Operator de-energisation:
     for each feeder F:
       flooded_fraction = (length of F's zone edges with depth > 0.15) / total
       if flooded_fraction > cfg.energy.deenergise_flood_fraction:
           base_f[F] = 0.0 ; mark F as "de-energised (safety)"
3. Source connectivity:
     Build the energy graph from POWERS links restricted to energy assets.
     An asset is energised iff there is a path from a GRID_SUPPLY with
     base_f > 0 along assets with base_f > 0.
4. Load transfer:
     For each de-energised feeder F with an available tie to feeder G
     (represented as a POWERS link between feeders in the township):
       spare = G.capacity * (1 - cfg.energy.transfer_capacity_margin) - load(G)
       transferred = min(load(F), spare)
       power_available for F's zones = transferred / load(F)
5. power_available[a] for a non-energy asset = the functionality of its
   POWERS provider chain (min over providers, since all are required).
6. zone_service[z][ENERGY] = power_available of the zone's transformer.
```

Record the reason each asset lost power: one of
`{"damaged", "upstream", "deenergised", "transfer_partial"}` in
`state.meta_reason[asset]` — the UI needs this for explanations.

### 6.2 Detailed model (optional)

`energy.detailed.PandapowerAdapter` with the same interface, used only when
`cfg.detailed_energy` is true and `pandapower` imports. It builds a net from
buses (substations, feeders, transformers), runs `pp.runpp`, and reports a
feeder as failed if it is isolated or if any line loading exceeds 100%.
Verified against the fast model in spec 08.

## 7. Water layer — `civictwin/engine/layers/water.py`

**Inputs:** `damage`, `power_available`, `comms_available` (SCADA),
`manual_until_h`.
**Outputs:** `water_supply`, `tank_level_h`, `functionality` for water assets,
`zone_service[z][WATER]`.

### 7.1 Algorithm

```
1. f[a] = residual_functionality[damage[a]] for water assets.
2. Power gate: for each of INTAKE, TREATMENT, PUMP, STORMWATER_PUMP:
     if power_available[a] == 0:
       if fuel_remaining_h[a] > 0: consume dt, f unchanged
       else: f[a] = 0
3. SCADA gate: if a.scada_controlled and comms_available[a] == 0:
     if state.manual_until_h[a] > t: f[a] = 0    # crew has not arrived yet
4. Supply chain: f_effective = min(f along INTAKE -> TREATMENT -> PUMP chain).
5. Tank dynamics, per tank K with capacity_hours = K.capacity:
     inflow  = f_effective of the pump feeding K
     outflow = 1.0 * demand_multiplier[WATER]
     tank_level_h[K] += (inflow - outflow) * dt, clipped to [0, capacity_hours]
6. zone_service[z][WATER]:
     if tank_level_h[zone's tank] > 0: 1.0
     else: inflow fraction (partial supply straight through)
     Then apply household storage: a zone keeps service for
     water_storage_hours after supply ends (track a per-zone countdown).
7. Hospitals draw from SUPPLIES_WATER links; same logic with their own storage.
```

### 7.2 Detailed model (optional)

`water.detailed.WNTRAdapter` builds a WNTR model with pressure-dependent demand
and returns per-zone satisfied demand. Used only for hero scenarios.

## 8. Comms layer — `civictwin/engine/layers/comms.py`

**Inputs:** `damage`, `power_available`, `demand_multiplier[COMMS]`.
**Outputs:** `comms_available`, `functionality` for comms assets,
`zone_service[z][COMMS]`.

### 8.1 Algorithm

```
1. f[a] = residual_functionality[damage[a]] for comms assets.
2. Backhaul: a TOWER needs a path to an EXCHANGE over BACKHAULS links whose
   FIBRE assets have f > 0. If no path: f[tower] = 0.
3. Power and battery:
     if power_available[tower] == 0:
        drain = dt * (load_factor ** cfg.comms.battery_load_exponent)
        backup_remaining_h[tower] -= drain
        if backup_remaining_h[tower] <= 0: f[tower] = 0
     else:
        # batteries do NOT recharge within the horizon (documented simplification)
        pass
4. Handover and congestion:
     For each zone, live_towers = [t for t in zone.towers if f[t] > 0]
     If empty -> zone_service[COMMS] = 0
     Else:
       offered = zone.population * demand_multiplier[COMMS]
       For each live tower, sum offered load from all its zones.
       capacity_eff = tower.capacity * cfg.comms.handover_capacity_factor
       tower_service = min(1.0, capacity_eff / offered_load_on_tower)
       zone_service[COMMS] = max over its live towers of tower_service
5. load_factor for battery drain = offered_load_on_tower / tower.capacity,
   clipped to [0.2, 2.0].
6. comms_available[a] for a SCADA-dependent asset = 1.0 if the CONTROLS
   provider exchange has f > 0 else 0.0.
```

## 9. Transport layer — `civictwin/engine/layers/transport.py`

**Inputs:** `road_depth`, `damage` (bridges, underpasses).
**Outputs:** `road_open`, `travel_time_h`, `zone_service[z][MOBILITY]`.

### 9.1 Algorithm

```
1. Compute open/closed and speed factors (spec §5.4).
2. Build a signature = frozenset of closed edge ids.
   If the signature matches the previous step, reuse all cached paths.
3. Multi-source Dijkstra from each "interest node" set:
     - hospital nodes (for HEALTH)
     - depot nodes (for crews)
     - fuel station nodes (for fuel trucks)
   Use one Dijkstra per source set, not per pair.
4. zone_service[z][MOBILITY] =
     (population reachable from z within cfg.sim.mobility_access_minutes)
     / (population reachable under no-flood conditions)
   Precompute the denominator once at construction.
```

**Performance:** Dijkstra over 169 nodes is trivial; the signature cache is what
keeps 1000 scenarios fast. Cache must be per-simulation, not global.

### 9.2 Detailed model (optional)

`transport.detailed.AequilibraeAdapter` runs user-equilibrium assignment with
BPR (`alpha=0.15`, `beta=4`) over an OD matrix derived from zone populations.
Hero scenarios only; outputs congested travel times that replace free-flow times.

## 10. Services layer — `civictwin/engine/layers/services.py`

**Inputs:** everything.
**Outputs:** `functionality` for service assets, `zone_service[z][HEALTH]`.

```
For each HOSPITAL / CLINIC h:
  physical = residual_functionality[damage[h]]
  power    = 1 if power_available[h] > 0
             else (1 if fuel_remaining_h[h] > 0 else 0)   # consume dt
  water    = 1 if water_supply[h] > 0 or its storage countdown > 0 else 0
  comms    = 1 if comms_available[h] > 0 else 0.8   # degraded, not fatal
  staff    = 1 if h is reachable from ≥1 shelter/zone node else 0.6
  f[h] = physical * min(power, water) * comms * staff

zone_service[z][HEALTH] =
   max over hospitals h of ( f[h] if travel_time(z.node -> h.node)
                             <= cfg.sim.health_access_minutes/60 else 0 )
```

Clinics count at 0.4 weight of a hospital for HEALTH (they handle minor cases).

## 11. Response — `civictwin/engine/response.py`

### 11.1 Crews

State per crew: `location_node`, `task` (`idle` | `enroute:<asset>` |
`repairing:<asset>`), `eta_h`, `finish_h`.

```
Dispatch policy (fast model), evaluated whenever a crew is idle:
  candidates = assets with damage > NONE, portfolio matching the crew,
               site depth < cfg.response.site_dry_depth_m,
               reachable from the crew's current node (travel_time < inf)
  score = served_population(asset) / (travel_time_h + repair_hours(asset))
  pick argmax score; ties broken by asset id (determinism)
```

Travel time uses the **current** road network; if the asset becomes unreachable
mid-journey, the crew returns to its depot and re-dispatches.

Repair completes at `t_arrive + repair_hours`; on completion set
`damage[asset] = NONE` and clear it from `manual_until_h`.

**Co-location bonus:** if a crew repairs an asset with `HOSTED_ON` dependents at
the same node, those are repaired at 30% additional time each.

### 11.2 Fuel

Each asset with `fuel_hours > 0` decrements `fuel_remaining_h` by `dt` whenever
it is running on generator. When `fuel_remaining_h < 8`, a fuel truck is
dispatched from the nearest functioning `FUEL_STATION`. On arrival,
`fuel_remaining_h += cfg.response.refuel_adds_hours`.
Fuel trucks are unlimited in number but constrained by road access. If no
functioning fuel station is reachable, the generator runs dry.

### 11.3 Manual operation

When a `scada_controlled` asset loses `comms_available`, set
`manual_until_h[asset] = t + cfg.response.manual_operation_penalty_h` and
dispatch the matching crew as if it were a repair task of that duration.

## 12. Demand — `civictwin/engine/demand.py`

```
demand_multiplier[COMMS]  = 1 + (cfg.comms.demand_surge_factor - 1) * surge(t)
demand_multiplier[HEALTH] = 1 + 0.8 * surge(t)
demand_multiplier[WATER]  = 1.0
demand_multiplier[ENERGY] = diurnal(t, onset_hour)     # 0.7..1.25
surge(t) = exp(-max(0, t - peak_t) / 18) with peak_t = cfg.hazard.rise_hours
```

Evacuation (simple): zones with `flood_depth > 0.5` move
`min(0.6, depth)` of their population to the nearest functioning `SHELTER`.
Moved population is counted at the shelter's zone for service loss.

## 13. Loss — `civictwin/engine/loss.py`

```python
def accumulate(state: SharedState, dt: float, acc: LossAccumulator) -> None
```

For each zone and each `Service`:
```
unmet = population * (1 - zone_service[z][service]) * dt
acc.by_service[service] += unmet
acc.by_zone[z]          += unmet * weight(service)
acc.vulnerable          += unmet * z.vulnerable_fraction * weight(service)
```
`weighted_loss_ph = Σ_service weight(service) * by_service[service]`.

**Amplification ratio:** after the run, for each directly damaged asset,
re-run with only that asset failing (no flood) to get its standalone loss,
then `amplification = weighted_loss / max(1.0, Σ standalone)`.
Cache standalone losses on the `Engine` — they do not depend on the scenario.

## 14. Coordinator — `civictwin/engine/coordinator.py`

```python
class Engine:
    def __init__(self, township: Township, cfg: Config = DEFAULT): ...
    def simulate(self, scenario: HazardScenario, overlay: Overlay = Overlay(),
                 record: bool = False) -> SimResult
    def simulate_many(self, scenarios: Sequence[HazardScenario],
                      overlay: Overlay = Overlay(),
                      n_jobs: int = 1) -> list[SimResult]
    def standalone_loss(self, asset_id: str) -> float   # cached
```

- `overlay.interventions` are resolved via `analysis.interventions.apply()`
  (spec 03 §5) which returns a modified copy of per-asset parameters — the
  `Township` itself is never mutated.
- `record=True` appends a `TimelineFrame` each step (schema below).
- Early exit: if at some step every asset has `damage == NONE`,
  every service is 1.0, and the hazard has fully receded, break out of the loop
  and report the accumulated loss.

### 14.1 `TimelineFrame` schema (this is what the UI consumes)

```json
{
  "t": 9.0,
  "flood": {"S2": 1.42, "P2": 1.80, "e114": 0.55},
  "func": {"S2": 0.0, "T5": 0.0, "P2": 0.0, "H2": 0.35},
  "damage": {"S2": "extensive", "P2": "complete"},
  "reason": {"T5": "upstream:S2", "H2": "water"},
  "closed_roads": ["e114", "e115"],
  "crews": [{"id": "power-1", "node": 84, "task": "enroute:S2", "eta_h": 2.5}],
  "zones": {
    "Z10": {"energy": 0.0, "water": 0.3, "comms": 0.55, "health": 0.0, "mobility": 0.71}
  },
  "totals": {"people_no_power": 24100, "people_no_water": 18800,
             "people_no_comms": 9200, "people_no_health": 31000}
}
```

`reason` strings: `damaged`, `upstream:<id>`, `deenergised`, `battery`,
`backhaul`, `water`, `fuel`, `scada`, `access`.

## 15. Performance requirements

| Operation | Target |
|---|---|
| One 72-h simulation, `record=False` | ≤ 25 ms |
| One 72-h simulation, `record=True` | ≤ 60 ms |
| 1000 scenarios, single-threaded | ≤ 30 s |
| `Engine` construction | ≤ 200 ms |

Optimisation rules: preallocate dicts once and mutate in place; never build a
new graph inside the loop; cache Dijkstra by closed-edge signature; avoid
`copy.deepcopy` anywhere in the hot path.

## 16. Acceptance tests

### `tests/test_hazard.py`
1. `test_no_rain_no_flood`: `rain_mm = 0` → all depths 0 at all times.
2. `test_depth_monotone_in_rain`: higher `rain_mm` never lowers any depth.
3. `test_hand_protects`: an asset with `hand_m = 14` never floods below 400 mm rain.
4. `test_recession`: depth returns to 0 before the horizon for rain ≤ 300 mm.
5. `test_field_deterministic`: same `field_seed` → identical field.
6. `test_pluvial_feedback`: with stormwater pumps forced to 0 functionality,
   basin depth increases by ≥ 20% versus the same scenario with them running.

### `tests/test_damage.py`
7. `test_fragility_monotone`: `P_k(d)` increasing in `d`, decreasing in `k`.
8. `test_damage_monotone_in_time`: damage state never decreases without repair.
9. `test_common_random_numbers`: same scenario, two overlays → identical
   damage for assets unaffected by the overlay.
10. `test_hosted_on_propagates`: damaging `B1` to EXTENSIVE damages both fibres.
11. `test_invulnerable`: an asset in `overlay.invulnerable` never takes damage.
12. `test_forced_failure`: an asset in `forced_failures` is COMPLETE at t=0.

### `tests/test_layers.py`
13. `test_energy_isolation`: forcing `S2` to fail zeroes ENERGY only in zones
    served by S2's feeders.
14. `test_energy_deenergisation`: flooding 30% of a feeder's area de-energises
    it even with zero damage.
15. `test_load_transfer`: with a tie link and spare capacity, a de-energised
    feeder's zones get partial power.
16. `test_battery_delay`: a tower with `backup_hours=4` keeps service for
    ~4 h after its substation fails (±1 step), then drops.
17. `test_battery_load_dependence`: with a demand surge, the same tower drops
    earlier than without.
18. `test_backhaul_cut`: failing `B1` (hosting both fibres) zeroes COMMS in all
    east zones regardless of power.
19. `test_water_tank_drain`: a zone with `water_storage_hours=6` retains WATER
    for 6 h after its pump fails.
20. `test_scada_manual_penalty`: failing `X2` delays P2's restart by
    `manual_operation_penalty_h`.
21. `test_hospital_cross_portfolio`: `H2` loses HEALTH when `S2` fails, even
    though `H2` itself is never flooded. **(This is trap #3 — must pass.)**
22. `test_transport_bridge_closure`: closing both bridges makes east-bank
    hospitals unreachable from west zones (HEALTH → 0 there).
23. `test_mobility_baseline_is_one`: with no flood, MOBILITY == 1.0 everywhere.

### `tests/test_coordinator.py`
24. `test_zero_hazard_zero_loss`: `flood_enabled=False`, no forced failures →
    `weighted_loss_ph == 0`.
25. `test_determinism`: same scenario + overlay twice → identical `SimResult`
    (compare full dict).
26. `test_monotonicity_hardening`: for 50 random scenarios, applying any single
    hardening intervention never increases `weighted_loss_ph`.
27. `test_monotonicity_backup`: same for any backup intervention.
28. `test_recovery_requires_access`: with both bridges forced to COMPLETE,
    east-bank assets are never repaired within the horizon.
    **(This is trap #4 — must pass.)**
29. `test_fuel_runs_dry`: a hospital with `fuel_hours=48` and no reachable fuel
    station loses power at ~48 h.
30. `test_amplification_above_one`: for a scenario that fails `S2`, the
    amplification ratio exceeds 1.0.
31. `test_timeline_schema`: `record=True` frames validate against §14.1
    (all keys present, types correct, `t` strictly increasing).
32. `test_performance`: 200 scenarios complete in under 8 seconds.
