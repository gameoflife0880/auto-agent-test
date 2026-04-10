"""Implementation pipeline that builds approved ideas with Codex CLI."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_agent.agent.codex import CodexNotFoundError, CodexTimeoutError, run_codex
from auto_agent.db import get_active_tags, get_idea_by_id, update_idea_status

_DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass
class BuildResult:
    """Final status of one idea implementation run."""

    success: bool
    project_path: str
    error: str = ""


def _slugify(value: str) -> str:
    """Create a filesystem-safe directory slug from a free-form title."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "untitled-project"


def _json_list(value: Any) -> list[str]:
    """Normalize text/json list-ish values into a clean list of strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _next_available_project_dir(projects_root: Path, slug: str) -> Path:
    """Pick a non-conflicting directory path under *projects_root*."""
    candidate = projects_root / slug
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        next_candidate = projects_root / f"{slug}-{suffix}"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def _display_project_path(project_root: Path, project_dir: Path) -> str:
    """Return the persisted project path (prefer repo-relative)."""
    try:
        return str(project_dir.relative_to(project_root.parent))
    except ValueError:
        return str(project_dir)


def _build_prompt(
    *,
    title: str,
    description: str,
    tech_stack: list[str],
    constraint_tags: list[str],
) -> str:
    """Build a codex prompt for project implementation."""
    return (
        "Implement the following software project idea in the current working "
        "directory.\n\n"
        f"Idea title: {title}\n"
        f"Idea description: {description}\n"
        f"Preferred tech stack: {json.dumps(tech_stack, ensure_ascii=True)}\n"
        f"Constraint tags: {json.dumps(constraint_tags, ensure_ascii=True)}\n\n"
        "Requirements:\n"
        "1. Produce clean, maintainable code.\n"
        "2. Include passing automated tests.\n"
        "3. Include a concise README with setup and usage instructions.\n"
        "4. Leave the project in a ready-to-run state.\n"
        "5. If decisions are needed, choose pragmatic defaults.\n"
        "6. Finish by summarizing what was implemented and how to run tests."
    )


async def build_approved_idea(
    conn: sqlite3.Connection,
    idea_id: str,
    *,
    on_output: Callable[[str], None] | None = None,
    on_log: Callable[[str, str], None] | None = None,
    projects_root: Path | None = None,
    cancel_event: asyncio.Event | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> BuildResult:
    """Build one approved idea and persist completed/failed status in DB."""
    idea = get_idea_by_id(conn, idea_id)
    if idea is None:
        raise ValueError(f"Idea {idea_id} not found")

    project_root = projects_root or Path(__file__).resolve().parents[2] / "projects"
    project_root.mkdir(parents=True, exist_ok=True)

    project_dir = _next_available_project_dir(
        project_root, _slugify(str(idea["title"]))
    )
    project_dir.mkdir(parents=True, exist_ok=True)
    project_path = _display_project_path(project_root, project_dir)

    tech_stack = _json_list(idea.get("tech_stack"))
    constraint_tags = get_active_tags(conn, tag_type="constraint")
    prompt = _build_prompt(
        title=str(idea["title"]),
        description=str(idea["description"]),
        tech_stack=tech_stack,
        constraint_tags=constraint_tags,
    )

    if on_log is not None:
        on_log(f"Starting implementation for idea '{idea['title']}'", "info")
        on_log(f"Project directory prepared at {project_path}", "info")

    def _on_codex_output(line: str) -> None:
        if on_output is not None:
            on_output(line)

    try:
        result = await run_codex(
            prompt,
            working_dir=project_path,
            cancel_event=cancel_event,
            on_output=_on_codex_output,
            timeout=timeout_seconds,
        )
    except CodexNotFoundError as exc:
        error = str(exc)
        update_idea_status(
            conn,
            idea_id,
            "failed",
            decline_reason=error,
            project_path=project_path,
        )
        if on_log is not None:
            on_log(error, "error")
        return BuildResult(success=False, project_path=project_path, error=error)
    except CodexTimeoutError as exc:
        error = str(exc)
        update_idea_status(
            conn,
            idea_id,
            "failed",
            decline_reason=error,
            project_path=project_path,
        )
        if on_log is not None:
            on_log(error, "error")
        return BuildResult(success=False, project_path=project_path, error=error)

    if result.exit_code != 0:
        stderr = result.stderr.strip()
        error = (
            f"Codex exited with code {result.exit_code}: {stderr}"
            if stderr
            else f"Codex exited with code {result.exit_code}"
        )
        update_idea_status(
            conn,
            idea_id,
            "failed",
            decline_reason=error,
            project_path=project_path,
        )
        if on_log is not None:
            on_log(error, "error")
        return BuildResult(success=False, project_path=project_path, error=error)

    update_idea_status(conn, idea_id, "completed", project_path=project_path)
    if on_log is not None:
        on_log(f"Implementation completed successfully at {project_path}", "info")
    return BuildResult(success=True, project_path=project_path)
