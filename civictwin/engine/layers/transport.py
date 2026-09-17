"""Transport layer: road passability, travel times, and zone mobility."""
from __future__ import annotations

import heapq
import math

from civictwin.config import Config
from civictwin.engine.contract import SharedState
from civictwin.engine.damage import road_speed_factor
from civictwin.ontology import (
    AssetKind,
    DamageState,
    Portfolio,
    Service,
    Township,
    damage_index,
)

CLOSED_HOST_DAMAGE = damage_index(DamageState.EXTENSIVE)
#: Shortest paths are cached against a per-edge bucket of the speed factor, so
#: the cache survives the small hour-to-hour changes of a rising hydrograph.
#: Graph weights themselves always use the exact factor.
SPEED_QUANTUM = 0.34


#: Mean absolute drift in speed factors that forces a rebuild even when every
#: edge is still in the same bucket.
MAX_WEIGHT_DRIFT = 0.08


def _speed_bucket(factor: float) -> int:
    """Cache bucket for a speed factor; 0 means closed."""
    return 0 if factor <= 0.0 else 1 + int(factor / SPEED_QUANTUM)


class TransportLayer:
    """Road state plus shortest paths, cached per closed/degraded-edge signature."""

    portfolio = Portfolio.TRANSPORT

    def __init__(self) -> None:
        self.township: Township | None = None

    def reset(self, township: Township, cfg: Config, rng: object = None) -> None:
        self.township = township
        self._edges = list(township.roads.values())
        self._n = max(township.nodes) + 1
        self._zone_nodes = {z.id: z.node for z in township.zones}
        self._zone_pop = {z.id: float(z.population) for z in township.zones}
        self._zone_node_list = [z.node for z in township.zones]
        self._zone_pop_list = [float(z.population) for z in township.zones]
        self._budget_h = cfg.sim.mobility_access_minutes / 60.0
        self._signature: tuple[int, ...] | None = None
        self._weight_factors: list[float] = [1.0] * len(self._edges)
        self._dist_cache: dict[int, list[float]] = {}
        self._bounded_cache: dict[int, list[float]] = {}
        self._reach_cache: dict[int, float] = {}
        # zone mobility and health access both fall inside this horizon
        self._access_bound_h = max(
            cfg.sim.mobility_access_minutes, cfg.sim.health_access_minutes
        ) / 60.0
        # CSR-style adjacency rebuilt on each signature change
        self._adj: list[list[tuple[int, float]]] = [[] for _ in range(self._n)]
        self._build_weights([1.0] * len(self._edges))
        self._baseline_reach = {
            zid: self._reachable_population(node)
            for zid, node in self._zone_nodes.items()
        }

    def _build_weights(self, factors: list[float]) -> None:
        self._weight_factors = list(factors)
        adj: list[list[tuple[int, float]]] = [[] for _ in range(self._n)]
        for e, fac in zip(self._edges, factors):
            if fac <= 0.0:
                continue
            tt = (e.length_m / 1000.0) / (e.free_flow_kph * fac)
            adj[e.u].append((e.v, tt))
            adj[e.v].append((e.u, tt))
        self._adj = adj
        self._dist_cache = {}
        self._bounded_cache = {}
        self._reach_cache = {}
        self._components: list[int] | None = None

    def distances_from(self, src: int, bound: float | None = None) -> list[float]:
        """Dijkstra from a node, cached per signature.

        Args:
            src: source node id.
            bound: stop expanding past this travel time (hours). Entries beyond
                the bound come back as `inf`, which is what every caller that
                passes a bound is testing for anyway.
        """
        cache = self._dist_cache if bound is None else self._bounded_cache
        cached = cache.get(src)
        if cached is not None:
            return cached
        inf = math.inf
        limit = inf if bound is None else bound
        dist = [inf] * self._n
        dist[src] = 0.0
        adj = self._adj
        pq: list[tuple[float, int]] = [(0.0, src)]
        push, pop = heapq.heappush, heapq.heappop
        while pq:
            d, n = pop(pq)
            if d > dist[n] or d > limit:
                continue
            for nb, tt in adj[n]:
                nd = d + tt
                if nd < dist[nb] and nd <= limit:
                    dist[nb] = nd
                    push(pq, (nd, nb))
        cache[src] = dist
        return dist

    def access_time(self, src: int, dst: int) -> float:
        """Travel time capped at the access horizon; `inf` beyond it."""
        return self.distances_from(src, self._access_bound_h)[dst]

    def travel_time(self, src: int, dst: int) -> float:
        return self.distances_from(src)[dst]

    def component_of(self, node: int) -> int:
        """Connected-component label of a node under the current network."""
        if self._components is None:
            labels = [-1] * self._n
            label = 0
            for start in range(self._n):
                if labels[start] != -1:
                    continue
                if not self._adj[start]:
                    # A node with no open edge is its own component, not a
                    # member of the catch-all -1 group -- otherwise two cut-off
                    # assets would look like neighbours, and an asset would not
                    # even be in the same component as itself.
                    labels[start] = label
                    label += 1
                    continue
                stack = [start]
                labels[start] = label
                while stack:
                    n = stack.pop()
                    for nb, _tt in self._adj[n]:
                        if labels[nb] == -1:
                            labels[nb] = label
                            stack.append(nb)
                label += 1
            self._components = labels
        return self._components[node]

    def same_component(self, a: int, b: int) -> bool:
        if a == b:
            return True
        ca = self.component_of(a)
        return ca != -1 and ca == self.component_of(b)

    def _reachable_population(self, node: int) -> float:
        cached = self._reach_cache.get(node)
        if cached is not None:
            return cached
        dist = self.distances_from(node, self._access_bound_h)
        budget = self._budget_h
        total = 0.0
        for zn, pop in zip(self._zone_node_list, self._zone_pop_list):
            if dist[zn] <= budget:
                total += pop
        self._reach_cache[node] = total
        return total

    def step(self, state: SharedState, cfg: Config) -> None:
        t = self.township
        assert t is not None
        factors: list[float] = []
        sig: list[int] = []
        road_open = state.road_open
        road_depth = state.road_depth
        damage = state.damage
        for e in self._edges:
            fac = road_speed_factor(road_depth[e.id], cfg)
            if e.host_asset is not None and damage_index(damage[e.host_asset]) >= CLOSED_HOST_DAMAGE:
                fac = 0.0
            road_open[e.id] = fac > 0.0
            factors.append(fac)
            sig.append(_speed_bucket(fac))

        signature = tuple(sig)
        # The bucket keeps the path cache alive across small changes, but the
        # weights behind it are the exact factors from whenever it was last
        # built. Once those have drifted far enough the cached paths are stale
        # even though the bucket has not moved, so rebuild on drift too.
        drift = sum(
            abs(f - w) for f, w in zip(factors, self._weight_factors)
        ) / max(1, len(factors))
        if signature != self._signature or drift > MAX_WEIGHT_DRIFT:
            self._signature = signature
            self._build_weights(factors)

        for zid, node in self._zone_nodes.items():
            base = self._baseline_reach[zid]
            reach = self._reachable_population(node)
            state.zone_service[zid][Service.MOBILITY] = (
                1.0 if base <= 0.0 else min(1.0, reach / base)
            )

    def begin(self) -> None:
        """Clear per-simulation caches; the no-flood baseline is kept."""
        self._signature = None
        self._build_weights([1.0] * len(self._edges))
