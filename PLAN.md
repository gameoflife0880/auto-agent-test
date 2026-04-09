# auto-agent-test — Implementation Plan

## Context

Build an autonomous agent system where **Codex CLI (headless/non-interactive mode)** researches news, synthesizes project ideas based on user-defined focus tags, and implements approved ideas. All controlled via a minimalistic web dashboard with real-time streaming logs. Local-only, single-user, MVP scope.

Claude/Codex builds this system. Codex headless is the runtime agent. No API credits needed — Codex headless uses authentication-based quota, not API keys.

---

## Architecture

```
Dashboard (FastAPI + WebSocket)
         │
    Agent Brain (Python)
    ┌────┴──────────────────────────┐
    │                               │
RSS Fetcher                   Codex CLI (headless)
(our Python code)             invoked by brain per phase:
    │                          • research: analyze articles, web search (built-in)
    │                          • synthesis: generate ideas from news + tags
    │                          • implementation: build projects in projects/
    │                               │
    └───────────┬───────────────────┘
             SQLite DB
```

**Key simplifications:** No LM Studio, no Gemma, no SearXNG, no Docker, no custom tool executor, no scaffolder. Codex has built-in file I/O, shell access, and web search. Our backend is thin: RSS fetcher + dashboard + Codex orchestration.

---

## File Structure

```
auto-agent-test/
    config.yaml                    # Static config (ports, limits)
    pyproject.toml
    auto_agent/
        __init__.py
        __main__.py                # Entry point: uvicorn
        config.py                  # Frozen dataclasses, YAML loading
        db.py                      # Schema, migrations, CRUD helpers
        normalize.py               # URL normalization (ported from news-fetcher)
        server.py                  # FastAPI app, lifespan, mount routers + static
        agent/
            __init__.py
            brain.py               # State machine, cancellation, background loop
            codex.py               # Codex headless invocation + output parsing
            research.py            # RSS fetch (our code) + Codex analysis
            synthesis.py           # Codex-driven idea generation
            builder.py             # Codex-driven project implementation
        routes/
            __init__.py
            status.py              # GET/POST /api/status
            news.py                # GET /api/news
            ideas.py               # GET/POST /api/ideas/*
            tags.py                # GET/POST/DELETE /api/tags
            feeds.py               # GET/POST/DELETE /api/feeds
            config.py              # GET/POST /api/config (synthesis prompt, settings)
            ws.py                  # Single WebSocket /ws/events
        static/
            index.html             # Dashboard SPA (vanilla JS + Bootstrap-like)
    projects/                      # Shared folder for finished projects
    tests/
        test_smoke.py
        test_codex.py              # Codex invocation tests
        test_db.py
```

**Removed vs previous plan:** `tools/` (executor, definitions, search, news, file_ops), `scaffolder/` (writer, runner, validator). All replaced by `agent/codex.py`.

---

## Config File (config.yaml) — Static Only

```yaml
database: data/agent.db
log_file: logs/agent.log

server:
  host: 0.0.0.0
  port: 5000

research:
  max_searches_per_run: 10

schedule:
  research_interval_hours: 24     # daily by default

projects_dir: projects
```

Runtime-editable settings (synthesis prompt, research interval) also stored in DB `settings` table and editable from dashboard. DB values override YAML defaults. No LM Studio or SearXNG config — Codex handles all LLM and web search internally.

---

## Database Schema (single SQLite)

