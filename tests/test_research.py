"""Tests for research prompt construction helpers."""

from __future__ import annotations

from auto_agent.agent.research import _build_analysis_prompt


def test_build_analysis_prompt_uses_real_newlines() -> None:
    prompt = _build_analysis_prompt(
        articles=[
            {
                "id": "a1",
                "source": "example",
                "title": "Test Title",
                "content": "Body",
                "published": 123456,
            }
        ],
        interest_tags=["ai"],
        constraint_tags=["privacy"],
        max_searches=3,
    )

    assert "\n\n" in prompt
    assert "\\n" not in prompt
