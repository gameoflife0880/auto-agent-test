"""Smoke tests for server startup and status endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _write_config(tmp_path: Any) -> str:
    """Create a temporary config file and return its path."""
    db_path = tmp_path / "smoke.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"database: {db_path}\nserver:\n  host: 127.0.0.1\n  port: 5000\n"
        "schedule:\n  research_interval_hours: 24\n",
        encoding="utf-8",
    )
    return str(config_path)


def test_server_starts_and_status_endpoint_reports_health(tmp_path: Any) -> None:
    """Server starts and GET /api/status returns state plus Codex health."""
    import auto_agent.server as srv

    config_path = _write_config(tmp_path)
    original_load = srv.load_config

    def _patched_load(path: Any = None) -> Any:
        return original_load(config_path)

    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"codex 1.2.3\n", b""))
    fake_proc.returncode = 0

    srv.load_config = _patched_load  # type: ignore[assignment]
    try:
        with patch(
            "auto_agent.routes.status.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            with TestClient(srv.app) as client:
                response = client.get("/api/status")
    finally:
        srv.load_config = original_load  # type: ignore[assignment]

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "idle"
    assert payload["codex_cli"] == "ok"
    assert payload["codex_health"]["status"] == "ok"
    assert payload["codex_health"]["version"] == "codex 1.2.3"
