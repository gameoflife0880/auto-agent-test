"""Idea routes for listing and approving/declining synthesized ideas."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auto_agent.db import get_idea_by_id, get_ideas, update_idea_status
from auto_agent.routes.ws import emit_idea_update

router = APIRouter()


@router.get("/ideas")
async def list_ideas(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return ideas, optionally filtered by status."""
    ideas = get_ideas(request.app.state.db, limit=limit, status=status)
    return {"items": ideas}


@router.post("/ideas/{idea_id}/approve")
async def approve_idea(request: Request, idea_id: str) -> dict[str, Any]:
    """Mark an idea approved and trigger implementation orchestration."""
    conn = request.app.state.db
    idea = get_idea_by_id(conn, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    brain = request.app.state.brain
    result = await brain.trigger_implementation(idea_id)
    if not result["started"]:
        raise HTTPException(
            status_code=409,
            detail=f"Agent is busy ({result['status']}); only one build can run at a time.",
        )

    return {"ok": True, "id": idea_id, "status": "implementing", "started": True}


class DeclineBody(BaseModel):
    """Body for declining an idea."""

    reason: str = Field(min_length=1)


@router.post("/ideas/{idea_id}/decline")
async def decline_idea(
    request: Request,
    idea_id: str,
    body: DeclineBody,
) -> dict[str, Any]:
    """Mark an idea declined and persist the reason."""
    conn = request.app.state.db
    idea = get_idea_by_id(conn, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    update_idea_status(conn, idea_id, "declined", decline_reason=body.reason)
    updated = get_idea_by_id(conn, idea_id)
    if updated is not None:
        await emit_idea_update(updated)

    return {"ok": True, "id": idea_id, "status": "declined"}
