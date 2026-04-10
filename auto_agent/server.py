"""FastAPI application with lifespan-managed database connection."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from auto_agent.config import load_config
from auto_agent.db import connect
from auto_agent.routes import config, data, status, ws

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: load config, open DB.  Shutdown: close DB."""
    cfg = load_config()
    conn = connect(cfg.database)
    app.state.config = cfg
    app.state.db = conn
    yield
    conn.close()


app = FastAPI(title="auto-agent", lifespan=lifespan)

app.include_router(status.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(ws.router)

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the dashboard SPA."""
    index_file = _STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>auto-agent</h1><p>No dashboard yet.</p>")
