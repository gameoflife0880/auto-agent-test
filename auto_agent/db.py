"""SQLite database — schema bootstrap, connection helper, and CRUD functions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    content_is_full INTEGER NOT NULL CHECK (content_is_full IN (0, 1)),
    published INTEGER,
    relevance_score REAL DEFAULT 0.0,
    matched_tags TEXT DEFAULT '[]',
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE (source, title)
);
CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at DESC);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('interest', 'constraint')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS feeds (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    max_items INTEGER NOT NULL DEFAULT 10,
    active INTEGER NOT NULL DEFAULT 1,
    discovered_by TEXT DEFAULT 'user',
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    tech_stack TEXT NOT NULL DEFAULT '[]',
    why_now TEXT NOT NULL,
    existing_alternatives TEXT DEFAULT '',
    effort_estimate TEXT NOT NULL,
    inspired_by TEXT DEFAULT '[]',
    matched_tags TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'declined', 'implementing', 'completed', 'failed')),
    decline_reason TEXT DEFAULT '',
    project_path TEXT DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS agent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    level TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL DEFAULT 'system',
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK (status IN ('idle', 'researching', 'synthesizing', 'implementing')),
    current_idea_id TEXT DEFAULT NULL,
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (current_idea_id) REFERENCES ideas(id)
);
INSERT OR IGNORE INTO agent_state (id, status) VALUES (1, 'idle');

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (and bootstrap) the database, returning a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


# --- agent_state ---------------------------------------------------------- #


def get_agent_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the singleton agent state row as a dict."""
    row = conn.execute("SELECT * FROM agent_state WHERE id = 1").fetchone()
    return dict(row) if row else {"status": "idle", "current_idea_id": None}


def set_agent_status(
    conn: sqlite3.Connection,
    status: str,
    current_idea_id: str | None = None,
) -> None:
    """Update the agent state."""
    conn.execute(
        "UPDATE agent_state SET status = ?, current_idea_id = ?, "
        "updated_at = strftime('%s', 'now') WHERE id = 1",
        (status, current_idea_id),
    )
    conn.commit()


# --- articles ------------------------------------------------------------- #


def insert_article(
    conn: sqlite3.Connection,
    *,
    source: str,
    title: str,
    content: str = "",
    content_is_full: bool = False,
    published: int | None = None,
    relevance_score: float = 0.0,
    matched_tags: list[str] | None = None,
    article_id: str | None = None,
) -> str:
    """INSERT OR IGNORE an article; returns the id."""
    aid = article_id or _uid()
    conn.execute(
        "INSERT OR IGNORE INTO articles "
        "(id, source, title, content, content_is_full, published, "
        "relevance_score, matched_tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            aid,
            source,
            title,
            content,
            int(content_is_full),
            published,
            relevance_score,
            json.dumps(matched_tags or []),
        ),
    )
    conn.commit()
    return aid


def get_articles(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM articles ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_high_relevance_articles(
    conn: sqlite3.Connection,
    *,
    threshold: float,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return the highest relevance articles at or above *threshold*."""
    rows = conn.execute(
        "SELECT * FROM articles WHERE relevance_score > ? "
        "ORDER BY relevance_score DESC, created_at DESC LIMIT ?",
        (threshold, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- tags ----------------------------------------------------------------- #


def insert_tag(
    conn: sqlite3.Connection,
    *,
    label: str,
    tag_type: str,
    active: bool = True,
) -> str:
    tid = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO tags (id, label, type, active) VALUES (?, ?, ?, ?)",
        (tid, label, tag_type, int(active)),
    )
    conn.commit()
    return tid


def get_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM tags ORDER BY label").fetchall()
    return [dict(r) for r in rows]


def get_active_tags(
    conn: sqlite3.Connection,
    *,
    tag_type: str | None = None,
) -> list[str]:
    """Return active tag labels, optionally filtered by type."""
    if tag_type is None:
        rows = conn.execute(
            "SELECT label FROM tags WHERE active = 1 ORDER BY label"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT label FROM tags WHERE active = 1 AND type = ? ORDER BY label",
            (tag_type,),
        ).fetchall()
    return [str(r["label"]) for r in rows]


def delete_tag(conn: sqlite3.Connection, tag_id: str) -> bool:
    """Delete a tag by id. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    return cur.rowcount > 0


# --- feeds ---------------------------------------------------------------- #


def insert_feed(
    conn: sqlite3.Connection,
    *,
    source: str,
    url: str,
    max_items: int = 10,
    discovered_by: str = "user",
) -> str:
    fid = _uid()
    conn.execute(
        "INSERT OR IGNORE INTO feeds (id, source, url, max_items, discovered_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (fid, source, url, max_items, discovered_by),
    )
    conn.commit()
    return fid


def get_feeds(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM feeds ORDER BY source").fetchall()
    return [dict(r) for r in rows]


def delete_feed(conn: sqlite3.Connection, feed_id: str) -> bool:
    """Delete a feed by id. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    conn.commit()
    return cur.rowcount > 0


# --- ideas ---------------------------------------------------------------- #


def insert_idea(
    conn: sqlite3.Connection,
    *,
    title: str,
    description: str,
    why_now: str,
    effort_estimate: str,
    tech_stack: list[str] | None = None,
    existing_alternatives: str = "",
    inspired_by: list[str] | None = None,
    matched_tags: list[str] | None = None,
) -> str:
    iid = _uid()
    conn.execute(
        "INSERT INTO ideas "
        "(id, title, description, tech_stack, why_now, existing_alternatives, "
        "effort_estimate, inspired_by, matched_tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            iid,
            title,
            description,
            json.dumps(tech_stack or []),
            why_now,
            existing_alternatives,
            effort_estimate,
            json.dumps(inspired_by or []),
            json.dumps(matched_tags or []),
        ),
    )
    conn.commit()
    return iid


def get_ideas(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM ideas ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ideas WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_idea_by_id(conn: sqlite3.Connection, idea_id: str) -> dict[str, Any] | None:
    """Return one idea row by id, if present."""
    row = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    return dict(row) if row else None


def get_declined_ideas(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return previously declined ideas with reasons."""
    rows = conn.execute(
        "SELECT id, title, description, decline_reason "
        "FROM ideas WHERE status = 'declined' "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_idea_status(
    conn: sqlite3.Connection,
    idea_id: str,
    status: str,
    *,
    decline_reason: str = "",
    project_path: str = "",
) -> None:
    conn.execute(
        "UPDATE ideas SET status = ?, decline_reason = ?, project_path = ?, "
        "updated_at = strftime('%s', 'now') WHERE id = ?",
        (status, decline_reason, project_path, idea_id),
    )
    conn.commit()


# --- agent_log ------------------------------------------------------------ #


def add_log(
    conn: sqlite3.Connection,
    message: str,
    *,
    level: str = "info",
    category: str = "system",
) -> None:
    conn.execute(
        "INSERT INTO agent_log (level, category, message) VALUES (?, ?, ?)",
        (level, category, message),
    )
    conn.commit()


def get_logs(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- settings ------------------------------------------------------------- #


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
