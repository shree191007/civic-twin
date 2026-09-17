"""Thin graph helpers. Uses igraph or networkx when present, else pure Python."""
from __future__ import annotations

from collections import deque
from typing import Iterable, Mapping, Sequence

try:  # pragma: no cover - optional acceleration
    import igraph  # type: ignore

    _BACKEND = "igraph"
except ImportError:  # pragma: no cover
    try:
        import networkx  # type: ignore

        _BACKEND = "networkx"
    except ImportError:
        _BACKEND = "python"

BACKEND = _BACKEND


def reachable(
    adj: Mapping[str, Sequence[str]], sources: Iterable[str], alive: set[str]
) -> set[str]:
    """Forward-reachable set from `sources`, traversing only `alive` nodes."""
    out: set[str] = set()
    q = deque(s for s in sources if s in alive)
    out.update(q)
    while q:
        n = q.popleft()
        for nb in adj.get(n, ()):
            if nb in alive and nb not in out:
                out.add(nb)
                q.append(nb)
    return out


def topological_order(adj: Mapping[str, Sequence[str]], nodes: Iterable[str]) -> list[str]:
    """Kahn ordering; nodes in cycles are appended at the end in a stable order."""
    nodes = list(nodes)
    indeg: dict[str, int] = {n: 0 for n in nodes}
    for n in nodes:
        for m in adj.get(n, ()):
            if m in indeg:
                indeg[m] += 1
    q = deque(sorted(n for n in nodes if indeg[n] == 0))
    out: list[str] = []
    while q:
        n = q.popleft()
        out.append(n)
        for m in adj.get(n, ()):
            if m in indeg:
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
    if len(out) < len(nodes):
        seen = set(out)
        out.extend(n for n in nodes if n not in seen)
    return out
