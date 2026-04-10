"""Data CRUD routes for articles and logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from auto_agent.db import get_articles, get_logs

router = APIRouter()


@router.get("/articles")
async def list_articles(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return paginated articles."""
    conn = request.app.state.db
    all_rows = get_articles(conn, limit=limit + offset + 1)
    items = all_rows[offset : offset + limit]
    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "has_more": len(all_rows) > offset + limit,
    }


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return agent logs newest-first."""
    return {"items": get_logs(request.app.state.db, limit=limit)}
