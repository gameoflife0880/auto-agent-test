"""Agent status routes — GET and POST /api/status."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class StatusAction(BaseModel):
    action: str  # "start_research" | "stop"


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Return current agent state and Codex CLI health."""
    state = request.app.state.brain.get_status()
    codex_health = await _codex_cli_health()
    state["codex_cli"] = codex_health["status"]
    state["codex_health"] = codex_health
    return state


@router.post("/status")
async def post_status(request: Request, body: StatusAction) -> dict[str, Any]:
    """Control the agent — start research or stop."""
    brain = request.app.state.brain
    if body.action == "start_research":
        result = await brain.start_research()
        return {"status": result["status"], "started": result["started"]}
    if body.action == "stop":
        return await brain.stop()
    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")


async def _codex_cli_health() -> dict[str, str | None]:
    """Probe Codex CLI availability by running `codex --version`."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "codex",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "status": "not_found",
            "version": None,
            "error": "Codex CLI not found on PATH",
        }

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
    except TimeoutError:
        proc.terminate()
        await proc.wait()
        return {
            "status": "error",
            "version": None,
            "error": "Timed out running codex --version",
        }

    output = stdout.decode(errors="replace").strip()
    if proc.returncode == 0:
        return {"status": "ok", "version": output or None, "error": None}

    error = stderr.decode(errors="replace").strip() or "codex --version failed"
    return {"status": "error", "version": None, "error": error}
