"""Tests for the web dashboard — routes, config, data, and WebSocket."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient

from auto_agent.db import (
    add_log,
    connect,
    insert_article,
    insert_feed,
    insert_idea,
    insert_tag,
    set_setting,
)
from auto_agent.routes.ws import hub

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app_client(tmp_path: Any) -> Generator[TestClient, None, None]:
    """Create a TestClient backed by a temporary SQLite database."""
    db_path = tmp_path / "test.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"database: {db_path}\nserver:\n  host: 127.0.0.1\n  port: 5000\n"
        "schedule:\n  research_interval_hours: 24\n"
    )

    # Patch load_config to use our temp config.
    import auto_agent.server as srv

    original_load = srv.load_config

    def _patched_load(path: Any = None) -> Any:
        return original_load(str(config_path))

    srv.load_config = _patched_load  # type: ignore[assignment]

    try:
        with TestClient(srv.app) as client:
            yield client
    finally:
        srv.load_config = original_load  # type: ignore[assignment]


def _db(client: TestClient) -> sqlite3.Connection:
    """Get the db connection from the running app."""
    return client.app.state.db  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Status routes (existing, sanity check)
# --------------------------------------------------------------------------- #


class TestStatusRoutes:
    def test_get_status(self, app_client: TestClient) -> None:
        r = app_client.get("/api/status")
        assert r.status_code == 200
        assert r.json()["status"] == "idle"

    def test_start_research(self, app_client: TestClient) -> None:
        r = app_client.post("/api/status", json={"action": "start_research"})
        assert r.status_code == 200
        assert r.json()["status"] == "researching"

    def test_stop(self, app_client: TestClient) -> None:
        app_client.post("/api/status", json={"action": "start_research"})
        r = app_client.post("/api/status", json={"action": "stop"})
        assert r.json()["status"] == "idle"


# --------------------------------------------------------------------------- #
# Config routes
# --------------------------------------------------------------------------- #


class TestConfigRoutes:
    def test_get_config_defaults(self, app_client: TestClient) -> None:
        r = app_client.get("/api/config")
        assert r.status_code == 200
        d = r.json()
        assert d["synthesis_prompt"] == ""
        assert d["research_interval_hours"] == 24

    def test_post_config_prompt(self, app_client: TestClient) -> None:
        app_client.post("/api/config", json={"synthesis_prompt": "Test prompt"})
        r = app_client.get("/api/config")
        assert r.json()["synthesis_prompt"] == "Test prompt"

    def test_post_config_interval(self, app_client: TestClient) -> None:
        app_client.post("/api/config", json={"research_interval_hours": 12})
        r = app_client.get("/api/config")
        assert r.json()["research_interval_hours"] == 12


# --------------------------------------------------------------------------- #
# Articles / News routes
# --------------------------------------------------------------------------- #


class TestArticleRoutes:
    def test_empty(self, app_client: TestClient) -> None:
        r = app_client.get("/api/articles")
        assert r.json()["items"] == []

    def test_pagination(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        for i in range(5):
            insert_article(conn, source="src", title=f"Article {i}")
        r = app_client.get("/api/articles?limit=2&offset=0")
        d = r.json()
        assert len(d["items"]) == 2
        assert d["has_more"] is True

    def test_no_more(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        insert_article(conn, source="s", title="only one")
        r = app_client.get("/api/articles?limit=20&offset=0")
        assert r.json()["has_more"] is False


# --------------------------------------------------------------------------- #
# Ideas routes
# --------------------------------------------------------------------------- #


class TestIdeaRoutes:
    def test_empty(self, app_client: TestClient) -> None:
        r = app_client.get("/api/ideas")
        assert r.json()["items"] == []

    def test_filter_by_status(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        insert_idea(conn, title="A", description="d", why_now="w", effort_estimate="1d")
        insert_idea(conn, title="B", description="d", why_now="w", effort_estimate="1d")
        # Approve one
        ideas = app_client.get("/api/ideas").json()["items"]
        app_client.put(f"/api/ideas/{ideas[0]['id']}", json={"status": "approved"})
        r = app_client.get("/api/ideas?status=pending")
        assert len(r.json()["items"]) == 1

    def test_decline_with_reason(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        iid = insert_idea(
            conn, title="X", description="d", why_now="w", effort_estimate="1d"
        )
        r = app_client.put(
            f"/api/ideas/{iid}",
            json={"status": "declined", "decline_reason": "not relevant"},
        )
        assert r.json()["ok"] is True

    def test_invalid_status(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        iid = insert_idea(
            conn, title="Y", description="d", why_now="w", effort_estimate="1d"
        )
        r = app_client.put(f"/api/ideas/{iid}", json={"status": "garbage"})
        assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Tags routes
# --------------------------------------------------------------------------- #


class TestTagRoutes:
    def test_crud(self, app_client: TestClient) -> None:
        # Create
        r = app_client.post(
            "/api/tags", json={"label": "python", "tag_type": "interest"}
        )
        assert r.json()["ok"] is True
        tid = r.json()["id"]

        # List
        r = app_client.get("/api/tags")
        assert len(r.json()["items"]) == 1

        # Delete
        r = app_client.delete(f"/api/tags/{tid}")
        assert r.json()["ok"] is True
        r = app_client.get("/api/tags")
        assert len(r.json()["items"]) == 0

    def test_invalid_type(self, app_client: TestClient) -> None:
        r = app_client.post("/api/tags", json={"label": "x", "tag_type": "bad"})
        assert r.status_code == 400

    def test_delete_missing(self, app_client: TestClient) -> None:
        r = app_client.delete("/api/tags/nonexistent")
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Feeds routes
# --------------------------------------------------------------------------- #


class TestFeedRoutes:
    def test_crud(self, app_client: TestClient) -> None:
        r = app_client.post(
            "/api/feeds", json={"source": "HN", "url": "https://example.com/feed"}
        )
        assert r.json()["ok"] is True
        fid = r.json()["id"]

        r = app_client.get("/api/feeds")
        assert len(r.json()["items"]) == 1

        r = app_client.delete(f"/api/feeds/{fid}")
        assert r.json()["ok"] is True

    def test_delete_missing(self, app_client: TestClient) -> None:
        r = app_client.delete("/api/feeds/nonexistent")
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Logs routes
# --------------------------------------------------------------------------- #


class TestLogRoutes:
    def test_empty(self, app_client: TestClient) -> None:
        r = app_client.get("/api/logs")
        assert r.json()["items"] == []

    def test_returns_logs(self, app_client: TestClient) -> None:
        conn = _db(app_client)
        add_log(conn, "hello", level="info", category="system")
        add_log(conn, "oops", level="error", category="research")
        r = app_client.get("/api/logs")
        assert len(r.json()["items"]) == 2


# --------------------------------------------------------------------------- #
# Dashboard index
# --------------------------------------------------------------------------- #


class TestDashboard:
    def test_index_serves_html(self, app_client: TestClient) -> None:
        r = app_client.get("/")
        assert r.status_code == 200
        assert "auto-agent" in r.text


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


class TestWebSocket:
    def test_ws_connect_and_receive_ping(self, app_client: TestClient) -> None:
        with app_client.websocket_connect("/ws/events") as ws:
            # The server sends a ping after the timeout; we can also just
            # verify connection works by sending a message.
            ws.send_text("hello")
            # Read back a response (ping or event).
            data = ws.receive_json(mode="text")
            assert "type" in data
