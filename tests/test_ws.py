"""Concurrency tests for WebSocket event broadcasting."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from auto_agent.routes.ws import EventHub


class _BlockingSocket:
    """WebSocket test double that blocks inside send_json until released."""

    def __init__(
        self,
        entered: asyncio.Event,
        release: asyncio.Event,
        marker: dict[str, Any],
    ) -> None:
        self._entered = entered
        self._release = release
        self._marker = marker
        self.send_json = AsyncMock(side_effect=self._send_json)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        self._marker["current"] = self
        self._entered.set()
        await self._release.wait()


class TestEventHubConcurrency:
    """Regression tests for EventHub iteration and connect/disconnect behavior."""

    @pytest.mark.asyncio
    async def test_broadcast_with_disconnect_mid_iteration(self) -> None:
        """broadcast should not fail when client set is mutated during send."""
        hub = EventHub()
        entered = asyncio.Event()
        release = asyncio.Event()
        marker: dict[str, Any] = {}

        ws_a = _BlockingSocket(entered, release, marker)
        ws_b = _BlockingSocket(entered, release, marker)
        hub._clients = {cast(Any, ws_a), cast(Any, ws_b)}

        task = asyncio.create_task(hub.broadcast({"type": "log"}))
        await entered.wait()

        current = marker["current"]
        other = ws_a if current is ws_b else ws_b
        hub.disconnect(cast(Any, other))
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_connect_disconnect_idempotent(self) -> None:
        """connect/disconnect tolerate duplicates without throwing."""
        hub = EventHub()
        ws = MagicMock()
        ws.accept = AsyncMock()

        await hub.connect(ws)
        await hub.connect(ws)
        assert hub.client_count == 1

        hub.disconnect(ws)
        hub.disconnect(ws)
        assert hub.client_count == 0
