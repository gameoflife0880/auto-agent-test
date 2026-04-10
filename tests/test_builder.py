"""Tests for idea implementation builder flow."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_agent.agent.builder import build_approved_idea
from auto_agent.agent.codex import CodexResult, CodexTimeoutError
from auto_agent.db import connect, get_idea_by_id, insert_idea, update_idea_status


@pytest.fixture()
def conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db = connect(tmp_path / "builder.db")
    yield db
    db.close()


def _idea_id(conn: sqlite3.Connection) -> str:
    idea_id = insert_idea(
        conn,
        title="CLI Task Runner",
        description="Create a small command runner utility.",
        why_now="Teams need automation",
        effort_estimate="1 week",
        tech_stack=["Python"],
    )
    update_idea_status(conn, idea_id, "approved")
    return idea_id


@pytest.mark.asyncio
async def test_builder_marks_completed_on_success(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    idea_id = _idea_id(conn)
    logs: list[tuple[str, str]] = []
    outputs: list[str] = []
    projects_root = tmp_path / "projects-success"

    with patch(
        "auto_agent.agent.builder.run_codex",
        new=AsyncMock(return_value=CodexResult(stdout="done", stderr="", exit_code=0)),
    ):
        result = await build_approved_idea(
            conn,
            idea_id,
            projects_root=projects_root,
            on_log=lambda message, level: logs.append((message, level)),
            on_output=outputs.append,
        )

    stored = get_idea_by_id(conn, idea_id)
    assert result.success is True
    assert stored is not None
    assert stored["status"] == "completed"
    assert str(stored["project_path"]).endswith("cli-task-runner")
    assert logs


@pytest.mark.asyncio
async def test_builder_marks_failed_on_nonzero_exit(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    idea_id = _idea_id(conn)
    projects_root = tmp_path / "projects-nonzero"

    with patch(
        "auto_agent.agent.builder.run_codex",
        new=AsyncMock(
            return_value=CodexResult(
                stdout="",
                stderr="build failed",
                exit_code=1,
            )
        ),
    ):
        result = await build_approved_idea(
            conn,
            idea_id,
            projects_root=projects_root,
        )

    stored = get_idea_by_id(conn, idea_id)
    assert result.success is False
    assert stored is not None
    assert stored["status"] == "failed"
    assert "build failed" in str(stored["decline_reason"])


@pytest.mark.asyncio
async def test_builder_marks_failed_on_timeout(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    idea_id = _idea_id(conn)
    projects_root = tmp_path / "projects-timeout"

    with patch(
        "auto_agent.agent.builder.run_codex",
        new=AsyncMock(side_effect=CodexTimeoutError("timed out")),
    ):
        result = await build_approved_idea(
            conn,
            idea_id,
            projects_root=projects_root,
        )

    stored = get_idea_by_id(conn, idea_id)
    assert result.success is False
    assert stored is not None
    assert stored["status"] == "failed"
    assert "timed out" in str(stored["decline_reason"])


@pytest.mark.asyncio
async def test_builder_passes_absolute_project_dir_as_working_dir(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    idea_id = _idea_id(conn)
    projects_root = tmp_path / "projects-working-dir"
    run_mock = AsyncMock(
        return_value=CodexResult(stdout="done", stderr="", exit_code=0)
    )

    with patch("auto_agent.agent.builder.run_codex", new=run_mock):
        await build_approved_idea(conn, idea_id, projects_root=projects_root)

    assert run_mock.await_count == 1
    call = run_mock.await_args
    assert call is not None
    _, kwargs = call
    assert kwargs["working_dir"] == str(projects_root / "cli-task-runner")
