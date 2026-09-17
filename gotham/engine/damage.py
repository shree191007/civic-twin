"""Fragility curves, damage-state assignment, hosted-on propagation, repairs."""
from __future__ import annotations

import math

from gotham.config import Config
from gotham.engine.contract import HazardScenario, Overlay, SharedState
from gotham.engine.hazard import _derived_uniform
from gotham.ontology import (
    DAMAGE_ORDER,
    DamageState,
    LinkKind,
    Township,
    damage_index,
)

NOT_DAMAGEABLE_MEDIAN_M = 99.0
REPAIR_SPREAD_LO = 0.7
REPAIR_SPREAD_RANGE = 0.6

_SQRT2 = math.sqrt(2.0)

# Acklam's rational approximation to the inverse standard normal CDF.
_A = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
      1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
      6.680131188771972e01, -1.328068155288572e01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
      -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
      3.754408661907416e00)
_P_LOW = 0.02425


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def _phi_inv(p: float) -> float:
    """Inverse standard normal CDF, accurate to about 1e-9."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q
                + _C[5]) / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    if p > 1.0 - _P_LOW:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q
                 + _C[5]) / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
        ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)


def exceedance_probability(
    depth_m: float, median_m: float, beta: float, state_multiplier: float
) -> float:
    """P(damage >= this state) at the given depth, lognormal fragility."""
    if depth_m <= 0.0 or median_m <= 0.0:
        return 0.0
    return _phi(math.log(depth_m / (median_m * state_multiplier)) / beta)


class DamageModel:
    """Assigns monotonically increasing damage states as water rises."""

    def __init__(
        self,
        township: Township,
        scenario: HazardScenario,
        overlay: Overlay,
        cfg: Config,
    ) -> None:
        self.township = township
        self.scenario = scenario
        self.cfg = cfg
        self.invulnerable = set(overlay.invulnerable)
        self.forced = set(overlay.forced_failures)
        self._draws = scenario.asset_draws
        self._median_override: dict[str, float] = {}
        self.repair_factor = 1.0
        self._thresholds: dict[str, tuple[float, ...]] | None = None
        self._max_depth: dict[str, float] = {}
        self._max_level: dict[str, int] = {}

        # HOSTED_ON parents, ordered so that hosts are resolved before hosted.
        self._hosted_pairs: list[tuple[str, str]] = [
            (ln.source, ln.target)
            for ln in township.links
            if ln.kind is LinkKind.HOSTED_ON
        ]
        self._hosted_order = self._topological_hosted_order()

        self._repair_u = {
            aid: REPAIR_SPREAD_LO
            + REPAIR_SPREAD_RANGE
            * _derived_uniform(scenario.seed, f"{aid}:repair")
            for aid in township.assets
        }

    def set_median_override(self, asset_id: str, median_m: float) -> None:
        """Used by hardening interventions to raise an asset's flood threshold."""
        self._median_override[asset_id] = median_m

    def median_of(self, asset_id: str) -> float:
        if asset_id in self._median_override:
            return self._median_override[asset_id]
        return self.township.assets[asset_id].fragility_median_m

    def _topological_hosted_order(self) -> list[tuple[str, str]]:
        """Order HOSTED_ON links so a host's own damage is settled first."""
        depth: dict[str, int] = {}

        def host_depth(aid: str, guard: frozenset[str] = frozenset()) -> int:
            if aid in depth:
                return depth[aid]
            if aid in guard:
                return 0
            hosts = self.township.providers(aid, {LinkKind.HOSTED_ON})
            d = 0 if not hosts else 1 + max(
                host_depth(h, guard | {aid}) for h in hosts
            )
            depth[aid] = d
            return d

        return sorted(self._hosted_pairs, key=lambda p: host_depth(p[0]))

    def _build_thresholds(self) -> dict[str, tuple[float, ...]]:
        """Depth at which each asset reaches each damage state, given its draw.

        The fragility is lognormal and the scenario draw `u` is fixed, so
        `P_k(d) > u` is equivalent to `d > median * multiplier_k * exp(beta *
        Phi^-1(u))`. Precomputing those four depths turns the per-step damage
        update into plain comparisons.
        """
        mult = self.cfg.damage.state_multipliers
        out: dict[str, tuple[float, ...]] = {}
        for aid, asset in self.township.assets.items():
            median = self.median_of(aid)
            if median >= NOT_DAMAGEABLE_MEDIAN_M:
                continue
            u = self._draws.get(aid, 1.0)
            if u >= 1.0:
                continue
            z = _phi_inv(u)
            if not math.isfinite(z):
                continue
            scale = median * math.exp(asset.fragility_beta * z)
            out[aid] = tuple(scale * m for m in mult)
        return out

    def initialise(self, state: SharedState) -> None:
        """Set every asset to NONE, then apply forced failures at t=0."""
        for aid in self.township.assets:
            state.damage[aid] = DamageState.NONE
        for aid in self.forced:
            if aid in state.damage:
                state.damage[aid] = DamageState.COMPLETE
        self._propagate(state)

    def update(self, state: SharedState, t: float) -> None:
        """Raise damage states to match the current flood depth. Never lowers."""
        if self._thresholds is None:
            self._thresholds = self._build_thresholds()
        flood = state.flood_depth
        damage = state.damage
        seen = self._max_depth
        seen_level = self._max_level
        for aid, thresholds in self._thresholds.items():
            if aid in self.invulnerable or aid in self.forced:
                continue
            depth = flood[aid]
            # Damage is monotone in depth, so falling water can do nothing new --
            # unless a crew has since repaired the asset. Repairing a substation
            # that is still under a metre of water must not leave it working, so
            # the fast path only applies while the recorded damage still matches
            # what the deepest water so far implies.
            if depth <= seen.get(aid, 0.0) and damage_index(
                damage[aid]
            ) >= seen_level.get(aid, 0):
                continue
            level = 0
            for th in thresholds:
                if depth > th:
                    level += 1
                else:
                    break
            if depth > seen.get(aid, 0.0):
                seen[aid] = depth
                seen_level[aid] = level
            if level and damage_index(damage[aid]) < level:
                damage[aid] = DAMAGE_ORDER[level]
                state.reason[aid] = "damaged"
        self._propagate(state)

    def _propagate(self, state: SharedState) -> None:
        """HOSTED_ON: a hosted asset is at least as damaged as its host."""
        for host, hosted in self._hosted_order:
            if hosted in self.invulnerable:
                continue
            if damage_index(state.damage[host]) > damage_index(state.damage[hosted]):
                state.damage[hosted] = state.damage[host]
                state.reason[hosted] = f"upstream:{host}"

    def repair_hours(self, asset_id: str, state: DamageState) -> float:
        """Deterministic repair duration for an asset at a damage state."""
        base = self.township.assets[asset_id].repair_hours_base
        mult = self.cfg.damage.repair_multipliers[damage_index(state)]
        return base * mult * self._repair_u[asset_id] * self.repair_factor


def road_speed_factor(depth_m: float, cfg: Config) -> float:
    """Fraction of free-flow speed available at the given standing-water depth."""
    if depth_m <= 0.05:
        return 1.0
    if depth_m >= cfg.road.impassable_depth_m:
        return 0.0
    return float(
        min(1.0, max(0.15, 1.0 - depth_m / cfg.road.impassable_depth_m))
    )
