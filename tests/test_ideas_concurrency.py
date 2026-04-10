"""Concurrency tests for idea approval and implementation triggering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from auto_agent.agent.brain import AgentBrain
from auto_agent.agent.builder import BuildResult
from auto_agent.config import Config
from auto_agent.db import connect, insert_idea
from auto_agent.routes.ideas import approve_idea


async def _approve_status(request: Any, idea_id: str) -> int:
    """Run approve_idea and normalize response into an HTTP-like status code."""
    try:
        await approve_idea(request, idea_id)
        return 200
    except HTTPException as exc:
        return int(exc.status_code)


@pytest.mark.asyncio
async def test_concurrent_approvals_trigger_single_build(tmp_path: Any) -> None:
    """Two concurrent approvals should start exactly one implementation task."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    idea_id = insert_idea(
        conn,
        title="Concurrent approval",
        description="Race guard",
        why_now="Now",
        effort_estimate="1d",
    )
    brain = AgentBrain(conn, Config(database=str(db_path)))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db=conn, brain=brain))
    )

    release_build = asyncio.Event()

    async def _fake_build(*args: Any, **kwargs: Any) -> BuildResult:
        await release_build.wait()
        return BuildResult(success=True, project_path="projects/concurrent-approval")

    run_mock = AsyncMock(side_effect=_fake_build)

    try:
        with patch("auto_agent.agent.brain.build_approved_idea", new=run_mock):
            first = asyncio.create_task(_approve_status(request, idea_id))
            await asyncio.sleep(0)
            second = asyncio.create_task(_approve_status(request, idea_id))

            first_status, second_status = await asyncio.gather(first, second)
            release_build.set()
            await brain.stop(wait_for_current_task=True)
    finally:
        conn.close()

    assert sorted([first_status, second_status]) == [200, 409]
    assert run_mock.await_count == 1
