"""Deterministic geography for the synthetic township: river, terrain, roads."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

RIVER_START = (0.0, 2600.0)
RIVER_END = (6000.0, 3400.0)
RIVER_HALF_WIDTH_M = 60.0
RIVER_JITTER_M = 250.0
HAND_DIVISOR_M = 260.0
HAND_MAX_M = 14.0
RIDGE_X, RIDGE_Y = 1200.0, 4200.0
RIDGE_HEIGHT_M = 3.0
RIDGE_SIGMA_M = 900.0
HAND_NOISE_M = 0.4

GRID_N = 13
GRID_SPACING_M = 500.0


@dataclass(slots=True)
class Terrain:
    """River polyline plus the HAND (height above nearest drainage) field."""

    river_xs: np.ndarray
    river_ys: np.ndarray
    extent_m: float
    _noise: np.ndarray
    _noise_n: int
    #: The western ridge, scaled with the township. Left in nominal 6 km
    #: coordinates it would sit outside a smaller extent and flatten the terrain.
    ridge_x: float = RIDGE_X
    ridge_y: float = RIDGE_Y
    ridge_sigma_m: float = RIDGE_SIGMA_M

    def river_y(self, x: float) -> float:
        """Centreline y of the river at the given x."""
        return float(np.interp(x, self.river_xs, self.river_ys))

    def distance_to_river_m(self, x: float, y: float) -> float:
        """Perpendicular distance to the river polyline (centreline)."""
        best = math.inf
        for i in range(len(self.river_xs) - 1):
            ax, ay = float(self.river_xs[i]), float(self.river_ys[i])
            bx, by = float(self.river_xs[i + 1]), float(self.river_ys[i + 1])
            dx, dy = bx - ax, by - ay
            denom = dx * dx + dy * dy
            t = 0.0 if denom == 0.0 else ((x - ax) * dx + (y - ay) * dy) / denom
            t = min(1.0, max(0.0, t))
            px, py = ax + t * dx, ay + t * dy
            d = math.hypot(x - px, y - py)
            if d < best:
                best = d
        return best

    def hand_m(self, x: float, y: float) -> float:
        """Height above nearest drainage, in metres."""
        d = max(0.0, self.distance_to_river_m(x, y) - RIVER_HALF_WIDTH_M)
        base = min(HAND_MAX_M, d / HAND_DIVISOR_M)
        ridge = RIDGE_HEIGHT_M * math.exp(
            -(((x - self.ridge_x) ** 2) + ((y - self.ridge_y) ** 2))
            / (2.0 * self.ridge_sigma_m**2)
        )
        return max(0.0, base + ridge + self._noise_at(x, y))

    def _noise_at(self, x: float, y: float) -> float:
        """Deterministic ±HAND_NOISE_M terrain roughness, bilinear interpolated."""
        n = self._noise_n
        gx = min(n - 1.001, max(0.0, x / self.extent_m * (n - 1)))
        gy = min(n - 1.001, max(0.0, y / self.extent_m * (n - 1)))
        i0, j0 = int(gx), int(gy)
        fx, fy = gx - i0, gy - j0
        v = (
            self._noise[i0, j0] * (1 - fx) * (1 - fy)
            + self._noise[i0 + 1, j0] * fx * (1 - fy)
            + self._noise[i0, j0 + 1] * (1 - fx) * fy
            + self._noise[i0 + 1, j0 + 1] * fx * fy
        )
        return float(v) * HAND_NOISE_M

    def bank(self, x: float, y: float) -> int:
        """+1 for the near (home/'west') bank, -1 for the far ('east') bank."""
        return 1 if y >= self.river_y(x) else -1


def build_terrain(rng: np.random.Generator, extent_m: float) -> Terrain:
    """River polyline with three jittered control points, plus the HAND field."""
    ts = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    xs = RIVER_START[0] + ts * (RIVER_END[0] - RIVER_START[0])
    ys = RIVER_START[1] + ts * (RIVER_END[1] - RIVER_START[1])
    jitter = rng.uniform(-RIVER_JITTER_M, RIVER_JITTER_M, size=3)
    ys[1:4] = ys[1:4] + jitter
    scale = extent_m / 6000.0
    noise_n = 25
    noise = rng.uniform(-1.0, 1.0, size=(noise_n, noise_n))
    return Terrain(
        river_xs=xs * scale,
        river_ys=ys * scale,
        extent_m=extent_m,
        _noise=noise,
        _noise_n=noise_n,
        ridge_x=RIDGE_X * scale,
        ridge_y=RIDGE_Y * scale,
        ridge_sigma_m=RIDGE_SIGMA_M * scale,
    )


def grid_nodes(extent_m: float) -> dict[int, tuple[float, float]]:
    """13x13 regular node grid covering the extent."""
    spacing = extent_m / (GRID_N - 1)
    return {
        iy * GRID_N + ix: (ix * spacing, iy * spacing)
        for iy in range(GRID_N)
        for ix in range(GRID_N)
    }


def grid_edge_pairs() -> list[tuple[int, int]]:
    """All orthogonal neighbour pairs of the node grid (312 for 13x13)."""
    pairs: list[tuple[int, int]] = []
    for iy in range(GRID_N):
        for ix in range(GRID_N):
            n = iy * GRID_N + ix
            if ix + 1 < GRID_N:
                pairs.append((n, n + 1))
            if iy + 1 < GRID_N:
                pairs.append((n, n + GRID_N))
    return pairs


def nearest_node(nodes: dict[int, tuple[float, float]], x: float, y: float) -> int:
    """Id of the grid node closest to (x, y)."""
    best_id, best_d = -1, math.inf
    for nid, (nx, ny) in nodes.items():
        d = (nx - x) ** 2 + (ny - y) ** 2
        if d < best_d:
            best_d, best_id = d, nid
    return best_id
