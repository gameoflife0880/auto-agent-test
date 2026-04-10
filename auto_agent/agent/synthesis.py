"""Synthesis pipeline for generating ideas from high-relevance research."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from typing import Any

from auto_agent.agent.codex import run_codex
from auto_agent.db import (
    add_log,
    get_active_tags,
    get_declined_ideas,
    get_high_relevance_articles,
    get_setting,
    get_idea_by_id,
    insert_idea,
)

DEFAULT_RELEVANCE_THRESHOLD = 0.6
DEFAULT_ARTICLE_LIMIT = 25
_DEFAULT_SYNTHESIS_PROMPT = (
    "Generate practical and original project ideas from the provided research context."
)


class SynthesisError(RuntimeError):
    """Raised when synthesis output cannot be parsed or persisted."""


async def run_synthesis(
    conn: sqlite3.Connection,
    *,
    relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    article_limit: int = DEFAULT_ARTICLE_LIMIT,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Run the synthesis flow and store generated ideas as pending."""
    high_relevance_articles = get_high_relevance_articles(
        conn,
        threshold=relevance_threshold,
        limit=article_limit,
    )
    interest_tags = get_active_tags(conn, tag_type="interest")
    constraint_tags = get_active_tags(conn, tag_type="constraint")
    user_prompt = get_setting(conn, "synthesis_prompt") or _DEFAULT_SYNTHESIS_PROMPT
    declined_ideas = get_declined_ideas(conn, limit=100)

    if not high_relevance_articles:
        add_log(
            conn,
            "Synthesis skipped: no high-relevance articles available.",
            category="synthesis",
        )
        return []

    prompt = _build_prompt(
        high_relevance_articles=high_relevance_articles,
        interest_tags=interest_tags,
        constraint_tags=constraint_tags,
        user_prompt=user_prompt,
        declined_ideas=declined_ideas,
    )

    add_log(
        conn,
        (
            "Starting synthesis with "
            f"{len(high_relevance_articles)} articles, "
            f"{len(interest_tags)} interest tags, "
            f"{len(constraint_tags)} constraint tags."
        ),
        category="synthesis",
    )

    result = await run_codex(prompt, cancel_event=cancel_event, timeout=420)
    if result.exit_code != 0:
        raise SynthesisError(
            f"Codex synthesis failed with exit code {result.exit_code}: {result.stderr}"
        )

    raw_ideas = _parse_idea_array(result.stdout)
    created_ideas = _persist_ideas(conn, raw_ideas)

    add_log(
        conn,
        f"Synthesis created {len(created_ideas)} pending ideas.",
        category="synthesis",
    )
    return created_ideas


def _build_prompt(
    *,
    high_relevance_articles: list[dict[str, Any]],
    interest_tags: list[str],
    constraint_tags: list[str],
    user_prompt: str,
    declined_ideas: list[dict[str, Any]],
) -> str:
    """Construct a structured prompt for Codex synthesis."""
    return (
        "You are an autonomous product strategist. "
        "Analyze the research context and propose project ideas as JSON only.\n\n"
        "High-relevance articles (JSON):\n"
        f"{json.dumps(high_relevance_articles, ensure_ascii=True, indent=2)}\n\n"
        "Active interest tags (JSON array):\n"
        f"{json.dumps(interest_tags, ensure_ascii=True)}\n\n"
        "Active constraint tags (JSON array):\n"
        f"{json.dumps(constraint_tags, ensure_ascii=True)}\n\n"
        "User synthesis instructions:\n"
        f"{user_prompt}\n\n"
        "Previously declined ideas (do not re-propose similar ideas) (JSON):\n"
        f"{json.dumps(declined_ideas, ensure_ascii=True, indent=2)}\n\n"
        "Tasks:\n"
        "1. Identify trends, gaps, and opportunities from the articles.\n"
        "2. Search the web for existing solutions for each proposed idea.\n"
        "3. Generate innovative ideas while respecting all constraint tags.\n"
        "4. Avoid ideas that overlap with declined ideas.\n"
        "5. Return only JSON: an array of idea objects with keys:\n"
        "   title (string),\n"
        "   description (string),\n"
        "   tech_stack (array of strings),\n"
        "   why_now (string),\n"
        "   existing_alternatives (string),\n"
        "   effort_estimate (string),\n"
        "   inspired_by (array of article ids),\n"
        "   matched_tags (array of strings).\n"
        "Output must be valid JSON and contain no markdown."
    )


def _parse_idea_array(raw_output: str) -> list[dict[str, Any]]:
    """Parse idea array from Codex output."""
    candidates = [raw_output.strip()]

    code_block_match = re.search(
        r"```(?:json)?\s*(\[.*?\])\s*```", raw_output, re.DOTALL
    )
    if code_block_match:
        candidates.append(code_block_match.group(1).strip())

    first_bracket = raw_output.find("[")
    last_bracket = raw_output.rfind("]")
    if 0 <= first_bracket < last_bracket:
        candidates.append(raw_output[first_bracket : last_bracket + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            idea_rows = [x for x in parsed if isinstance(x, dict)]
            return idea_rows

    raise SynthesisError("Codex synthesis output is not a valid JSON array of ideas.")


def _persist_ideas(
    conn: sqlite3.Connection,
    raw_ideas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert synthesized ideas into DB and return full created records."""
    created: list[dict[str, Any]] = []

    for raw in raw_ideas:
        title = str(raw.get("title", "")).strip()
        description = str(raw.get("description", "")).strip()
        why_now = str(raw.get("why_now", "")).strip()
        effort_estimate = str(raw.get("effort_estimate", "")).strip()
        if not (title and description and why_now and effort_estimate):
            continue

        tech_stack = _as_string_list(raw.get("tech_stack"))
        inspired_by = _as_string_list(raw.get("inspired_by"))
        matched_tags = _as_string_list(raw.get("matched_tags"))
        existing_alternatives = str(raw.get("existing_alternatives", "")).strip()

        idea_id = insert_idea(
            conn,
            title=title,
            description=description,
            why_now=why_now,
            effort_estimate=effort_estimate,
            tech_stack=tech_stack,
            existing_alternatives=existing_alternatives,
            inspired_by=inspired_by,
            matched_tags=matched_tags,
        )
        created_row = get_idea_by_id(conn, idea_id)
        if created_row is not None:
            created.append(created_row)

    return created


def _as_string_list(value: Any) -> list[str]:
    """Normalize list-like values to a clean list of strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
    return out
