"""The tools the copilot may call. Each is a thin wrapper over endpoint logic."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Literal

from gotham.api.state import AppState

logger = logging.getLogger(__name__)

#: No tool result may exceed this; longer payloads are truncated and say so.
MAX_TOOL_BYTES = 8_192


def _truncate(payload: dict[str, Any]) -> dict[str, Any]:
    """Shrink a payload until it fits, trimming the longest list each pass.

    Halving with a floor of one element cannot shrink a payload whose single
    remaining element is itself oversized, so the loop also stops as soon as a
    pass fails to make the payload any smaller.
    """
    size = len(json.dumps(payload, default=str))
    while size > MAX_TOOL_BYTES:
        lists = [
            (key, value)
            for key, value in payload.items()
            if isinstance(value, list) and value
        ]
        if not lists:
            break
        key, value = max(lists, key=lambda kv: len(kv[1]))
        payload[key] = value[: len(value) // 2]
        payload["truncated"] = True
        payload[f"{key}_shown"] = len(payload[key])
        new_size = len(json.dumps(payload, default=str))
        if new_size >= size:
            break
        size = new_size
    if size > MAX_TOOL_BYTES:
        # Nothing left to trim and it still does not fit: return the summary
        # rather than something the model cannot read anyway.
        payload = {
            k: v
            for k, v in payload.items()
            if k in {"error", "asset_id", "summary", "count"}
        }
        payload["truncated"] = True
        payload["note"] = "the full result was too large to return"
    return payload


#: Tools that expose how the town is wired together. A public viewer gets the
#: map and the scenario player; they do not get a list of what to attack.
RESTRICTED_TOOLS = frozenset(
    {"trace_dependencies", "get_spofs", "get_criticality"}
)


class ToolBox:
    """Bound to one AppState; every method returns JSON-safe data."""

    def __init__(self, state: AppState) -> None:
        self.state = state

    @property
    def restricted(self) -> frozenset[str]:
        return RESTRICTED_TOOLS if self.state.is_public else frozenset()

    # -------------------------------------------------------------- the tools

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        from gotham.api.routes.township import get_asset as endpoint

        try:
            return _truncate(dict(endpoint(asset_id, self.state)))
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as data
            return {"error": "unknown_asset", "id": asset_id, "detail": str(exc)}

    def trace_dependencies(
        self, asset_id: str, direction: Literal["up", "down"] = "down"
    ) -> dict[str, Any]:
        from gotham.api.routes.township import trace as endpoint

        try:
            return _truncate(dict(endpoint(asset_id, self.state, direction=direction)))
        except Exception as exc:  # noqa: BLE001
            return {"error": "trace_failed", "id": asset_id, "detail": str(exc)}

    def run_scenario(
        self,
        rain_mm: float,
        forced_failures: list[str] | None = None,
        interventions: list[str] | None = None,
    ) -> dict[str, Any]:
        from gotham.api.routes.scenarios import run_scenario as endpoint
        from gotham.api.schemas import ScenarioRequest

        request = ScenarioRequest(
            rain_mm=rain_mm,
            forced_failures=list(forced_failures or ()),
            interventions=list(interventions or ()),
            record=True,
        )
        try:
            payload = endpoint(request, self.state)
        except Exception as exc:  # noqa: BLE001
            return {"error": "simulation_failed", "detail": str(exc)}
        result = payload["result"]
        return {
            "scenario_id": payload["scenario_id"],
            "rain_mm": rain_mm,
            "people_summary": payload["people_summary"],
            "damaged_assets": sorted(result.get("damaged_assets", {})),
            "recovery_90_h": result.get("recovery_90_h", {}),
            "loss_by_service_ph": result.get("loss_by_service_ph", {}),
        }

    def compare_scenarios(self, a: str, b: str) -> dict[str, Any]:
        from gotham.api.routes.scenarios import compare as endpoint

        try:
            return _truncate(dict(endpoint(self.state, a=a, b=b)))
        except Exception as exc:  # noqa: BLE001
            return {"error": "unknown_scenario", "a": a, "b": b, "detail": str(exc)}

    def get_criticality(
        self, portfolio: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        from gotham.api.routes.analytics import criticality as endpoint

        try:
            payload = dict(endpoint(self.state, portfolio=portfolio, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {"error": "results_missing", "detail": str(exc)}
        payload["items"] = [
            {
                k: v
                for k, v in row.items()
                if k
                in {
                    "asset_id",
                    "portfolio",
                    "tail_criticality_ph",
                    "annual_failure_prob",
                    "systemic_ratio",
                    "systemic",
                    "served_population",
                    "explanation",
                }
            }
            for row in payload.get("items", [])
        ]
        return _truncate(payload)

    def get_spofs(self, limit: int = 5) -> dict[str, Any]:
        from gotham.api.routes.analytics import spofs as endpoint

        try:
            return _truncate(dict(endpoint(self.state, limit=limit)))
        except Exception as exc:  # noqa: BLE001
            return {"error": "results_missing", "detail": str(exc)}

    def get_plan(self, budget_inr: float) -> dict[str, Any]:
        from gotham.api.routes.plans import get_plan as endpoint

        try:
            payload = dict(endpoint(self.state, budget=budget_inr))
        except Exception as exc:  # noqa: BLE001
            return {"error": "results_missing", "detail": str(exc)}
        plan = payload.get("plan", {})
        return _truncate(
            {
                "budget_inr": payload.get("budget_inr"),
                "cost_inr": plan.get("cost_inr"),
                "cvar_before": plan.get("cvar_before"),
                "cvar_after": plan.get("cvar_after"),
                "cvar_reduction_pct": plan.get("cvar_reduction_pct"),
                "interventions": payload.get("interventions", []),
            }
        )

    def list_assets(
        self, kind: str | None = None, portfolio: str | None = None
    ) -> dict[str, Any]:
        rows = [
            {
                "id": a.id,
                "kind": a.kind.value,
                "portfolio": a.portfolio.value,
                "name": a.name,
                "served_population": self.state.township.served_population(a.id),
            }
            for a in sorted(self.state.township.assets.values(), key=lambda a: a.id)
            if (kind is None or a.kind.value == kind)
            and (portfolio is None or a.portfolio.value == portfolio)
        ]
        return _truncate({"count": len(rows), "items": rows})

    # ------------------------------------------------------------- dispatch

    def registry(self) -> dict[str, Callable[..., dict[str, Any]]]:
        """The tools this role may call."""
        restricted = self.restricted
        return {
            k: v
            for k, v in self._all_tools().items()
            if k not in restricted
        }

    def _all_tools(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "get_asset": self.get_asset,
            "trace_dependencies": self.trace_dependencies,
            "run_scenario": self.run_scenario,
            "compare_scenarios": self.compare_scenarios,
            "get_criticality": self.get_criticality,
            "get_spofs": self.get_spofs,
            "get_plan": self.get_plan,
            "list_assets": self.list_assets,
        }

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in self.restricted:
            return {
                "error": "forbidden_for_role",
                "tool": name,
                "detail": (
                    "Dependency structure is not available to this role. Say so "
                    "plainly rather than describing it from memory."
                ),
            }
        fn = self.registry().get(name)
        if fn is None:
            return {"error": "unknown_tool", "tool": name}
        try:
            return fn(**args)
        except TypeError as exc:
            return {"error": "bad_arguments", "tool": name, "detail": str(exc)}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_asset",
        "description": "Full detail for one asset: properties, criticality, "
        "dependencies, affected zones, and any SPOF naming it.",
        "input_schema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "trace_dependencies",
        "description": "The dependency subgraph above or below an asset.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["up", "down"]},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "run_scenario",
        "description": "Simulate a storm of a given rainfall, optionally forcing "
        "assets to fail or applying interventions. Returns people affected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rain_mm": {"type": "number"},
                "forced_failures": {"type": "array", "items": {"type": "string"}},
                "interventions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["rain_mm"],
        },
    },
    {
        "name": "compare_scenarios",
        "description": "Compare two precomputed scenario/plan pairs, "
        'each written as "scenario_id:plan".',
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "get_criticality",
        "description": "Assets ranked by how much tail risk they carry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_spofs",
        "description": "Hidden single points of failure behind apparent redundancy.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
    {
        "name": "get_plan",
        "description": "The recommended investment plan at a given budget.",
        "input_schema": {
            "type": "object",
            "properties": {"budget_inr": {"type": "number"}},
            "required": ["budget_inr"],
        },
    },
    {
        "name": "list_assets",
        "description": "List assets, optionally filtered by kind or portfolio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "portfolio": {"type": "string"},
            },
        },
    },
]
