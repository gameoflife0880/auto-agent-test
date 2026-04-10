"""Research pipeline: RSS ingestion plus Codex-driven article analysis."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import feedparser  # type: ignore[import-untyped]
import httpx

from auto_agent.agent.codex import run_codex
from auto_agent.config import Config
from auto_agent.normalize import article_id_from_url, sha256_hex


def _extract_published(entry: Any) -> int | None:
    """Extract UNIX timestamp from RSS entry if available."""
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is None:
        return None
    try:
        return int(time.mktime(parsed))
    except (OverflowError, ValueError, TypeError):
        return None


def _extract_content(entry: Any) -> str:
    """Extract a useful text body from an RSS entry."""
    if hasattr(entry, "summary") and entry.summary:
        return str(entry.summary)

    content = getattr(entry, "content", None)
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict):
            value = first.get("value")
            if value:
                return str(value)
    return ""


def _entry_id(source: str, entry: Any) -> str:
    """Build a stable article ID from GUID/link with normalize.py utilities."""
    link = str(getattr(entry, "link", "") or "")
    if link:
        return article_id_from_url(link)

    guid = str(getattr(entry, "id", "") or getattr(entry, "guid", "") or "")
    if guid:
        return sha256_hex(guid.strip())

    title = str(getattr(entry, "title", "") or "")
    return sha256_hex(f"{source}:{title}")


async def process_feed(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    feed: Mapping[str, Any],
) -> int:
    """Fetch and parse one RSS feed, inserting new articles with dedupe."""
    response = await client.get(str(feed["url"]))
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    max_items = int(feed.get("max_items", 10))
    inserted = 0

    for entry in parsed.entries[:max_items]:
        title = str(getattr(entry, "title", "") or "").strip()
        if not title:
            continue

        article_id = _entry_id(str(feed["source"]), entry)
        content = _extract_content(entry)
        published = _extract_published(entry)

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO articles
            (id, source, title, content, content_is_full, published, relevance_score, matched_tags)
            VALUES (?, ?, ?, ?, 0, ?, 0.0, '[]')
            """,
            (article_id, str(feed["source"]), title, content, published),
        )
        inserted += cur.rowcount

    conn.commit()
    return inserted


def _ensure_seed_feeds(config: Config, conn: sqlite3.Connection) -> None:
    """Seed configured feeds into DB when the feeds table is empty."""
    row = conn.execute("SELECT COUNT(*) AS c FROM feeds").fetchone()
    if row is None or int(row["c"]) > 0:
        return

    for feed in config.feeds:
        conn.execute(
            """
            INSERT OR IGNORE INTO feeds (id, source, url, max_items, active, discovered_by)
            VALUES (lower(hex(randomblob(6))), ?, ?, ?, 1, 'config')
            """,
            (feed.source, feed.url, feed.max_items),
        )
    conn.commit()


