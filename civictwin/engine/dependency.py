"""Dependency behaviour in time: thresholds, delays, and per-link reserves.

A dependency is more than an edge. A pump needs 60% of its rated supply, not
any supply at all; it keeps running for half an hour on inertia and a local
reserve before the loss reaches its output. This module holds that behaviour in
one place so every layer gates its inputs the same way.

The defaults on `Link` (threshold 0, no delay, no reserve) make
`availability()` collapse to "whatever the provider is delivering", which is
what a plain graph edge means -- so a township that says nothing about
dependency behaviour behaves exactly as it did before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from civictwin.ontology import Link


@dataclass(slots=True)
class LinkState:
    """Per-link simulation state. Lives in `SharedState`, not on the Township."""

    short_since_h: float | None = None
    reserve_left_h: float = 0.0
    reserve_primed: bool = False
    #: Set on every step this link actually draws on its reserve, including the
    #: step that empties it -- that step was still carried by the reserve.
    drawing: bool = False


@dataclass(slots=True)
class DependencyGate:
    """Applies each link's threshold, delay and reserve to what a provider offers."""

    states: dict[str, LinkState] = field(default_factory=dict)

    def reset(self, links: list[Link]) -> None:
        self.states = {
            link.key: LinkState(reserve_left_h=link.backup_capacity_h) for link in links
        }

    def availability(
        self, link: Link, supplied: float, t: float, dt: float
    ) -> float:
        """What the dependent actually receives through this link, in [0, 1].

        Args:
            link: the dependency.
            supplied: what the provider is delivering right now, in [0, 1].
            t: current time in hours.
            dt: step length in hours.
        Returns:
            The availability the dependent sees. Full supply while the provider
            meets the threshold; full supply during the delay and while the
            link's own reserve lasts; zero once both are spent.
        """
        state = self.states.get(link.key)
        if state is None:
            state = LinkState(reserve_left_h=link.backup_capacity_h)
            self.states[link.key] = state

        state.drawing = False
        meets_threshold = supplied > 0.0 and supplied >= link.threshold
        if meets_threshold:
            # Recovered: the delay clock resets, and the reserve refills only if
            # it was never drawn on. A spent reserve stays spent for the event.
            state.short_since_h = None
            if not state.reserve_primed:
                state.reserve_left_h = link.backup_capacity_h
            return supplied

        if state.short_since_h is None:
            state.short_since_h = t
        if t - state.short_since_h < link.delay_h:
            return 1.0

        if state.reserve_left_h > 0.0:
            state.reserve_primed = True
            state.drawing = True
            state.reserve_left_h = max(0.0, state.reserve_left_h - dt)
            return 1.0
        return 0.0

    def reserve_in_use(self, link: Link) -> bool:
        """True when this link is living off its own reserve on this step.

        Including the step that empties it: an asset carried through the last
        half-hour of its header tank was on backup for that half-hour, and
        reporting it as already failed would lose the state entirely for any
        reserve shorter than one step.
        """
        state = self.states.get(link.key)
        return bool(state and state.drawing)
