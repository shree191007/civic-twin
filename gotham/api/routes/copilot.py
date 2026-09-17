"""The copilot endpoint."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gotham.api.copilot import agent
from gotham.api.schemas import CopilotRequest
from gotham.api.state import AppState, get_state

router = APIRouter(tags=["copilot"])
State = Annotated[AppState, Depends(get_state)]

#: Tests install a stub here rather than reaching the network.
CLIENT_OVERRIDE: object | None = None


@router.post("/copilot")
def copilot(request: CopilotRequest, state: State) -> dict[str, object]:
    return agent.answer(
        state,
        request.question,
        scene=request.scene,
        client=CLIENT_OVERRIDE,  # type: ignore[arg-type]
    )