```sql
CREATE TABLE articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    content_is_full INTEGER NOT NULL CHECK (content_is_full IN (0, 1)),
    published INTEGER,
    relevance_score REAL DEFAULT 0.0,
    matched_tags TEXT DEFAULT '[]',       -- JSON array of tag labels
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE (source, title)
);
CREATE INDEX idx_articles_created ON articles(created_at DESC);

CREATE TABLE tags (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('interest', 'constraint')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE feeds (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    max_items INTEGER NOT NULL DEFAULT 10,
    active INTEGER NOT NULL DEFAULT 1,
    discovered_by TEXT DEFAULT 'user',    -- 'user' or 'agent'
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE ideas (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    tech_stack TEXT NOT NULL DEFAULT '[]',           -- JSON array
    why_now TEXT NOT NULL,
    existing_alternatives TEXT DEFAULT '',
    effort_estimate TEXT NOT NULL,
    inspired_by TEXT DEFAULT '[]',                   -- JSON array of article IDs
    matched_tags TEXT DEFAULT '[]',                  -- JSON array of tag labels
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'declined', 'implementing', 'completed', 'failed')),
    decline_reason TEXT DEFAULT '',
    project_path TEXT DEFAULT '',                    -- path in projects/ once built
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE agent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    level TEXT NOT NULL DEFAULT 'info',              -- info, warning, error
    category TEXT NOT NULL DEFAULT 'system',         -- research, synthesis, implementation, system, state
    message TEXT NOT NULL
);

CREATE TABLE agent_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK (status IN ('idle', 'researching', 'synthesizing', 'implementing')),
    current_idea_id TEXT DEFAULT NULL,
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (current_idea_id) REFERENCES ideas(id)
);
INSERT OR IGNORE INTO agent_state (id, status) VALUES (1, 'idle');

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

---

## Core Component: Codex Invocation (`agent/codex.py`)

Wraps Codex CLI headless mode for programmatic use:

```python
async def run_codex(
    prompt: str,
    working_dir: str | None = None,
    cancel_event: asyncio.Event | None = None,
    on_output: Callable[[str], None] | None = None,  # streams lines to WebSocket
    timeout: int = 300,
) -> CodexResult:
    """
    Invoke Codex CLI in headless/non-interactive mode.
    
    - Runs as async subprocess
    - Streams stdout lines to on_output callback (for real-time dashboard logs)
    - Checks cancel_event between output lines for graceful stopping
    - Returns CodexResult(stdout, stderr, exit_code)
    """
```

Key design decisions:
- Subprocess-based (not import) — Codex CLI is a standalone tool
- Async with line-by-line streaming — dashboard sees progress in real-time
- Cancellation via `cancel_event` — sends SIGTERM to subprocess for graceful stop
- Timeout per invocation (default 5 min, configurable per phase)

---

## Agent Phases

### Phase: Research

1. **RSS Fetch** (our Python code — no Codex needed):
   - Port `process_feed` / `run_fetch` from news-fetcher
   - Fetch all active feeds from `feeds` table
   - Store new articles in `articles` table with INSERT OR IGNORE dedup

2. **Analysis + Deep Search** (Codex headless):
   ```
   Prompt to Codex:
   "Here are recent news articles (JSON):
   {articles_json}
   
   Active interest tags: {interest_tags}
   Active constraint tags: {constraint_tags}
   Max web searches allowed: {max_searches}
   
   Tasks:
   1. Score each article's relevance (0.0-1.0) to the interest tags
   2. For high-relevance articles lacking detail, search the web for more context
   3. If you discover good RSS feeds related to interest tags, note them
   
   Output as JSON:
   {
     "scored_articles": [{"id": "...", "relevance_score": 0.8, "matched_tags": ["ai", "python"]}],
     "additional_context": [{"article_id": "...", "context": "..."}],
     "suggested_feeds": [{"source": "...", "url": "...", "reason": "..."}]
   }"
   ```
   - Codex uses its built-in web search for deeper research
   - Backend parses JSON output, updates articles, inserts suggested feeds (discovered_by='agent')

### Phase: Synthesis

```
Prompt to Codex:
"Analyzed articles (high relevance only, JSON):
{high_relevance_articles}

User interests: {interest_tags}
User constraints: {constraint_tags}
Synthesis instructions: {user_synthesis_prompt}

Previously declined ideas (do NOT re-propose similar):
{declined_ideas_with_reasons}

Tasks:
1. Identify trends, gaps, and opportunities from the articles
2. For each potential idea, search the web for existing solutions
3. Generate innovative project ideas that respect the constraint tags
4. For each idea, explain why it's timely and what alternatives exist

