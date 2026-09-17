"""Flood hazard: scenario sampling, spatial field, fluvial and pluvial depth."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from gotham.config import Config
from gotham.engine.contract import HazardScenario, SharedState
from gotham.ontology import AssetKind, Township

RAIN_CLIP_MM = 600.0
PEAK_METRES_PER_W = 3.0
BASIN_RADIUS_M = 1500.0
BASIN_FACTOR_IN = 1.0
BASIN_FACTOR_OUT = 0.35
DRAIN_FLOOR = 0.4
STORM_INTENSITY_DIVISOR = 2.0
#: Hours over which ponded water drains away once the rain has stopped.
PLUVIAL_DRAIN_H = 6.0


def _derived_uniform(seed: int, key: str) -> float:
    """A stable U(0,1) from (seed, key); independent of any other draw."""
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def gumbel_cdf(x: float, loc: float, scale: float) -> float:
    return math.exp(-math.exp(-(x - loc) / scale))


def sample_scenarios(
    township: Township, cfg: Config, n: int, seed: int
) -> list[HazardScenario]:
    """Draw `n` independent storm years.

    Per-asset draws come from a hash of (seed, scenario index, asset id), so
    adding an asset later never perturbs the draws of existing assets.
    """
    rng = np.random.default_rng(seed)
    asset_ids = sorted(township.assets)
    out: list[HazardScenario] = []
    for i in range(n):
        rain = float(
            np.clip(
                rng.gumbel(cfg.hazard.gumbel_loc_mm, cfg.hazard.gumbel_scale_mm),
                0.0,
                RAIN_CLIP_MM,
            )
        )
        field_seed = int(rng.integers(0, 2**31))
        onset = int(rng.integers(0, 24))
        draws = {
            aid: _derived_uniform(seed, f"{i}:{aid}") for aid in asset_ids
        }
        cdf = gumbel_cdf(rain, cfg.hazard.gumbel_loc_mm, cfg.hazard.gumbel_scale_mm)
        rp = math.inf if cdf >= 1.0 else 1.0 / max(1e-12, 1.0 - cdf)
        out.append(
            HazardScenario(
                id=f"s{seed}-{i:05d}",
                seed=seed * 1_000_003 + i,
                rain_mm=rain,
                field_seed=field_seed,
                onset_hour=onset,
                asset_draws=draws,
                return_period_y=rp,
            )
        )
    return out


@dataclass(slots=True)
class _Bumps:
    cx: np.ndarray
    cy: np.ndarray
    amp: np.ndarray
    sigma: float


def _bumps(field_seed: int, cfg: Config, extent_m: float) -> _Bumps:
    rng = np.random.default_rng(field_seed)
    k = cfg.hazard.field_bumps
    return _Bumps(
        cx=rng.uniform(0.0, extent_m, size=k),
        cy=rng.uniform(0.0, extent_m, size=k),
        amp=rng.uniform(-cfg.hazard.field_amplitude, cfg.hazard.field_amplitude, size=k),
        sigma=cfg.hazard.field_sigma_frac * extent_m,
    )


def spatial_field(
    x: np.ndarray | float,
    y: np.ndarray | float,
    field_seed: int,
    cfg: Config,
    extent_m: float,
) -> np.ndarray:
    """Multiplicative spatial variation of flood severity, clipped to [0.3, 1.8]."""
    b = _bumps(field_seed, cfg, extent_m)
    xa = np.atleast_1d(np.asarray(x, dtype=float))
    ya = np.atleast_1d(np.asarray(y, dtype=float))
    total = np.ones_like(xa)
    two_sigma_sq = 2.0 * b.sigma**2
    for cx, cy, amp in zip(b.cx, b.cy, b.amp):
        total = total + amp * np.exp(
            -(((xa - cx) ** 2) + ((ya - cy) ** 2)) / two_sigma_sq
        )
    return np.clip(total, 0.3, 1.8)


class HazardModel:
    """Flood depth at every asset and road edge over the event horizon."""

    def __init__(
        self,
        township: Township,
        scenario: HazardScenario,
        cfg: Config,
        flood_enabled: bool = True,
    ) -> None:
        self.township = township
        self.scenario = scenario
        self.cfg = cfg
        self.enabled = flood_enabled

        self.w = max(
            0.0,
            (scenario.rain_mm - cfg.hazard.rain_threshold_mm) / cfg.hazard.rain_scale_mm,
        )
        if not flood_enabled:
            self.w = 0.0
        self.duration_h = (
            cfg.hazard.rise_hours
            + cfg.hazard.recession_base_h
            + cfg.hazard.recession_per_w_h * self.w
        )

        self._asset_ids = list(township.assets)
        self._edge_ids = list(township.roads)

        ax = np.array([township.assets[a].x for a in self._asset_ids])
        ay = np.array([township.assets[a].y for a in self._asset_ids])
        a_hand = np.array([township.assets[a].hand_m for a in self._asset_ids])
        ex, ey, e_hand = [], [], []
        for eid in self._edge_ids:
            e = township.roads[eid]
            ux, uy = township.nodes[e.u]
            vx, vy = township.nodes[e.v]
            ex.append((ux + vx) / 2.0)
            ey.append((uy + vy) / 2.0)
            e_hand.append(e.hand_m)
        ex_a, ey_a = np.array(ex), np.array(ey)
        e_hand_a = np.array(e_hand)

        field_a = spatial_field(ax, ay, scenario.field_seed, cfg, township.extent_m)
        field_e = spatial_field(ex_a, ey_a, scenario.field_seed, cfg, township.extent_m)
        self._asset_peak = self.w * field_a * PEAK_METRES_PER_W
        self._edge_peak = self.w * field_e * PEAK_METRES_PER_W
        self._asset_hand = a_hand
        self._edge_hand = e_hand_a

        self._basins = [
            a
            for a in township.assets.values()
            if a.kind is AssetKind.STORMWATER_PUMP
        ]
        self._asset_basin = self._basin_membership(ax, ay)
        self._edge_basin = self._basin_membership(ex_a, ey_a)
        self._pump_ids = [p.id for p in self._basins]
        self._asset_basin_mask = self._basin_mask(self._asset_basin)
        self._edge_basin_mask = self._basin_mask(self._edge_basin)
        self._asset_in_basin = self._asset_basin_mask.any(axis=1)
        self._edge_in_basin = self._edge_basin_mask.any(axis=1)

        self._rain_intensity_mm_h = scenario.rain_mm / (
            cfg.hazard.rise_hours * STORM_INTENSITY_DIVISOR
        )
        self._asset_depth = np.zeros(len(self._asset_ids))
        self._edge_depth = np.zeros(len(self._edge_ids))

    def _basin_mask(self, membership: list[list[str]]) -> np.ndarray:
        """Boolean (points x pumps) membership matrix for vectorised drainage."""
        mask = np.zeros((len(membership), max(1, len(self._pump_ids))), dtype=bool)
        for i, members in enumerate(membership):
            for m in members:
                mask[i, self._pump_ids.index(m)] = True
        return mask

    def _basin_membership(self, x: np.ndarray, y: np.ndarray) -> list[list[str]]:
        """Which stormwater pumps' basins contain each point."""
        out: list[list[str]] = []
        for xi, yi in zip(x, y):
            members = [
                p.id
                for p in self._basins
                if math.hypot(p.x - xi, p.y - yi) <= BASIN_RADIUS_M
            ]
            out.append(members)
        return out

    def _pluvial_decay(self, t: float) -> float:
        """How much of the ponded water is still standing at time `t`.

        Ponding does not vanish the moment the rain stops: it drains away over
        a few hours. Cutting it to zero at the end of the storm put a step
        change in the depth series and let assets dry out instantly.
        """
        storm_end = self.cfg.hazard.rise_hours * STORM_INTENSITY_DIVISOR
        if t <= storm_end:
            return 1.0
        if t >= storm_end + PLUVIAL_DRAIN_H:
            return 0.0
        return 1.0 - (t - storm_end) / PLUVIAL_DRAIN_H

    def profile(self, t: float) -> float:
        """Hydrograph shape: linear rise to 1.0, then linear recession to 0."""
        rise = self.cfg.hazard.rise_hours
        if t <= 0.0:
            return 0.0
        if t < rise:
            return t / rise
        if t >= self.duration_h:
            return 0.0
        return max(0.0, 1.0 - (t - rise) / max(1e-9, self.duration_h - rise))

    def _pluvial(
        self,
        t: float,
        mask: np.ndarray,
        in_basin: np.ndarray,
        drain_factor: dict[str, float],
    ) -> np.ndarray:
        cfgh = self.cfg.hazard
        n = mask.shape[0]
        if not self.enabled:
            return np.zeros(n)
        decay = self._pluvial_decay(t)
        if decay <= 0.0:
            return np.zeros(n)
        f = np.array(
            [drain_factor.get(pid, 1.0) for pid in self._pump_ids], dtype=float
        )
        counts = mask.sum(axis=1)
        sums = mask @ f if len(f) else np.zeros(n)
        mean_f = np.where(counts > 0, sums / np.maximum(counts, 1), 1.0)
        basin_factor = np.where(in_basin, BASIN_FACTOR_IN, BASIN_FACTOR_OUT)
        capacity = cfgh.pluvial_drain_capacity_mm_h * (
            DRAIN_FLOOR + (1.0 - DRAIN_FLOOR) * mean_f
        )
        excess = np.maximum(0.0, self._rain_intensity_mm_h - capacity)
        return excess * cfgh.pluvial_pond_factor * basin_factor * decay

    def update(self, state: SharedState, t: float) -> None:
        """Write the current flood depth for every asset and road edge."""
        if not self.enabled:
            state.flood_depth.update(dict.fromkeys(self._asset_ids, 0.0))
            state.road_depth.update(dict.fromkeys(self._edge_ids, 0.0))
            return

        p = self.profile(t)
        fluvial_a = np.maximum(0.0, self._asset_peak * p - self._asset_hand)
        fluvial_e = np.maximum(0.0, self._edge_peak * p - self._edge_hand)
        pluvial_a = self._pluvial(
            t, self._asset_basin_mask, self._asset_in_basin, state.drain_factor
        )
        pluvial_e = self._pluvial(
            t, self._edge_basin_mask, self._edge_in_basin, state.drain_factor
        )
        np.maximum(fluvial_a, pluvial_a, out=self._asset_depth)
        np.maximum(fluvial_e, pluvial_e, out=self._edge_depth)
        state.flood_depth.update(zip(self._asset_ids, self._asset_depth.tolist()))
        state.road_depth.update(zip(self._edge_ids, self._edge_depth.tolist()))

    def max_depth(self, asset_id: str, drain_factor: float = 1.0) -> float:
        """Highest depth reached at an asset over the whole horizon.

        Includes ponding: an asset in a basin whose stormwater pumps are out can
        be under water without the river ever reaching it, and a fluvial-only
        figure would report it as dry.
        """
        i = self._asset_ids.index(asset_id)
        fluvial = max(0.0, self._asset_peak[i] - self._asset_hand[i])
        pluvial = 0.0
        if self.enabled and self._asset_basin_mask.shape[0] > i:
            members = self._asset_basin[i]
            mean_f = (
                sum(drain_factor for _ in members) / len(members) if members else 1.0
            )
            cfgh = self.cfg.hazard
            capacity = cfgh.pluvial_drain_capacity_mm_h * (
                DRAIN_FLOOR + (1.0 - DRAIN_FLOOR) * mean_f
            )
            excess = max(0.0, self._rain_intensity_mm_h - capacity)
            basin_factor = BASIN_FACTOR_IN if members else BASIN_FACTOR_OUT
            pluvial = excess * cfgh.pluvial_pond_factor * basin_factor
        return float(max(fluvial, pluvial))

    def depth_series(self, asset_id: str, times: np.ndarray) -> np.ndarray:
        """Fluvial depth at an asset for each requested time."""
        i = self._asset_ids.index(asset_id)
        prof = np.array([self.profile(float(t)) for t in np.atleast_1d(times)])
        return np.maximum(0.0, self._asset_peak[i] * prof - self._asset_hand[i])
