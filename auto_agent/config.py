"""Application configuration — frozen dataclasses loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FeedConfig:
    source: str
    url: str
    max_items: int = 10


@dataclass(frozen=True)
class Config:
    database: str = "data/agent.db"
    log_file: str = "logs/agent.log"
    server_host: str = "0.0.0.0"
    server_port: int = 5000
    max_searches_per_run: int = 10
    research_interval_hours: int = 24
    projects_dir: str = "projects"
    feeds: tuple[FeedConfig, ...] = ()


def _parse_config(raw: dict[str, Any]) -> Config:
    """Build a Config from the raw YAML dict."""
    feeds_raw: list[dict[str, Any]] = raw.get("feeds", [])
    feeds = tuple(FeedConfig(**f) for f in feeds_raw)

    server: dict[str, Any] = raw.get("server", {})
    research: dict[str, Any] = raw.get("research", {})
    schedule: dict[str, Any] = raw.get("schedule", {})

    return Config(
        database=raw.get("database", Config.database),
        log_file=raw.get("log_file", Config.log_file),
        server_host=server.get("host", Config.server_host),
        server_port=server.get("port", Config.server_port),
        max_searches_per_run=research.get(
            "max_searches_per_run", Config.max_searches_per_run
        ),
        research_interval_hours=schedule.get(
            "research_interval_hours", Config.research_interval_hours
        ),
        projects_dir=raw.get("projects_dir", Config.projects_dir),
        feeds=feeds,
    )


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load configuration from a YAML file."""
    with open(path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    return _parse_config(raw)
