"""Tests for auto_agent.agent.synthesis."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_agent.agent.codex import CodexResult
from auto_agent.agent.synthesis import run_synthesis
from auto_agent.db import (
    connect,
    get_ideas,
    insert_article,
    insert_idea,
    insert_tag,
    update_idea_status,
)


@pytest.fixture()
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db = connect(tmp_path / "synthesis.db")
    yield db
    db.close()


@pytest.mark.asyncio
async def test_run_synthesis_persists_pending_ideas(conn: sqlite3.Connection) -> None:
    insert_article(
        conn,
        source="hn",
        title="Agentic tooling trend",
        relevance_score=0.95,
        content="high relevance",
    )
    insert_tag(conn, label="ai", tag_type="interest")
    insert_tag(conn, label="privacy", tag_type="constraint")

    fake_stdout = (
        "["
        '{"title":"Privacy-first changelog copilot",'
        '"description":"Summarize releases into safe rollouts",'
        '"tech_stack":["Python","FastAPI"],'
        '"why_now":"LLM usage is rising",'
        '"existing_alternatives":"Linear changelog tools",'
        '"effort_estimate":"2-3 weeks",'
        '"inspired_by":[],"matched_tags":["ai","privacy"]}'
        "]"
    )

    with patch(
        "auto_agent.agent.synthesis.run_codex",
        new=AsyncMock(
            return_value=CodexResult(stdout=fake_stdout, stderr="", exit_code=0)
        ),
    ) as run_mock:
        ideas = await run_synthesis(conn)

    assert len(ideas) == 1
    assert ideas[0]["status"] == "pending"
    assert run_mock.await_count == 1


@pytest.mark.asyncio
async def test_run_synthesis_skips_without_relevant_articles(
    conn: sqlite3.Connection,
) -> None:
    insert_article(conn, source="hn", title="low relevance", relevance_score=0.2)

    with patch("auto_agent.agent.synthesis.run_codex", new=AsyncMock()) as run_mock:
        ideas = await run_synthesis(conn, relevance_threshold=0.9)

    assert ideas == []
    assert run_mock.await_count == 0


@pytest.mark.asyncio
async def test_declined_ideas_do_not_block_other_valid_ideas(
    conn: sqlite3.Connection,
) -> None:
    insert_article(conn, source="blog", title="new trend", relevance_score=0.99)

    declined_id = insert_idea(
        conn,
        title="Old Declined Idea",
        description="desc",
        why_now="why",
        effort_estimate="1d",
    )
    update_idea_status(conn, declined_id, "declined", decline_reason="not useful")

    fake_stdout = (
        "["
        '{"title":"Fresh Idea",'
        '"description":"Different from declined one",'
        '"tech_stack":[],"why_now":"timely",'
        '"existing_alternatives":"none",'
        '"effort_estimate":"1 week",'
        '"inspired_by":[],"matched_tags":[]}'
        "]"
    )

    with patch(
        "auto_agent.agent.synthesis.run_codex",
        new=AsyncMock(
            return_value=CodexResult(stdout=fake_stdout, stderr="", exit_code=0)
        ),
    ):
        await run_synthesis(conn)

    idea_titles = [row["title"] for row in get_ideas(conn, limit=10)]
    assert "Fresh Idea" in idea_titles
