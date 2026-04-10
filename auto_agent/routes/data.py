"""Data CRUD routes for articles, ideas, tags, feeds, and logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from auto_agent.db import (
    get_articles,
    get_ideas,
    get_logs,
    update_idea_status,
)

router = APIRouter()

# --- Articles ------------------------------------------------------------- #


@router.get("/articles")
async def list_articles(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return paginated articles."""
    conn = request.app.state.db
    # Fetch one extra to know if there are more pages.
    all_rows = get_articles(conn, limit=limit + offset + 1)
    items = all_rows[offset : offset + limit]
    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "has_more": len(all_rows) > offset + limit,
    }


# --- Ideas ---------------------------------------------------------------- #


@router.get("/ideas")
async def list_ideas(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return ideas, optionally filtered by status."""
    conn = request.app.state.db
    ideas = get_ideas(conn, limit=limit)
    if status:
        ideas = [i for i in ideas if i["status"] == status]
    return {"items": ideas}


class IdeaAction(BaseModel):
    """Body for PUT /api/ideas/{id}."""

    status: str
    decline_reason: str = ""


@router.put("/ideas/{idea_id}")
async def update_idea(
    request: Request,
    idea_id: str,
    body: IdeaAction,
) -> dict[str, Any]:
    """Approve, decline, or update idea status."""
    valid = {"pending", "approved", "declined", "implementing", "completed", "failed"}
    if body.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
    conn = request.app.state.db
    update_idea_status(conn, idea_id, body.status, decline_reason=body.decline_reason)
    return {"ok": True, "id": idea_id, "status": body.status}


# --- Logs ----------------------------------------------------------------- #


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    return {"items": get_logs(request.app.state.db, limit=limit)}
