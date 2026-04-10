"""Agent status routes — GET and POST /api/status."""

from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auto_agent.db import get_agent_state, set_agent_status

router = APIRouter()


class StatusAction(BaseModel):
    action: str  # "start_research" | "stop"


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Return current agent state and Codex CLI health."""
    state = get_agent_state(request.app.state.db)
    codex_available = shutil.which("codex") is not None
    return {
        "status": state["status"],
        "current_idea_id": state["current_idea_id"],
        "codex_cli": "ok" if codex_available else "not_found",
    }


@router.post("/status")
async def post_status(request: Request, body: StatusAction) -> dict[str, Any]:
    """Control the agent — start research or stop."""
    conn = request.app.state.db
    if body.action == "start_research":
        set_agent_status(conn, "researching")
        return {"status": "researching"}
    if body.action == "stop":
        set_agent_status(conn, "idle")
        return {"status": "idle"}
    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
