"""Agent status routes — GET and POST /api/status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class StatusAction(BaseModel):
    action: str  # "start_research" | "stop"


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Return current agent state and Codex CLI health."""
    return request.app.state.brain.get_status()


@router.post("/status")
async def post_status(request: Request, body: StatusAction) -> dict[str, Any]:
    """Control the agent — start research or stop."""
    brain = request.app.state.brain
    if body.action == "start_research":
        result = await brain.start_research()
        return {"status": result["status"], "started": result["started"]}
    if body.action == "stop":
        return await brain.stop()
    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
