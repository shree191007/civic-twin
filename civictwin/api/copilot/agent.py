"""The copilot loop. It calls tools and explains their output; it invents nothing."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from civictwin.api.copilot.tools import TOOL_SCHEMAS, ToolBox
from civictwin.api.state import AppState

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 6
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a planning assistant for a municipal infrastructure resilience model of
{township_name}. You answer ONLY from tool results.

Rules:
- Never state a number that did not come from a tool result in this conversation.
- If a tool has not been called for a claim, call it or say you do not know.
- Confirm only what actually happened. If a tool returned no result, say so.
- Always name the dependency chain when explaining an impact.
- Report impact in people and hours first; technical risk metrics second.
- Distinguish data provenance when relevant: some layers are synthetic.
- You cannot make decisions or commit spending. You present options."""


class LLMClient(Protocol):
    """The slice of the Anthropic Messages API the copilot uses."""

    def create(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Any: ...


def _anthropic_client() -> LLMClient | None:
    key = os.environ.get("CIVICTWIN_LLM_KEY")
    if not key:
        return None
    try:  # pragma: no cover - exercised only with a real key
        import anthropic
    except ImportError:
        logger.warning("CIVICTWIN_LLM_KEY is set but the anthropic package is missing")
        return None

    class _Client:
        def __init__(self) -> None:
            self._inner = anthropic.Anthropic(api_key=key)

        def create(self, model, system, messages, tools, max_tokens):  # type: ignore[no-untyped-def]
            return self._inner.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )

    return _Client()


def _scene_preamble(scene: Any) -> str:
    if scene is None:
        return ""
    bits = []
    if getattr(scene, "selected_asset", None):
        bits.append(
            f'The user currently has asset {scene.selected_asset} selected; '
            f'"this" and "it" refer to {scene.selected_asset}.'
        )
    if getattr(scene, "scenario_id", None):
        bits.append(f"They are viewing scenario {scene.scenario_id}.")
    if getattr(scene, "plan", None):
        bits.append(f"The active plan is {scene.plan}.")
    if getattr(scene, "t", None) is not None:
        bits.append(f"The timeline is at t={scene.t} hours.")
    layers = getattr(scene, "active_layers", None)
    if layers:
        visible = ", ".join(l.value if hasattr(l, "value") else str(l) for l in layers)
        bits.append(f"Visible layers: {visible}.")
    return "\n\nCurrent view:\n" + "\n".join(f"- {b}" for b in bits) if bits else ""


def summarise_tool_result(name: str, payload: dict[str, Any]) -> str:
    """A one-line, human-readable trace entry for the UI."""
    if "error" in payload:
        return f"{name} returned no result ({payload['error']})"
    if name == "run_scenario":
        people = payload.get("people_summary", {})
        return (
            f"{people.get('peak_no_power', 0):,} people lost power, "
            f"{people.get('peak_no_water', 0):,} lost water, "
            f"{people.get('person_hours_lost', 0):,.0f} person-hours lost"
        )
    if name == "get_spofs":
        items = payload.get("items", [])
        if not items:
            return "no single points of failure recorded"
        top = items[0]
        return (
            f"{len(items)} SPOFs; worst is {top.get('shared_asset')} "
            f"affecting {top.get('affected_population', 0):,} people"
        )
    if name == "get_criticality":
        items = payload.get("items", [])
        return (
            f"{len(items)} assets ranked; top is {items[0]['asset_id']}"
            if items
            else "no criticality data"
        )
    if name == "get_asset":
        asset = payload.get("asset", {})
        return (
            f"{asset.get('id')} ({asset.get('kind')}) serves "
            f"{asset.get('served_population', 0):,} people"
        )
    if name == "trace_dependencies":
        return (
            f"{len(payload.get('nodes', []))} assets in the chain, "
            f"{payload.get('affected_population', 0):,} people downstream"
        )
    if name == "get_plan":
        return (
            f"plan costs {payload.get('cost_inr', 0):,.0f} INR and cuts tail risk "
            f"by {payload.get('cvar_reduction_pct', 0)}%"
        )
    if name == "compare_scenarios":
        delta = payload.get("delta", {})
        return f"person-hours lost change: {delta.get('person_hours_lost', 0):,.0f}"
    if name == "list_assets":
        return f"{payload.get('count', 0)} assets"
    return "ok"


def answer(
    state: AppState,
    question: str,
    scene: Any = None,
    client: LLMClient | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run the copilot turn. Returns the response body for POST /copilot."""
    client = client or _anthropic_client()
    if client is None:
        return {
            "available": False,
            "answer": None,
            "tool_calls": [],
            "reason": "Copilot not configured",
        }

    toolbox = ToolBox(state)
    # Offer only the tools this role may actually call, so the model is never
    # invited to ask for something it will be refused.
    tool_schemas = [t for t in TOOL_SCHEMAS if t["name"] not in toolbox.restricted]
    system = SYSTEM_PROMPT.format(township_name=state.township.name) + _scene_preamble(
        scene
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    trace: list[dict[str, Any]] = []
    model = model or os.environ.get("CIVICTWIN_LLM_MODEL", DEFAULT_MODEL)

    for _ in range(MAX_TOOL_CALLS):
        response = client.create(
            model=model,
            system=system,
            messages=messages,
            tools=tool_schemas,
            max_tokens=1500,
        )
        blocks = list(getattr(response, "content", []) or [])
        tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            text = "\n".join(
                getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"
            ).strip()
            return {
                "available": True,
                "answer": text or None,
                "tool_calls": trace,
                "reason": None,
            }

        messages.append({"role": "assistant", "content": blocks})
        results = []
        for block in tool_uses:
            name = getattr(block, "name", "")
            args = dict(getattr(block, "input", {}) or {})
            payload = toolbox.call(name, args)
            trace.append(
                {
                    "tool": name,
                    "args": args,
                    "summary": summarise_tool_result(name, payload),
                }
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", ""),
                    "content": json.dumps(payload, default=str),
                }
            )
        messages.append({"role": "user", "content": results})

    # Budget exhausted: ask for a final answer with no further tools.
    final = client.create(
        model=model,
        system=system
        + "\n\nYou have used your tool budget. Answer now from what you have.",
        messages=messages,
        tools=[],
        max_tokens=1500,
    )
    text = "\n".join(
        getattr(b, "text", "")
        for b in getattr(final, "content", []) or []
        if getattr(b, "type", None) == "text"
    ).strip()
    return {
        "available": True,
        "answer": text or None,
        "tool_calls": trace,
        "reason": "tool budget reached",
    }
