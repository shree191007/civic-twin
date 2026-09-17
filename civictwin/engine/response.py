"""Repair crews, fuel logistics, and manual operation after SCADA loss."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from civictwin.config import Config
from civictwin.engine.contract import SharedState
from civictwin.engine.damage import DamageModel
from civictwin.engine.layers.transport import TransportLayer
from civictwin.ontology import (
    AssetKind,
    DamageState,
    LinkKind,
    Portfolio,
    Township,
)

LOW_FUEL_THRESHOLD_H = 8.0
COLOCATION_EXTRA_FRACTION = 0.3
#: Dispatch policies. "greedy_population" is the default from spec 02 section
#: 11.1; "nearest" is the naive comparison; a priority list overrides both.
POLICY_GREEDY = "greedy_population"
POLICY_NEAREST = "nearest"


@dataclass(slots=True)
class CrewState:
    id: str
    portfolio: Portfolio
    home_node: int
    node: int
    task: str = "idle"
    target: str | None = None
    eta_h: float = 0.0
    finish_h: float = 0.0
    speed_kph: float = 25.0


class ResponseModel:
    """Dispatches crews to damaged, dry, reachable assets and refuels generators."""

    def __init__(
        self,
        township: Township,
        damage: DamageModel,
        transport: TransportLayer,
        cfg: Config,
        policy: str = POLICY_GREEDY,
        priority: Sequence[str] | None = None,
    ) -> None:
        self.policy = policy
        self.priority = {aid: i for i, aid in enumerate(priority or ())}
        self.dispatch_log: list[tuple[str, str]] = []
        self.township = township
        self.damage = damage
        self.transport = transport
        self.cfg = cfg
        self.crews = [
            CrewState(
                id=c.id,
                portfolio=c.portfolio,
                home_node=township.assets[c.depot].node,
                node=township.assets[c.depot].node,
                speed_kph=c.speed_kph,
            )
            for c in township.crews
        ]
        self._fuel_nodes = [
            a for a in township.assets.values() if a.kind is AssetKind.FUEL_STATION
        ]
        self._hosted_by_host: dict[str, list[str]] = {}
        for ln in township.links:
            if ln.kind is LinkKind.HOSTED_ON:
                self._hosted_by_host.setdefault(ln.source, []).append(ln.target)
        self._served = {aid: township.served_population(aid) for aid in township.assets}
        self._depot_nodes = sorted(
            {
                a.node
                for a in township.assets.values()
                if a.kind is AssetKind.DEPOT
            }
        )
        self._fuel_eta: dict[str, float] = {}
        self._claimed: set[str] = set()

    def step(self, state: SharedState, cfg: Config) -> None:
        t = state.t
        self._advance_crews(state, t)
        self._dispatch(state, t)
        self._fuel(state, t, cfg)
        self._manual(state, t, cfg)

    # ----------------------------------------------------------------- crews

    def _advance_crews(self, state: SharedState, t: float) -> None:
        for c in self.crews:
            if c.task.startswith("enroute:"):
                if t >= c.eta_h:
                    aid = c.target
                    assert aid is not None
                    c.node = self.township.assets[aid].node
                    repair = self._repair_hours_with_colocation(aid, state)
                    c.task = f"repairing:{aid}"
                    c.finish_h = t + repair
                elif not math.isfinite(
                    self.transport.travel_time(c.node, self.township.assets[c.target].node)  # type: ignore[arg-type]
                ):
                    self._release(c)
            elif c.task.startswith("repairing:"):
                if t >= c.finish_h:
                    aid = c.target
                    assert aid is not None
                    state.damage[aid] = DamageState.NONE
                    state.reason.pop(aid, None)
                    state.manual_until_h.pop(aid, None)
                    # Rebuilding the bridge restores the fibre it was carrying,
                    # but only the damage the bridge caused. A fibre that was
                    # also cut on its own account stays cut.
                    for hosted in self._hosted_by_host.get(aid, ()):
                        if state.damage.get(hosted) in (None, DamageState.NONE):
                            continue
                        if state.reason.get(hosted) == f"upstream:{aid}":
                            state.damage[hosted] = DamageState.NONE
                            state.reason.pop(hosted, None)
                    c.node = self.township.assets[aid].node
                    self._release(c)

    def _repair_hours_with_colocation(
        self, asset_id: str, state: SharedState
    ) -> float:
        """Repair time, including the hosted assets the crew fixes while there.

        A crew rebuilding a bridge also re-hangs the fibre on it. That is
        cheaper than a separate visit but not free, so each hosted asset adds a
        fraction of its own repair time rather than none.
        """
        hours = self.damage.repair_hours(asset_id, state.damage[asset_id])
        host = self.township.assets[asset_id]
        for hosted in self._hosted_by_host.get(asset_id, ()):
            if state.damage.get(hosted) in (None, DamageState.NONE):
                continue
            if self.township.assets[hosted].node != host.node:
                continue
            hours += COLOCATION_EXTRA_FRACTION * self.damage.repair_hours(
                hosted, state.damage[hosted]
            )
        return hours

    def _release(self, c: CrewState) -> None:
        if c.target is not None:
            self._claimed.discard(c.target)
        c.task = "idle"
        c.target = None
        c.eta_h = 0.0
        c.finish_h = 0.0

    def _dispatch(self, state: SharedState, t: float) -> None:
        cfg = self.cfg
        for c in self.crews:
            if c.task != "idle":
                continue
            best_score = -1.0
            best_aid: str | None = None
            best_tt = 0.0
            for aid, asset in self.township.assets.items():
                if aid in self._claimed:
                    continue
                if state.damage[aid] is DamageState.NONE:
                    continue
                if asset.portfolio is not c.portfolio:
                    continue
                if state.flood_depth.get(aid, 0.0) >= cfg.response.site_dry_depth_m:
                    continue
                tt = self.transport.travel_time(c.node, asset.node)
                if not math.isfinite(tt):
                    continue
                repair = self.damage.repair_hours(aid, state.damage[aid])
                denom = tt + repair
                if self.priority:
                    rank = self.priority.get(aid)
                    score = 0.0 if rank is None else float(len(self.priority) - rank)
                elif self.policy == POLICY_NEAREST:
                    score = 1.0 / (tt + 1e-6)
                else:
                    score = self._served[aid] / denom if denom > 0 else 0.0
                if score > best_score or (
                    score == best_score and best_aid is not None and aid < best_aid
                ):
                    best_score, best_aid, best_tt = score, aid, tt
            if best_aid is not None:
                c.task = f"enroute:{best_aid}"
                c.target = best_aid
                c.eta_h = t + best_tt
                self._claimed.add(best_aid)
                self.dispatch_log.append((c.id, best_aid))

    # ------------------------------------------------------------------ fuel

    def _fuel(self, state: SharedState, t: float, cfg: Config) -> None:
        for aid, asset in self.township.assets.items():
            if asset.fuel_hours <= 0.0:
                continue
            remaining = state.fuel_remaining_h.get(aid, 0.0)
            eta = self._fuel_eta.get(aid)
            if eta is not None:
                # The truck is only any use if the road is still there when it
                # arrives, and it cannot put more in the tank than it holds.
                if not self._fuel_route_open(asset, state):
                    del self._fuel_eta[aid]
                    continue
                if t >= eta:
                    state.fuel_remaining_h[aid] = min(
                        asset.fuel_hours,
                        remaining + cfg.response.refuel_adds_hours,
                    )
                    del self._fuel_eta[aid]
                continue
            if remaining >= LOW_FUEL_THRESHOLD_H:
                continue
            best = math.inf
            for station in self._fuel_nodes:
                if state.damage[station.id] is DamageState.COMPLETE:
                    continue
                tt = self.transport.travel_time(station.node, asset.node)
                if not math.isfinite(tt):
                    continue
                speed_ratio = cfg.response.crew_travel_speed_kph / max(
                    1e-6, cfg.response.fuel_truck_speed_kph
                )
                best = min(best, tt * speed_ratio)
            if math.isfinite(best):
                self._fuel_eta[aid] = t + best

    def _fuel_route_open(self, asset: object, state: SharedState) -> bool:
        """Whether any surviving fuel station can still reach this asset."""
        for station in self._fuel_nodes:
            if state.damage[station.id] is DamageState.COMPLETE:
                continue
            if math.isfinite(
                self.transport.travel_time(station.node, asset.node)  # type: ignore[attr-defined]
            ):
                return True
        return False

    # -------------------------------------------------------- manual operation

    def _manual(self, state: SharedState, t: float, cfg: Config) -> None:
        """Schedule manual operation of a SCADA asset that has lost telemetry.

        Manual operation means somebody standing at the asset. If no depot can
        reach it -- the road is under water, the bridge is gone -- then nobody
        is going to be standing there, and the asset stays on manual hold rather
        than restarting on schedule.
        """
        for aid, asset in self.township.assets.items():
            if not asset.scada_controlled:
                continue
            if state.comms_available.get(aid, 1.0) > 0.0:
                state.manual_until_h.pop(aid, None)
                continue
            reachable = any(
                math.isfinite(self.transport.travel_time(node, asset.node))
                for node in self._depot_nodes
            )
            if not reachable:
                state.manual_until_h[aid] = math.inf
                state.reason.setdefault(aid, "access")
                continue
            if aid not in state.manual_until_h or math.isinf(
                state.manual_until_h[aid]
            ):
                state.manual_until_h[aid] = (
                    t + cfg.response.manual_operation_penalty_h
                )

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "id": c.id,
                "node": c.node,
                "task": c.task if c.task == "idle" else c.task,
                "eta_h": round(c.eta_h, 2),
            }
            for c in self.crews
        ]