Output as JSON:
{
  "ideas": [
    {
      "title": "...",
      "description": "...",
      "tech_stack": ["python", "fastapi"],
      "why_now": "...",
      "existing_alternatives": "...",
      "effort_estimate": "2-3 days",
      "inspired_by": ["article_id_1", "article_id_2"],
      "matched_tags": ["ai-tools", "python-only"]
    }
  ]
}"
```
- Codex uses its built-in web search to find existing solutions
- Backend parses output, inserts ideas with `status='pending'`

### Phase: Implementation

When user approves an idea:

```
Prompt to Codex (working_dir = projects/{project_name}/):
"Implement this project as an MVP:

Title: {idea.title}
Description: {idea.description}
Tech stack: {idea.tech_stack}
Constraints: {constraint_tags}

Requirements:
- Write clean, working code
- Include tests that pass
- Include a README with setup and run instructions
- Project should be ready-to-go with a few commands
- Follow best practices for the chosen tech stack

Build the project in the current directory."
```
- Codex runs directly in the project folder with full file I/O
- It writes code, installs deps, runs tests, iterates — all natively
- Backend streams Codex output to dashboard via WebSocket
- On completion: backend marks idea as `completed`, sets `project_path`
- On failure (non-zero exit or timeout): marks as `failed`

---

## Agent Brain (`agent/brain.py`)

State machine with `asyncio.Event` for cancellation:

```
idle ──(user: start_research)──→ researching
researching ──(done)──→ synthesizing ──(done)──→ idle
idle ──(user: approve idea)──→ implementing ──(done)──→ idle
ANY STATE ──(user: stop)──→ idle (graceful: SIGTERM to Codex subprocess)
```

**Scheduled research:** Background asyncio task runs continuously. When agent is idle and `last_research + interval < now`, automatically triggers a full research → synthesis cycle. Default interval: 24 hours (daily). Configurable from dashboard Settings tab. The schedule respects agent state — won't interrupt an active implementation.

**Manual trigger:** User can also click "Start Research" in dashboard at any time to trigger immediately.

---

## API Routes

```
GET  /                              → Dashboard SPA
GET  /api/status                    → Agent state + health (Codex CLI available)
POST /api/status                    → Control: {action: "start_research"|"stop"}
                                      Research also runs automatically on schedule (configurable interval, default daily)
