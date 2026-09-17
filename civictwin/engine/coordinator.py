"""The simulation coordinator: wires hazard, damage, layers, response and loss."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from civictwin.config import DEFAULT, Config
from civictwin.engine.contract import (
    HazardScenario,
    Overlay,
    SharedState,
    SimResult,
    TimelineFrame,
)
from civictwin.engine.damage import DamageModel
from civictwin.engine.demand import DemandModel
from civictwin.engine.hazard import HazardModel
from civictwin.engine.layers.comms import CommsLayer
from civictwin.engine.layers.energy import EnergyLayer
from civictwin.engine.layers.services import ServicesLayer
from civictwin.engine.layers.transport import TransportLayer
from civictwin.engine.layers.water import WaterLayer
from civictwin.engine.loss import accumulate, make_accumulator
from civictwin.engine.response import POLICY_GREEDY, ResponseModel
from civictwin.engine.states import classify
from civictwin.ontology import (
    AssetKind,
    DamageState,
    OperatingState,
    Portfolio,
    Service,
    Township,
)

logger = logging.getLogger(__name__)

RECOVERY_THRESHOLD = 0.9


@dataclass(slots=True)
class _Bundle:
    """A set of layer models bound to one (possibly augmented) township."""

    township: Township
    energy: EnergyLayer
    comms: CommsLayer
    water: WaterLayer
    transport: TransportLayer
    services: ServicesLayer


class Engine:
    """Runs 72-hour flood scenarios over a township."""

    def __init__(self, township: Township, cfg: Config = DEFAULT) -> None:
        self.township = township
        self.cfg = cfg
        self._bundles: dict[tuple[str, ...], _Bundle] = {}
        self.last_dispatch_log: list[tuple[str, str]] = []
        self._standalone: dict[str, float] = {}
        self._base = self._bundle(())

    # ----------------------------------------------------------- layer setup

    def _bundle(self, extra_links: tuple[str, ...]) -> _Bundle:
        cached = self._bundles.get(extra_links)
        if cached is not None:
            return cached
        from civictwin.analysis.interventions import apply_structural

        town = apply_structural(self.township, extra_links)
        rng = np.random.default_rng(town.seed)
        transport = TransportLayer()
        bundle = _Bundle(
            township=town,
            energy=EnergyLayer(),
            comms=CommsLayer(),
            water=WaterLayer(),
            transport=transport,
            services=ServicesLayer(transport),
        )
        for layer in (
            bundle.energy,
            bundle.comms,
            bundle.water,
            bundle.transport,
            bundle.services,
        ):
            layer.reset(town, self.cfg, rng)
        self._bundles[extra_links] = bundle
        return bundle

    # -------------------------------------------------------------- the loop

    def simulate(
        self,
        scenario: HazardScenario,
        overlay: Overlay = Overlay(),
        record: bool = False,
        dispatch: tuple[str, Sequence[str] | None] | None = None,
        _skip_amplification: bool = False,
    ) -> SimResult:
        """Run one scenario under one overlay and return its losses.

        Args:
            dispatch: optional (policy, priority) override for the repair crews;
                the resulting dispatch order is left on `self.last_dispatch_log`.
        """
        from civictwin.analysis.interventions import resolve

        effect = resolve(self.township, overlay.interventions, self.cfg)
        cfg = effect.cfg or self.cfg
        bundle = self._bundle(effect.extra_links)
        town = bundle.township

        hazard = HazardModel(town, scenario, cfg, flood_enabled=overlay.flood_enabled)
        damage = DamageModel(town, scenario, overlay, cfg)
        for aid, median in effect.median_override.items():
            damage.set_median_override(aid, median)
        damage.repair_factor = effect.repair_factor

        transport = bundle.transport
        transport.begin()
        state = self._fresh_state(town, effect)
        state.gate.reset(town.links)
        damage.initialise(state)

        demand = DemandModel(town, scenario.onset_hour)
        demand.step(state, cfg)
        policy, priority = dispatch if dispatch is not None else (POLICY_GREEDY, None)
        response = ResponseModel(town, damage, transport, cfg, policy, priority)

        acc = make_accumulator(town)
        dt = cfg.sim.dt_h
        n_steps = int(round(cfg.sim.horizon_h / dt)) + 1
        frames: list[TimelineFrame] | None = [] if record else None

        peak_loss: dict[Portfolio, float] = {p: 0.0 for p in Portfolio}
        recovery: dict[Portfolio, float] = {p: math.inf for p in Portfolio}
        portfolio_assets = {
            p: [a.id for a in town.assets.values() if a.portfolio is p]
            for p in Portfolio
        }

        for i in range(n_steps):
            t = i * dt
            state.t = t
            state.on_backup.clear()
            hazard.update(state, t)
            damage.update(state, t)
            transport.step(state, cfg)
            response.step(state, cfg)
            bundle.energy.step(state, cfg)
            bundle.comms.step(state, cfg)
            bundle.water.step(state, cfg)
            bundle.services.step(state, cfg)
            demand.step(state, cfg)
            self._classify_states(town, state)
            accumulate(state, dt, acc, town, cfg)

            for p, ids in portfolio_assets.items():
                if not ids:
                    continue
                mean_f = sum(state.functionality.get(a, 1.0) for a in ids) / len(ids)
                peak_loss[p] = max(peak_loss[p], 1.0 - mean_f)
                if mean_f >= RECOVERY_THRESHOLD:
                    if math.isinf(recovery[p]):
                        recovery[p] = t
                else:
                    recovery[p] = math.inf

            if record:
                assert frames is not None
                frames.append(self._frame(town, state, response, t))

            if self._settled(state, hazard, t, town):
                break

        self.last_dispatch_log = list(response.dispatch_log)
        weighted = acc.weighted_total(cfg)
        damaged = {
            aid: ds for aid, ds in state.damage.items() if ds is not DamageState.NONE
        }
        amplification = 1.0
        if not _skip_amplification and weighted > 0.0:
            standalone_sum = sum(
                self.standalone_loss(aid) for aid in sorted(damaged)
            )
            amplification = weighted / max(1.0, standalone_sum)

        return SimResult(
            scenario_id=scenario.id,
            overlay_hash=overlay.hash(),
            weighted_loss_ph=weighted,
            loss_by_service_ph=dict(acc.by_service),
            loss_by_zone_ph=dict(acc.by_zone),
            vulnerable_loss_ph=acc.vulnerable,
            peak_functionality_loss=peak_loss,
            recovery_90_h=recovery,
            damaged_assets=damaged,
            amplification_ratio=amplification,
            timeline=frames,
        )

    def simulate_many(
        self,
        scenarios: Sequence[HazardScenario],
        overlay: Overlay = Overlay(),
        n_jobs: int = 1,
    ) -> list[SimResult]:
        """Run a batch of scenarios under one overlay.

        Results are returned in input order and are identical whatever `n_jobs`
        is: scenarios never share state.
        """
        if n_jobs <= 1 or len(scenarios) < 2 * n_jobs:
            return [self.simulate(s, overlay) for s in scenarios]
        from concurrent.futures import BrokenExecutor, ProcessPoolExecutor

        payload = (self.township.to_dict(), self.cfg.to_dict())
        chunks = max(1, len(scenarios) // (n_jobs * 4))
        try:
            with ProcessPoolExecutor(
                max_workers=n_jobs, initializer=_worker_init, initargs=payload
            ) as pool:
                return list(
                    pool.map(
                        _worker_run,
                        ((s, overlay) for s in scenarios),
                        chunksize=chunks,
                    )
                )
        except (BrokenExecutor, RuntimeError, OSError) as exc:
            # Spawn-based platforms need the caller's entry point to be guarded
            # by `if __name__ == "__main__"`. Rather than fail, fall back to the
            # single-threaded path, which produces identical results.
            logger.warning(
                "parallel execution unavailable (%s); falling back to one process",
                exc.__class__.__name__,
            )
            return [self.simulate(s, overlay) for s in scenarios]

    def simulate_batch(
        self,
        jobs: Sequence[tuple[HazardScenario, Overlay]],
        n_jobs: int = 1,
        objective: object | None = None,
    ) -> list[float]:
        """Weighted loss for many (scenario, overlay) pairs, in input order.

        The optimiser's inner loop is a full cross product of candidates and
        tail scenarios, so batching the whole product into one pool is far
        cheaper than one pool per candidate.
        """
        score = _objective_scorer(objective, self.cfg)
        if n_jobs <= 1 or len(jobs) < 2 * n_jobs:
            return [
                score(self.simulate(s, o, _skip_amplification=True)) for s, o in jobs
            ]
        from concurrent.futures import BrokenExecutor, ProcessPoolExecutor

        payload = (self.township.to_dict(), self.cfg.to_dict())
        chunks = max(1, len(jobs) // (n_jobs * 4))
        try:
            with ProcessPoolExecutor(
                max_workers=n_jobs, initializer=_worker_init, initargs=payload
            ) as pool:
                results = list(pool.map(_worker_result, jobs, chunksize=chunks))
            return [score(r) for r in results]
        except (BrokenExecutor, RuntimeError, OSError) as exc:
            logger.warning(
                "parallel execution unavailable (%s); falling back to one process",
                exc.__class__.__name__,
            )
            return [
                score(self.simulate(s, o, _skip_amplification=True)) for s, o in jobs
            ]

    def standalone_loss(self, asset_id: str) -> float:
        """Weighted loss when only this asset fails, with no flood. Cached."""
        cached = self._standalone.get(asset_id)
        if cached is not None:
            return cached
        scenario = HazardScenario(
            id=f"standalone-{asset_id}",
            seed=0,
            rain_mm=0.0,
            field_seed=0,
            onset_hour=0,
            asset_draws={a: 1.0 for a in self.township.assets},
        )
        res = self.simulate(
            scenario,
            Overlay(forced_failures=(asset_id,), flood_enabled=False),
            _skip_amplification=True,
        )
        self._standalone[asset_id] = res.weighted_loss_ph
        return res.weighted_loss_ph

    # ------------------------------------------------------------- internals

    def new_state(self, town: Township | None = None) -> SharedState:
        """A pristine pre-event state. Useful for testing a single layer."""
        from civictwin.analysis.interventions import Effect

        return self._fresh_state(town or self.township, Effect())

    def _fresh_state(self, town: Township, effect: object) -> SharedState:
        backup = {a.id: a.backup_hours for a in town.assets.values()}
        fuel = {a.id: a.fuel_hours for a in town.assets.values()}
        for aid, hours in getattr(effect, "backup_hours", {}).items():
            backup[aid] = hours
        for aid, hours in getattr(effect, "fuel_hours", {}).items():
            fuel[aid] = hours
        return SharedState(
            t=0.0,
            flood_depth={a: 0.0 for a in town.assets},
            road_depth={e: 0.0 for e in town.roads},
            damage={a: DamageState.NONE for a in town.assets},
            functionality={a: 1.0 for a in town.assets},
            power_available={a: 1.0 for a in town.assets},
            comms_available={a: 1.0 for a in town.assets},
            water_supply={a: 1.0 for a in town.assets},
            road_open={e: True for e in town.roads},
            travel_time_h={},
            zone_service={z.id: {s: 1.0 for s in Service} for z in town.zones},
            demand_multiplier={s: 1.0 for s in Service},
            backup_remaining_h=backup,
            fuel_remaining_h=fuel,
            tank_level_h={
                a.id: a.capacity
                for a in town.assets.values()
                if a.kind is AssetKind.TANK
            },
            manual_until_h={},
            drain_factor={a: 1.0 for a in town.assets},
            operating_state={a: OperatingState.OPERATIONAL for a in town.assets},
            reserve_left_h={**backup},
        )

    @staticmethod
    def _classify_states(town: Township, state: SharedState) -> None:
        """Record how each asset is running, once the step has settled."""
        for aid in town.assets:
            reserve = max(
                state.backup_remaining_h.get(aid, 0.0),
                state.fuel_remaining_h.get(aid, 0.0),
            )
            state.reserve_left_h[aid] = reserve
            state.operating_state[aid] = classify(
                state.functionality.get(aid, 1.0),
                on_backup=aid in state.on_backup,
                reserve_left_h=reserve,
            )

    def _settled(
        self, state: SharedState, hazard: HazardModel, t: float, town: Township
    ) -> bool:
        """True when the event is genuinely over, not merely quiet.

        Stopping early is only safe if nothing can still change. Full service
        with a road still shut, a reservoir still refilling or a generator still
        burning through its tank is a system mid-recovery, and cutting the run
        there would credit it with a recovery it has not made yet.
        """
        if t < hazard.duration_h:
            return False
        if any(ds is not DamageState.NONE for ds in state.damage.values()):
            return False
        for services in state.zone_service.values():
            for level in services.values():
                if level < 0.999:
                    return False
        if not all(state.road_open.values()):
            return False
        for asset in town.assets.values():
            if asset.kind is AssetKind.TANK:
                if state.tank_level_h.get(asset.id, asset.capacity) < asset.capacity:
                    return False
        if state.on_backup:
            return False
        for aid, asset in town.assets.items():
            if state.backup_remaining_h.get(aid, 0.0) < asset.backup_hours:
                return False
            if state.fuel_remaining_h.get(aid, 0.0) < asset.fuel_hours:
                return False
        return True

    def _frame(
        self,
        town: Township,
        state: SharedState,
        response: ResponseModel,
        t: float,
    ) -> TimelineFrame:
        flood = {
            k: round(v, 3)
            for k, v in list(state.flood_depth.items()) + list(state.road_depth.items())
            if v > 0.01
        }
        func = {
            k: round(v, 3) for k, v in state.functionality.items() if v < 0.999
        }
        damage = {
            k: v.value for k, v in state.damage.items() if v is not DamageState.NONE
        }
        totals = {
            "people_no_power": 0,
            "people_no_water": 0,
            "people_no_comms": 0,
            "people_no_health": 0,
        }
        zones: dict[str, dict[str, float]] = {}
        for z in town.zones:
            sv = state.zone_service[z.id]
            zones[z.id] = {s.value: round(sv[s], 3) for s in Service}
            totals["people_no_power"] += int(z.population * (1.0 - sv[Service.ENERGY]))
            totals["people_no_water"] += int(z.population * (1.0 - sv[Service.WATER]))
            totals["people_no_comms"] += int(z.population * (1.0 - sv[Service.COMMS]))
            totals["people_no_health"] += int(z.population * (1.0 - sv[Service.HEALTH]))
        states = {
            k: v.value
            for k, v in state.operating_state.items()
            if v is not OperatingState.OPERATIONAL
        }
        return TimelineFrame(
            t=t,
            flood=flood,
            func=func,
            damage=damage,
            state=states,
            reason={k: v for k, v in state.reason.items() if k in func or k in damage},
            closed_roads=[e for e, open_ in state.road_open.items() if not open_],
            crews=response.snapshot(),
            zones=zones,
            totals=totals,
        )


_WORKER: dict[str, Engine] = {}


def _worker_init(township_dict: dict, cfg_dict: dict) -> None:
    """Build one Engine per worker process, once."""
    _WORKER["engine"] = Engine(
        Township.from_dict(township_dict), Config.from_dict(cfg_dict)
    )


def _worker_run(item: tuple[HazardScenario, Overlay]) -> SimResult:
    scenario, overlay = item
    return _WORKER["engine"].simulate(scenario, overlay)


def _worker_result(item: tuple[HazardScenario, Overlay]) -> SimResult:
    scenario, overlay = item
    return _WORKER["engine"].simulate(scenario, overlay, _skip_amplification=True)


def _objective_scorer(
    objective: object | None, cfg: Config
) -> "Callable[[SimResult], float]":
    """How to reduce one simulation to the single number being minimised."""
    if objective is None:
        return lambda result: result.weighted_loss_ph

    from civictwin.analysis.objectives import UNRECOVERED_H, _weight

    def score(result: SimResult) -> float:
        total = sum(
            _weight(objective, service) * value  # type: ignore[arg-type]
            for service, value in result.loss_by_service_ph.items()
        )
        multiplier = getattr(objective, "vulnerable_multiplier", 0.0)
        if multiplier:
            total += multiplier * result.vulnerable_loss_ph
        recovery_weight = getattr(objective, "recovery_weight", 0.0)
        if recovery_weight:
            slowest = max(result.recovery_90_h.values(), default=0.0)
            total += recovery_weight * (
                UNRECOVERED_H if not math.isfinite(slowest) else slowest
            )
        return total

    return score