async def run_rss_fetch(config: Config, conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch all active feeds from DB and return ingestion stats."""
    _ensure_seed_feeds(config, conn)

    feeds = conn.execute(
        "SELECT id, source, url, max_items FROM feeds WHERE active = 1 ORDER BY source"
    ).fetchall()

    attempted = 0
    succeeded = 0
    inserted = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        for feed in feeds:
            attempted += 1
            try:
                inserted += await process_feed(client, conn, dict(feed))
                succeeded += 1
            except Exception:
                continue

    return {
        "feeds_attempted": attempted,
        "feeds_succeeded": succeeded,
        "articles_inserted": inserted,
    }


def _extract_json_blob(text: str) -> dict[str, Any]:
    """Extract first top-level JSON object from plain output or fenced code."""
    stripped = text.strip()
    if not stripped:
        return {}

    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    try:
        loaded = json.loads(stripped[start : end + 1])
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _build_analysis_prompt(
    articles: list[dict[str, Any]],
    interest_tags: list[str],
    constraint_tags: list[str],
    max_searches: int,
) -> str:
    payload = [
        {
            "id": row["id"],
            "source": row["source"],
            "title": row["title"],
            "content": row["content"],
            "published": row["published"],
        }
        for row in articles
    ]

    return (
        "You are analyzing RSS news for an autonomous research agent.\n"
        "Return only valid JSON (no markdown).\n\n"
        f"Articles JSON:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"Active interest tags: {json.dumps(interest_tags, ensure_ascii=False)}\n"
        f"Active constraint tags: {json.dumps(constraint_tags, ensure_ascii=False)}\n"
        f"Max web searches allowed: {max_searches}\n\n"
        "Tasks:\n"
        "1) Score each article relevance (0.0-1.0) against interest tags.\n"
        "2) For high relevance articles with weak detail, search web for extra context.\n"
        "3) Suggest RSS feeds related to interest tags.\n\n"
        "Output JSON schema:\n"
        '{"scored_articles":[{"id":"...","relevance_score":0.0,"matched_tags":["..."]}],'
        '"additional_context":[{"article_id":"...","context":"..."}],'
        '"suggested_feeds":[{"source":"...","url":"...","reason":"..."}]}'
    )


async def run_analysis(
    conn: sqlite3.Connection,
    config: Config,
    cancel_event: asyncio.Event,
    on_output: Callable[[str], None] | None,
) -> dict[str, int]:
    """Invoke Codex analysis and persist article scoring + suggested feeds."""
    articles = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM articles ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    ]
    if not articles:
        return {"scored": 0, "contexts_added": 0, "feeds_suggested": 0}

    tag_rows = conn.execute(
        "SELECT label, type FROM tags WHERE active = 1 ORDER BY label"
    ).fetchall()
    interest_tags = [r["label"] for r in tag_rows if r["type"] == "interest"]
    constraint_tags = [r["label"] for r in tag_rows if r["type"] == "constraint"]

    prompt = _build_analysis_prompt(
        articles,
        interest_tags,
        constraint_tags,
        config.max_searches_per_run,
    )

    result = await run_codex(
        prompt,
        cancel_event=cancel_event,
        on_output=on_output,
    )
    payload = _extract_json_blob(result.stdout)

    scored_count = 0
    for item in payload.get("scored_articles", []):
        if not isinstance(item, dict):
            continue
        article_id = str(item.get("id", "")).strip()
        if not article_id:
            continue

        score_raw = item.get("relevance_score", 0.0)
        try:
            score = max(0.0, min(1.0, float(score_raw)))
        except (TypeError, ValueError):
            score = 0.0

        tags_raw = item.get("matched_tags", [])
        tags = (
            [str(t) for t in tags_raw if str(t).strip()]
            if isinstance(tags_raw, list)
            else []
        )

        conn.execute(
            "UPDATE articles SET relevance_score = ?, matched_tags = ? WHERE id = ?",
            (score, json.dumps(tags), article_id),
        )
        scored_count += 1

    context_count = 0
    for item in payload.get("additional_context", []):
        if not isinstance(item, dict):
            continue
        article_id = str(item.get("article_id", "")).strip()
        context = str(item.get("context", "")).strip()
        if not article_id or not context:
            continue

        row = conn.execute(
            "SELECT content FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if row is None:
            continue

        original = str(row["content"] or "")
        if context in original:
            continue

        combined = (
            f"{original}\n\nAdditional context ({datetime.utcnow().isoformat()}Z):\n"
            f"{context}"
        ).strip()
        conn.execute(
            "UPDATE articles SET content = ? WHERE id = ?", (combined, article_id)
        )
        context_count += 1

    suggested_count = 0
    for item in payload.get("suggested_feeds", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip()
        url = str(item.get("url", "")).strip()
        if not source or not url:
            continue

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO feeds (id, source, url, max_items, active, discovered_by)
            VALUES (lower(hex(randomblob(6))), ?, ?, 10, 1, 'agent')
            """,
            (source, url),
        )
        suggested_count += cur.rowcount

    conn.commit()
    return {
        "scored": scored_count,
        "contexts_added": context_count,
        "feeds_suggested": suggested_count,
    }
