"""FastAPI application: a read-through cache over results/ plus a live simulator."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gotham.api.routes import (
    analytics,
    copilot,
    evidence,
    hazard,
    plans,
    scenarios,
    township,
)
from gotham.api.state import RESULT_FILES, get_state

logger = logging.getLogger(__name__)

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

app = FastAPI(
    title="gotham",
    version="0.1.0",
    description=(
        "Portfolio risk analytics for township infrastructure. Every number "
        "served here comes from the simulator or the precomputed analytics."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to a client."""
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "path": request.url.path},
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    state = get_state()
    required_missing = [
        name for name in state.missing if RESULT_FILES.get(name, False)
    ]
    return {
        "status": "degraded" if required_missing else "ok",
        "results_loaded": state.loaded,
        "results_missing": sorted(state.missing),
        "copilot_available": state.copilot_available,
        "role": state.role,
        **state.versions(),
    }


app.include_router(township.router)
app.include_router(scenarios.router)
app.include_router(analytics.router)
app.include_router(plans.router)
app.include_router(hazard.router)
app.include_router(evidence.router)
app.include_router(copilot.router)