GET  /api/news                      → Articles (paginated, ?page=&source=&tag=)
GET  /api/ideas                     → Ideas (?status=pending|approved|declined|completed|failed)
POST /api/ideas/:id/approve         → Approve idea → triggers implementation
POST /api/ideas/:id/decline         → Decline with {reason}
GET  /api/tags                      → List tags
POST /api/tags                      → Add {label, type}
DELETE /api/tags/:id                → Remove tag
GET  /api/feeds                     → List feeds
POST /api/feeds                     → Add {source, url, max_items}
DELETE /api/feeds/:id               → Remove feed
GET  /api/config                    → Get settings (synthesis prompt, intervals)
POST /api/config                    → Update settings
WS   /ws/events                     → Single WebSocket for all real-time events
```

---

## Dashboard (`static/index.html`)

Vanilla JS + minimal Bootstrap-like CSS (Pico CSS or similar). Tabs:

1. **Status** — current state badge, start/stop buttons, health indicator (Codex CLI available)
2. **News** — article list with source, title, date, relevance score. Pagination.
3. **Ideas** — pending/approved/declined/completed/failed filter. Approve/decline buttons. Decline requires reason text.
4. **Projects** — completed projects list with folder path, status, test results
5. **Logs** — real-time streaming log viewer (WebSocket). Filter by category/level.
6. **Settings** — edit synthesis prompt (textarea), manage tags (add/remove), manage feeds (add/remove), adjust research schedule interval.

---

## Reused Code

| Source | What | Target |
|---|---|---|
| news-fetcher `normalize.py` | URL normalization + SHA-256 IDs | `auto_agent/normalize.py` (copy verbatim) |
| news-fetcher `fetch.py` | `process_feed`, `run_fetch`, entry parsing | `auto_agent/agent/research.py` (adapt for new schema) |
| news-fetcher `config.py` | Frozen dataclass pattern, YAML validation | `auto_agent/config.py` (same pattern) |
| news-fetcher `db.py` | SQLite connect, INSERT OR IGNORE, migrations | `auto_agent/db.py` (same pattern, new schema) |
| SearXNG `static/index.html` | Bootstrap layout, streaming UI patterns | `auto_agent/static/index.html` (adapt to WebSocket + tabs) |

---

## Build Order

### Phase 1: Foundation
- [ ] Create repo at ~/projects/auto-agent-test, pyproject.toml (deps: fastapi, uvicorn, httpx, feedparser, pyyaml, websockets)
- [ ] `config.py` — frozen dataclasses, YAML loading
- [ ] `db.py` — full schema, connect(), CRUD helpers for all tables
- [ ] `normalize.py` — copy from news-fetcher
- [ ] `server.py` — FastAPI app with lifespan, static mount, include routers
- [ ] `routes/status.py` — basic GET /api/status returns agent state
- [ ] Smoke test: server starts, returns status JSON

### Phase 2: Codex Integration (critical path — verify before proceeding)
- [ ] `agent/codex.py` — async subprocess wrapper for Codex headless
- [ ] Test: invoke Codex with simple prompt, capture output
- [ ] Test: verify streaming output line-by-line
- [ ] Test: verify cancellation via SIGTERM
- [ ] `test_codex.py` — automated smoke tests

### Phase 3: Research Pipeline
- [ ] `agent/research.py` — RSS fetch (port from news-fetcher) + Codex analysis invocation
- [ ] `agent/brain.py` — state machine, cancel_event, background task
- [ ] `routes/news.py`, `routes/feeds.py`, `routes/tags.py`
- [ ] Wire: POST /api/status {action: "start_research"} triggers full cycle
- [ ] Verify: articles appear in GET /api/news with relevance scores

### Phase 4: Dashboard
- [ ] `static/index.html` — all tabs, WebSocket connection, real-time logs
- [ ] `routes/ws.py` — WebSocket /ws/events endpoint
- [ ] News tab, feeds tab, tags tab, settings tab with working CRUD
- [ ] `routes/config.py` — settings read/write (synthesis prompt, intervals)
- [ ] Real data flowing through UI

### Phase 5: Synthesis
- [ ] `agent/synthesis.py` — prompt construction, Codex invocation, output parsing
- [ ] `routes/ideas.py` — list, approve, decline endpoints
- [ ] Dashboard: ideas tab with approve/decline + reason input
- [ ] Wire: brain.py auto-transitions research → synthesis → idle

### Phase 6: Implementation
- [ ] `agent/builder.py` — Codex invocation with working_dir set to projects/{name}/
- [ ] Wire: approve idea → brain transitions to implementing → Codex builds → idle
- [ ] Dashboard: projects tab showing status, folder path, Codex output
- [ ] Handle success (completed) and failure (failed) states

### Phase 7: Polish
- [ ] Health check: `codex --version` on GET /api/status
- [ ] Graceful shutdown on SIGTERM (propagate to running Codex subprocess)
- [ ] Error states in dashboard (red banners for Codex CLI not found)
- [ ] `test_smoke.py`, `test_db.py`

---

## Verification

1. Ensure Codex CLI is authenticated: `codex --version`
2. Run: `python -m auto_agent`
3. Open dashboard at `http://localhost:5000`
4. Add interest tag "ai-tools", constraint tag "python-only"
5. Click "Start Research" → verify articles appear in News tab with scores
6. Verify synthesis runs → ideas appear in Ideas tab
7. Approve an idea → verify Codex builds project in `projects/` folder
8. Stop agent mid-task → verify graceful cancellation
9. Verify completed project has tests, docs, runs successfully
