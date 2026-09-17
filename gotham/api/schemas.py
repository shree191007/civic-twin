"""Pydantic v2 models for the API boundary. Enums come from gotham.ontology."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gotham.ontology import Portfolio


class Versioned(BaseModel):
    """Every response carries the versions of the data and model behind it."""

    # `model_version` is a domain term here, not a pydantic attribute.
    model_config = ConfigDict(protected_namespaces=())

    data_version: str
    model_version: str


class HealthResponse(Versioned):
    status: Literal["ok", "degraded"]
    results_loaded: list[str]
    results_missing: list[str]
    copilot_available: bool
    role: str


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rain_mm: float = Field(ge=0.0, le=600.0)
    field_seed: int = Field(default=7, ge=0)
    onset_hour: int = Field(default=12, ge=0, le=23)
    forced_failures: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    invulnerable: list[str] = Field(default_factory=list)
    record: bool = True

    def hazard_key(self) -> str:
        """The part of the request that defines the storm itself.

        Interventions and forced failures are deliberately excluded: they
        change what the town does about the storm, not which storm it is, so
        they must not disturb the fragility draws.
        """
        return "|".join(
            [f"{self.rain_mm:.6f}", str(self.field_seed), str(self.onset_hour)]
        )

    def cache_key(self) -> str:
        return "|".join(
            [
                f"{self.rain_mm:.6f}",
                str(self.field_seed),
                str(self.onset_hour),
                ",".join(sorted(self.forced_failures)),
                ",".join(sorted(self.interventions)),
                ",".join(sorted(self.invulnerable)),
                str(self.record),
            ]
        )


class PeopleSummary(BaseModel):
    peak_no_power: int
    peak_no_water: int
    peak_no_comms: int
    peak_no_health: int
    person_hours_lost: float


class ScenarioResponse(Versioned):
    scenario_id: str
    scenario: dict[str, Any]
    result: dict[str, Any]
    people_summary: PeopleSummary
    cached: bool = False


class PrecomputedScenario(BaseModel):
    scenario_id: str
    rain_mm: float
    return_period_y: float | None
    plans: list[str]


class TraceResponse(Versioned):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    affected_population: int


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    rationale: str
    scenarios_considered: list[str] = Field(default_factory=list)
    author: str = "planner"
    #: What was decided about the plan. Older records have no status.
    status: Literal["approved", "approved_with_changes", "deferred", "rejected"] | None = None
    #: The plan's headline numbers as they stood when the decision was made, so
    #: the record still reads correctly after the analysis is re-run.
    plan_summary: dict[str, Any] | None = None


class DecisionRecord(DecisionRequest):
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    recorded_at: str
    data_version: str
    model_version: str


class CopilotScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_asset: str | None = None
    scenario_id: str | None = None
    t: float | None = None
    active_layers: list[Portfolio] = Field(default_factory=list)
    plan: str | None = None


class CopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    scene: CopilotScene | None = None


class CopilotToolCall(BaseModel):
    tool: str
    args: dict[str, Any]
    summary: str


class CopilotResponse(BaseModel):
    available: bool
    answer: str | None = None
    tool_calls: list[CopilotToolCall] = Field(default_factory=list)
    reason: str | None = None
