"""Feed routes — CRUD for RSS feed sources."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auto_agent.db import delete_feed, get_feeds, insert_feed

router = APIRouter()


class FeedCreate(BaseModel):
    source: str = Field(min_length=1)
    url: str = Field(min_length=1)
    max_items: int = Field(default=10, ge=1, le=200)


@router.get("/feeds")
async def list_feeds_route(request: Request) -> dict[str, Any]:
    """List all feeds."""
    return {"items": get_feeds(request.app.state.db)}


@router.post("/feeds")
async def create_feed_route(request: Request, body: FeedCreate) -> dict[str, Any]:
    """Add a feed."""
    feed_id = insert_feed(
        request.app.state.db,
        source=body.source,
        url=body.url,
        max_items=body.max_items,
    )
    return {"ok": True, "id": feed_id}


@router.delete("/feeds/{feed_id}")
async def delete_feed_route(request: Request, feed_id: str) -> dict[str, Any]:
    """Remove a feed by id."""
    if not delete_feed(request.app.state.db, feed_id):
        raise HTTPException(status_code=404, detail="Feed not found")
    return {"ok": True}
