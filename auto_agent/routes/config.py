"""Settings routes — GET and POST /api/config."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from auto_agent.db import get_setting, set_setting

router = APIRouter()


class ConfigUpdate(BaseModel):
    """Body for POST /api/config."""

    synthesis_prompt: str | None = None
    research_interval_hours: int | None = None


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return editable settings."""
    conn = request.app.state.db
    return {
        "synthesis_prompt": get_setting(conn, "synthesis_prompt") or "",
        "research_interval_hours": int(
            get_setting(conn, "research_interval_hours")
            or request.app.state.config.research_interval_hours
        ),
    }


@router.post("/config")
async def post_config(request: Request, body: ConfigUpdate) -> dict[str, Any]:
    """Update editable settings."""
    conn = request.app.state.db
    if body.synthesis_prompt is not None:
        set_setting(conn, "synthesis_prompt", body.synthesis_prompt)
    if body.research_interval_hours is not None:
        set_setting(conn, "research_interval_hours", str(body.research_interval_hours))
    return {"ok": True}
