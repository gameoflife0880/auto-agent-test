"""Data CRUD routes for articles, ideas, tags, feeds, and logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from auto_agent.db import (
    delete_feed,
    delete_tag,
    get_articles,
    get_feeds,
    get_ideas,
    get_logs,
    get_tags,
    insert_feed,
    insert_tag,
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


# --- Tags ----------------------------------------------------------------- #


class TagCreate(BaseModel):
    label: str
    tag_type: str  # "interest" | "constraint"


@router.get("/tags")
async def list_tags(request: Request) -> dict[str, Any]:
    return {"items": get_tags(request.app.state.db)}


@router.post("/tags")
async def create_tag(request: Request, body: TagCreate) -> dict[str, Any]:
    if body.tag_type not in ("interest", "constraint"):
        raise HTTPException(
            status_code=400, detail="tag_type must be interest or constraint"
        )
    tid = insert_tag(request.app.state.db, label=body.label, tag_type=body.tag_type)
    return {"ok": True, "id": tid}


@router.delete("/tags/{tag_id}")
async def remove_tag(request: Request, tag_id: str) -> dict[str, Any]:
    if not delete_tag(request.app.state.db, tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"ok": True}


# --- Feeds ---------------------------------------------------------------- #


class FeedCreate(BaseModel):
    source: str
    url: str
    max_items: int = 10


@router.get("/feeds")
async def list_feeds(request: Request) -> dict[str, Any]:
    return {"items": get_feeds(request.app.state.db)}


@router.post("/feeds")
async def create_feed(request: Request, body: FeedCreate) -> dict[str, Any]:
    fid = insert_feed(
        request.app.state.db,
        source=body.source,
        url=body.url,
        max_items=body.max_items,
    )
    return {"ok": True, "id": fid}


@router.delete("/feeds/{feed_id}")
async def remove_feed(request: Request, feed_id: str) -> dict[str, Any]:
    if not delete_feed(request.app.state.db, feed_id):
        raise HTTPException(status_code=404, detail="Feed not found")
    return {"ok": True}


# --- Logs ----------------------------------------------------------------- #


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    return {"items": get_logs(request.app.state.db, limit=limit)}
