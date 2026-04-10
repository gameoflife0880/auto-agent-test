"""Agent brain state machine with scheduled research loop."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from auto_agent.agent.builder import build_approved_idea
from auto_agent.agent.research import run_analysis, run_rss_fetch
from auto_agent.agent.synthesis import SynthesisError, run_synthesis
from auto_agent.config import Config
from auto_agent.db import (
    add_log,
    get_agent_state,
    get_idea_by_id,
    get_setting,
    update_idea_status,
    set_agent_status,
    set_setting,
)
from auto_agent.routes.ws import emit_idea_update, emit_log, emit_status


class AgentBrain:
    """Coordinates research state transitions and scheduled execution."""

    def __init__(self, conn: sqlite3.Connection, config: Config) -> None:
        self._conn = conn
        self._config = config
        self._lock = asyncio.Lock()
        self._cancel_event = asyncio.Event()
        self._cycle_task: asyncio.Task[None] | None = None
        self._implementation_task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scheduler task."""
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        """Gracefully stop current work and scheduler."""
        await self.stop(wait_for_current_task=True)

        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

    async def start_research(self) -> dict[str, Any]:
        """Start a research cycle from idle state."""
        async with self._lock:
            state = get_agent_state(self._conn)
            if state["status"] != "idle":
                return {"started": False, "status": state["status"]}

            self._cancel_event = asyncio.Event()
            await self._transition("researching", "Research cycle started")
            self._cycle_task = asyncio.create_task(self._run_research_cycle())
            return {"started": True, "status": "researching"}

    async def stop(self, *, wait_for_current_task: bool = False) -> dict[str, Any]:
        """Stop any active phase and return to idle."""
        self._cancel_event.set()

        timeout: float | None = None if wait_for_current_task else 10
        await self._wait_for_task(self._cycle_task, timeout=timeout)
        await self._wait_for_task(self._implementation_task, timeout=timeout)

        await self._transition("idle", "Agent stopped by user")
        return {"status": "idle"}

    def get_status(self) -> dict[str, Any]:
        """Return current state and scheduler metadata."""
        state = get_agent_state(self._conn)
        last_research_raw = get_setting(self._conn, "last_research_at")
        interval_hours = self._research_interval_hours()
        current_idea_id = state["current_idea_id"]
        current_idea_title = None
        if current_idea_id:
            current_idea = get_idea_by_id(self._conn, current_idea_id)
            current_idea_title = (
                str(current_idea["title"]) if current_idea is not None else None
            )
        return {
            "status": state["status"],
            "current_idea_id": current_idea_id,
            "current_idea_title": current_idea_title,
            "last_research_at": int(last_research_raw) if last_research_raw else None,
            "research_interval_hours": interval_hours,
        }

    async def trigger_implementation(self, idea_id: str) -> dict[str, Any]:
        """Transition to implementing for an approved idea, then back to idle."""
        async with self._lock:
            state = get_agent_state(self._conn)
            if state["status"] != "idle":
                return {"started": False, "status": state["status"]}

            self._cancel_event = asyncio.Event()
            update_idea_status(self._conn, idea_id, "approved")
            approved = get_idea_by_id(self._conn, idea_id)
            if approved is not None:
                await emit_idea_update(approved)
            set_agent_status(self._conn, "implementing", current_idea_id=idea_id)
            update_idea_status(self._conn, idea_id, "implementing")
            await self._log(
                f"Implementation triggered for approved idea {idea_id}",
                category="implementation",
            )
            updated = get_idea_by_id(self._conn, idea_id)
            if updated is not None:
                await emit_idea_update(updated)
            await emit_status("implementing", current_idea_id=idea_id)
            self._implementation_task = asyncio.create_task(
                self._run_implementation(idea_id)
            )
            return {"started": True, "status": "implementing"}

    async def _run_research_cycle(self) -> None:
        """Execute researching -> synthesizing -> idle."""
        try:
            rss_stats = await run_rss_fetch(self._conn)
            await self._log(
                "RSS fetch complete: "
                f"{rss_stats['articles_inserted']} new articles from "
                f"{rss_stats['feeds_succeeded']}/{rss_stats['feeds_attempted']} feeds",
                category="research",
            )

            if self._cancel_event.is_set():
                return

            analysis_stats = await run_analysis(
                self._conn,
                self._config,
                self._cancel_event,
                self._on_codex_output,
            )
            await self._log(
                "Codex analysis complete: "
                f"scored={analysis_stats['scored']}, "
                f"contexts={analysis_stats['contexts_added']}, "
                f"suggested_feeds={analysis_stats['feeds_suggested']}",
                category="research",
            )
            set_setting(self._conn, "last_research_at", str(int(time.time())))

            if self._cancel_event.is_set():
                return

            await self._transition("synthesizing", "Starting Codex synthesis")
            created = await run_synthesis(self._conn, cancel_event=self._cancel_event)
            await self._log(
                f"Synthesis complete: created {len(created)} pending ideas",
                category="synthesis",
            )
            for idea in created:
                await emit_idea_update(idea)
        except asyncio.CancelledError:
            raise
        except SynthesisError as exc:
            await self._log(
                f"Synthesis failed: {exc}",
                level="error",
                category="synthesis",
            )
        except Exception as exc:
            await self._log(
                f"Research cycle failed: {exc}",
                level="error",
                category="research",
            )
        finally:
            await self._transition("idle", "Research cycle finished")

    async def _scheduler_loop(self) -> None:
        """Run scheduled research while the agent remains idle."""
        while True:
            await asyncio.sleep(30)
            state = get_agent_state(self._conn)
            if state["status"] != "idle":
                continue

            interval = self._research_interval_hours() * 3600
            last_research = int(get_setting(self._conn, "last_research_at") or "0")
            now = int(time.time())
            if now - last_research < interval:
                continue

            await self._log("Scheduled research trigger fired", category="state")
            await self.start_research()

    async def _run_implementation(self, idea_id: str) -> None:
        """Execute approved idea build and then return the agent to idle."""
        try:
            result = await build_approved_idea(
                self._conn,
                idea_id,
                cancel_event=self._cancel_event,
                on_output=self._on_implementation_output,
                on_log=self._on_implementation_log,
            )
            if result.success:
                await self._log(
                    f"Idea {idea_id} marked completed at {result.project_path}",
                    category="implementation",
                )
            else:
                await self._log(
                    f"Idea {idea_id} failed: {result.error}",
                    level="error",
                    category="implementation",
                )
        except Exception as exc:
            update_idea_status(
                self._conn,
                idea_id,
                "failed",
                decline_reason=f"Unexpected implementation error: {exc}",
            )
            await self._log(
                f"Implementation failed unexpectedly: {exc}",
                level="error",
                category="implementation",
            )
        finally:
            updated = get_idea_by_id(self._conn, idea_id)
            if updated is not None:
                await emit_idea_update(updated)
            await self._transition(
                "idle",
                "Implementation cycle finished",
                current_idea_id=None,
            )

    async def _transition(
        self,
        status: str,
        message: str,
        *,
        current_idea_id: str | None = None,
    ) -> None:
        """Persist state transition and broadcast status/log events."""
        set_agent_status(self._conn, status, current_idea_id=current_idea_id)
        await self._log(message, category="state")
        await emit_status(status, current_idea_id=current_idea_id)

    async def _log(
        self,
        message: str,
        *,
        level: str = "info",
        category: str = "system",
    ) -> None:
        add_log(self._conn, message, level=level, category=category)
        await emit_log(message, level=level, category=category)

    def _research_interval_hours(self) -> int:
        configured = get_setting(self._conn, "research_interval_hours")
        if configured is None:
            return self._config.research_interval_hours

        try:
            parsed = int(configured)
        except ValueError:
            return self._config.research_interval_hours
        return max(parsed, 1)

    def _on_codex_output(self, line: str) -> None:
        """Forward live Codex output into the agent log."""
        add_log(self._conn, line, category="research")
        asyncio.create_task(emit_log(line, category="research"))

    def _on_implementation_output(self, line: str) -> None:
        """Forward live implementation output into the agent log."""
        add_log(self._conn, line, category="implementation")
        asyncio.create_task(emit_log(line, category="implementation"))

    def _on_implementation_log(self, message: str, level: str) -> None:
        """Log implementation lifecycle messages from the builder."""
        add_log(self._conn, message, level=level, category="implementation")
        asyncio.create_task(emit_log(message, level=level, category="implementation"))

    async def _wait_for_task(
        self,
        task: asyncio.Task[None] | None,
        *,
        timeout: float | None,
    ) -> None:
        """Wait for a task to finish with graceful cancellation fallback."""
        if task is None or task.done():
            return
        try:
            if timeout is None:
                await task
            else:
                await asyncio.wait_for(task, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
