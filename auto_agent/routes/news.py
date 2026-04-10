"""News routes — GET /api/news with pagination and filtering."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/news")
async def list_news(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=200),
    source: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> dict[str, Any]:
    """List articles with optional source/tag filters."""
    conn = request.app.state.db
    clauses: list[str] = []
    params: list[Any] = []

    if source:
        clauses.append("source = ?")
        params.append(source)

    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(articles.matched_tags) WHERE value = ?)"
        )
        params.append(tag)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    count_row = conn.execute(
        f"SELECT COUNT(*) AS total FROM articles {where}",
        tuple(params),
    ).fetchone()
    total = int(count_row["total"]) if count_row else 0

    offset = (page - 1) * per_page
    rows = conn.execute(
        f"""
        SELECT * FROM articles
        {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, per_page, offset),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        try:
            record["matched_tags"] = json.loads(record.get("matched_tags") or "[]")
        except json.JSONDecodeError:
            record["matched_tags"] = []
        items.append(record)

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "has_more": offset + per_page < total,
    }
