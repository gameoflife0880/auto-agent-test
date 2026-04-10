"""Data routes for logs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from auto_agent.db import get_logs

router = APIRouter()


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return agent logs newest-first."""
    return {"items": get_logs(request.app.state.db, limit=limit)}
