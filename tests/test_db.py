"""Database smoke tests: schema, CRUD, and dedup behavior."""

from __future__ import annotations

from typing import Any

from auto_agent.db import (
    connect,
    get_idea_by_id,
    get_articles,
    get_feeds,
    get_ideas,
    get_tags,
    insert_article,
    insert_feed,
    insert_idea,
    insert_tag,
    update_idea_status,
)


def test_schema_creation(tmp_path: Any) -> None:
    """connect() bootstraps the required schema."""
    conn = connect(tmp_path / "db.sqlite3")
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "articles" in tables
    assert "tags" in tables
    assert "feeds" in tables
    assert "ideas" in tables
    assert "agent_log" in tables
    assert "agent_state" in tables
    assert "settings" in tables


def test_crud_operations_and_article_dedup(tmp_path: Any) -> None:
    """Basic insert/list operations work and article dedup prevents duplicates."""
    conn = connect(tmp_path / "db.sqlite3")
    try:
        insert_tag(conn, label="python", tag_type="interest")
        insert_feed(conn, source="HN", url="https://example.com/feed.xml")
        insert_idea(
            conn,
            title="Idea",
            description="Build tool",
            why_now="Demand exists",
            effort_estimate="2d",
            tech_stack=["python", "fastapi"],
        )

        insert_article(conn, source="source-a", title="duplicate-title")
        insert_article(conn, source="source-a", title="duplicate-title")

        tags = get_tags(conn)
        feeds = get_feeds(conn)
        ideas = get_ideas(conn)
        articles = get_articles(conn, limit=10)
    finally:
        conn.close()

    assert len(tags) == 1
    assert len(feeds) == 1
    assert len(ideas) == 1
    assert len(articles) == 1
    assert articles[0]["title"] == "duplicate-title"


def test_update_idea_status_keeps_optional_fields_when_not_provided(
    tmp_path: Any,
) -> None:
    """Status-only updates should not erase decline_reason or project_path."""
    conn = connect(tmp_path / "db.sqlite3")
    try:
        idea_id = insert_idea(
            conn,
            title="Idea",
            description="Build tool",
            why_now="Demand exists",
            effort_estimate="2d",
        )
        update_idea_status(
            conn,
            idea_id,
            "declined",
            decline_reason="not useful",
            project_path="projects/idea",
        )
        update_idea_status(conn, idea_id, "approved")
        updated = get_idea_by_id(conn, idea_id)
    finally:
        conn.close()

    assert updated is not None
    assert updated["status"] == "approved"
    assert updated["decline_reason"] == "not useful"
    assert updated["project_path"] == "projects/idea"
