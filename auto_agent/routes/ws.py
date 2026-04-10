"""WebSocket endpoint — broadcast real-time events to connected clients."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class EventHub:
    """Simple in-memory pub/sub for WebSocket event broadcasting."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        """Accept and register a WebSocket connection."""
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send an event to every connected client."""
        payload = json.dumps(event)
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def close_all(self) -> None:
        """Close and remove all currently connected websocket clients."""
        clients = list(self._clients)
        self._clients.clear()
        for ws in clients:
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception:
                continue


# Module-level hub — imported by other modules to broadcast events.
hub = EventHub()


async def emit_log(
    message: str,
    *,
    level: str = "info",
    category: str = "system",
) -> None:
    """Convenience: broadcast a log event to all WebSocket clients."""
    await hub.broadcast(
        {
            "type": "log",
            "timestamp": int(time.time()),
            "data": {"level": level, "category": category, "message": message},
        }
    )


async def emit_status(status: str, current_idea_id: str | None = None) -> None:
    """Broadcast a status change event."""
    await hub.broadcast(
        {
            "type": "status",
            "timestamp": int(time.time()),
            "data": {"status": status, "current_idea_id": current_idea_id},
        }
    )


async def emit_idea_update(idea: dict[str, Any]) -> None:
    """Broadcast an idea update event."""
    await hub.broadcast(
        {
            "type": "idea_update",
            "timestamp": int(time.time()),
            "data": idea,
        }
    )


_PING_INTERVAL = 30  # seconds


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    """Single WebSocket endpoint — streams all events to the client."""
    await hub.connect(ws)
    try:
        while True:
            # Wait for client messages (keeps connection alive).
            # Clients can send pings; we just read to detect disconnects.
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=_PING_INTERVAL)
            except asyncio.TimeoutError:
                # Send a ping to keep the connection alive.
                await ws.send_json({"type": "ping", "timestamp": int(time.time())})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
