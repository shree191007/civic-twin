"""Process-wide application state: township, engine, and the results cache."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from gotham.analysis.interventions import generate_catalogue
from gotham.api.geo import Projector, build_township_payload, projector_for
from gotham.config import DEFAULT, Config
from gotham.engine.coordinator import Engine
from gotham.io import data_version_of, load_township, model_version
from gotham.ontology import Township

logger = logging.getLogger(__name__)

#: results/*.json files the API serves. Those marked required fail startup.
RESULT_FILES: dict[str, bool] = {
    "meta": True,
    "risk": True,
    "criticality": False,
    "spofs": True,
    "critical_sets": False,
    "frontier": True,
    "baselines": True,
    "ensemble": False,
    "voi": False,
    "sensitivity": False,
    "restoration": False,
    "redundancy": False,
}

SCENARIO_CACHE_SIZE = 256
#: Timelines dominate a cached scenario: 73 frames of per-asset state is
#: megabytes, and 256 of them would be gigabytes of heap. Anything above this
#: is spilled to a temporary file and read back on a hit.
CACHE_INLINE_BYTES = 64_000
#: Spilling bounds the heap but makes every hit pay for a JSON parse. The most
#: recently used payloads are therefore also kept decoded in memory: replaying
#: the same scenario -- which is exactly what dragging a slider does -- stays
#: fast, while the total held in RAM stays bounded by this count.
HOT_CACHE_SIZE = 8
PUBLIC_ROLE = "public"
PLANNER_ROLE = "planner"


class AppState:
    """Everything the API needs, loaded once at startup."""

    def __init__(
        self,
        township_path: Path | None = None,
        results_path: Path | None = None,
        cfg: Config = DEFAULT,
        role: str | None = None,
    ) -> None:
        self.township_path = Path(
            township_path or os.environ.get("GOTHAM_TOWNSHIP", "data/township.json")
        )
        self.results_path = Path(
            results_path or os.environ.get("GOTHAM_RESULTS", "results/")
        )
        self.role = role or os.environ.get("GOTHAM_ROLE", PLANNER_ROLE)
        self.cfg = cfg

        self.township: Township = load_township(self.township_path)
        self.engine = Engine(self.township, cfg)
        self.projector: Projector = projector_for(self.township)
        self.data_version = data_version_of(self.township)
        self.model_version = model_version()
        self.catalogue = {i.id: i for i in generate_catalogue(self.township, cfg)}

        self.results: dict[str, Any] = {}
        self.missing: list[str] = []
        self._load_results()

        self.township_payload = build_township_payload(
            self.township, self.projector, include_sensitive=True
        )
        self.township_payload_public = build_township_payload(
            self.township, self.projector, include_sensitive=False
        )
        self.hero: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_hero()
        # Uvicorn runs sync endpoints on a thread pool, so two requests can be
        # inside the cache at once. An OrderedDict is not safe under that.
        self._cache_lock = threading.Lock()
        self._scenario_cache: OrderedDict[str, dict[str, Any] | Path] = OrderedDict()
        self._hot: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._spill_dir: Path | None = None

    # ----------------------------------------------------------- result files

    def _load_results(self) -> None:
        for name, required in RESULT_FILES.items():
            path = self.results_path / f"{name}.json"
            if not path.exists():
                self.missing.append(name)
                if required:
                    logger.warning("required results file missing: %s", path)
                continue
            with path.open(encoding="utf-8") as fh:
                self.results[name] = json.load(fh)
        plans_dir = self.results_path / "plans"
        self.plans: dict[int, Any] = {}
        if plans_dir.is_dir():
            for path in sorted(plans_dir.glob("*.json")):
                try:
                    budget = int(path.stem)
                except ValueError:  # pragma: no cover
                    continue
                with path.open(encoding="utf-8") as fh:
                    self.plans[budget] = json.load(fh)

    def _load_hero(self) -> None:
        hero_dir = self.results_path / "hero"
        if not hero_dir.is_dir():
            return
        for path in sorted(hero_dir.glob("*__*.json")):
            scenario_id, _, plan_id = path.stem.partition("__")
            with path.open(encoding="utf-8") as fh:
                self.hero[(scenario_id, plan_id)] = json.load(fh)

    @property
    def loaded(self) -> list[str]:
        return sorted(self.results)

    def require(self, name: str) -> Any:
        """Fetch a results file or raise the documented 503."""
        from fastapi import HTTPException

        if name not in self.results:
            raise HTTPException(
                status_code=503,
                detail={"error": "results_missing", "file": f"{name}.json"},
            )
        return self.results[name]

    def versions(self) -> dict[str, str]:
        return {
            "data_version": self.data_version,
            "model_version": self.model_version,
        }

    # ------------------------------------------------------------- scenarios

    def scenario_id_for(self, key: str) -> str:
        return "adhoc-" + hashlib.sha256(key.encode()).hexdigest()[:12]

    def cache_get(self, key: str) -> dict[str, Any] | None:
        with self._cache_lock:
            value = self._scenario_cache.get(key)
            if value is not None:
                self._scenario_cache.move_to_end(key)
        if value is None:
            return None
        if not isinstance(value, Path):
            return value
        with self._cache_lock:
            hot = self._hot.get(key)
            if hot is not None:
                self._hot.move_to_end(key)
        if hot is not None:
            return hot
        try:
            decoded: dict[str, Any] = json.loads(value.read_text(encoding="utf-8"))
        except OSError:  # the spill file went away; treat it as a miss
            with self._cache_lock:
                self._scenario_cache.pop(key, None)
            return None
        self._remember_hot(key, decoded)
        return decoded

    def _remember_hot(self, key: str, value: dict[str, Any]) -> None:
        with self._cache_lock:
            self._hot[key] = value
            self._hot.move_to_end(key)
            while len(self._hot) > HOT_CACHE_SIZE:
                self._hot.popitem(last=False)

    def clear_scenario_cache(self) -> None:
        """Drop the ad-hoc scenario cache without rebuilding the whole state."""
        with self._cache_lock:
            entries = list(self._scenario_cache.values())
            self._scenario_cache.clear()
            self._hot.clear()
        for entry in entries:
            self._discard(entry)

    def cache_put(self, key: str, value: dict[str, Any]) -> None:
        payload: dict[str, Any] | Path = value
        encoded = json.dumps(value, default=str)
        if len(encoded) > CACHE_INLINE_BYTES:
            payload = self._spill(key, encoded)
            self._remember_hot(key, value)
        evicted: list[dict[str, Any] | Path] = []
        with self._cache_lock:
            existing = self._scenario_cache.pop(key, None)
            if existing is not None:
                evicted.append(existing)
            self._scenario_cache[key] = payload
            while len(self._scenario_cache) > SCENARIO_CACHE_SIZE:
                old_key, old = self._scenario_cache.popitem(last=False)
                self._hot.pop(old_key, None)
                evicted.append(old)
        for entry in evicted:
            self._discard(entry)

    def _spill(self, key: str, encoded: str) -> Path:
        """Write a large cached scenario to disk, keeping only its path in RAM."""
        import tempfile

        if self._spill_dir is None:
            self._spill_dir = Path(
                tempfile.mkdtemp(prefix="gotham-scenario-cache-")
            )
        path = self._spill_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"
        path.write_text(encoded, encoding="utf-8")
        return path

    @staticmethod
    def _discard(entry: dict[str, Any] | Path) -> None:
        if isinstance(entry, Path):
            entry.unlink(missing_ok=True)

    @property
    def is_public(self) -> bool:
        return self.role == PUBLIC_ROLE

    @property
    def copilot_available(self) -> bool:
        return bool(os.environ.get("GOTHAM_LLM_KEY"))


_STATE: AppState | None = None


def get_state() -> AppState:
    """FastAPI dependency returning the process-wide state."""
    global _STATE
    if _STATE is None:
        _STATE = AppState()
    return _STATE


def set_state(state: AppState | None) -> None:
    """Used by the tests to install a state built over a temporary results dir."""
    global _STATE
    _STATE = state


def require_planner(state: AppState) -> None:
    """Dependency guard for the endpoints that expose dependency structure."""
    from fastapi import HTTPException

    if state.is_public:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden_for_role", "role": state.role},
        )
