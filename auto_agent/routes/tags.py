"""Tag routes — CRUD for interest and constraint tags."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auto_agent.db import delete_tag, get_tags, insert_tag

router = APIRouter()


class TagCreate(BaseModel):
    label: str = Field(min_length=1)
    type: str | None = None
    tag_type: str | None = None


@router.get("/tags")
async def list_tags_route(request: Request) -> dict[str, Any]:
    """List all tags."""
    return {"items": get_tags(request.app.state.db)}


@router.post("/tags")
async def create_tag_route(request: Request, body: TagCreate) -> dict[str, Any]:
    """Add a tag."""
    normalized_type = body.type or body.tag_type
    if normalized_type not in {"interest", "constraint"}:
        raise HTTPException(
            status_code=400, detail="type/tag_type must be interest or constraint"
        )

    tag_id = insert_tag(
        request.app.state.db,
        label=body.label,
        tag_type=normalized_type,
    )
    return {"ok": True, "id": tag_id}


@router.delete("/tags/{tag_id}")
async def delete_tag_route(request: Request, tag_id: str) -> dict[str, Any]:
    """Remove a tag by id."""
    if not delete_tag(request.app.state.db, tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"ok": True}
