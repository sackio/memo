# memo

Lightweight HTTP API and MCP server for semantic document storage. Stores memos with vector embeddings in SQLite ([sqlite-vec](https://github.com/asg017/sqlite-vec)) and retrieves them by semantic similarity. Supports per-call `db_path` overrides for project-local databases.

## Stack

- **Python 3.12** + **uv**
- **FastAPI** + **uvicorn**
- **FastMCP** (streamable-HTTP transport at `/mcp/`)
- **sqlite-vec** for vector storage and cosine similarity search
- **tiktoken** (`cl100k_base`) for token counting at store time
- **AsyncOpenAI** → [OpenRouter](https://openrouter.ai) for embeddings

---

## Quick Start

```bash
cp .env.example .env
# edit .env and set OPENROUTER_API_KEY

docker compose up -d
curl http://localhost:8000/health
```

---

## Configuration

All settings are read from environment variables (or `.env`):

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | HTTP server port |
| `OPENROUTER_API_KEY` | *(required)* | API key for OpenRouter |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model |
| `EMBEDDING_DIMENSIONS` | `1536` | Embedding dimensions |
| `DEFAULT_DB_PATH` | `~/.memo/memo.db` | Default SQLite database path |

In Docker, `DEFAULT_DB_PATH` is set to `/data/memo.db` (mounted volume).

---

## HTTP API

### Health

```
GET /health
→ {"status": "ok"}
```

### Memo schema

```json
{
  "id": "uuid",
  "content": "...",
  "title": "...",
  "tags": ["tag1", "tag2"],
  "metadata": {},
  "token_count": 42,
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0
}
```

`token_count` is computed automatically from `content` at store and update time.

### Store

```
POST /documents
Content-Type: application/json

{
  "content": "SQLite is an embedded relational database",
  "title": "SQLite overview",       // optional
  "tags": ["db", "sqlite"],         // optional
  "metadata": {"source": "notes"},  // optional
  "db_path": "/path/to/proj"        // optional — see db_path below
}

→ {"id": "<uuid>"}
```

### Update

```
PATCH /documents/{id}
Content-Type: application/json

{
  "content": "...",   // optional — triggers re-embed + token recount
  "title": "...",     // optional
  "tags": [...],      // optional — replaces existing tags
  "metadata": {...},  // optional — replaces existing metadata
  "db_path": "..."    // optional
}

→ {memo}
```

Only provided fields are changed. Returns the updated memo, or 404 if not found.

### Get

```
GET /documents/{id}?db_path=...
→ {memo}
```

### Delete

```
DELETE /documents/{id}?db_path=...
→ {"deleted": true}
```

### List

```
GET /documents
  ?tags=db&tags=sqlite    // any-match tag filter
  &after=1700000000.0     // created_at >= (Unix timestamp)
  &before=1800000000.0    // created_at <= (Unix timestamp)
  &min_tokens=10
  &max_tokens=500
  &limit=100              // default 100
  &db_path=...

→ [{memo}, ...]
```

### Semantic search

```
POST /search
Content-Type: application/json

{
  "query": "serverless relational storage",
  "limit": 10,
  "min_score": 0.4,          // optional, 0–1 cosine similarity threshold
  "tags": ["db"],            // optional any-match tag filter
  "after": 1700000000.0,     // optional Unix timestamp
  "before": 1800000000.0,    // optional Unix timestamp
  "min_tokens": 10,          // optional
  "max_tokens": 500,         // optional
  "db_path": "...",          // optional
  "scope": "local"           // "local" | "global" | "all"
}

→ [{"document": {memo}, "score": 0.82}, ...]
```

Results are ordered by descending similarity score. Filters are applied after the vector search.

### Context retrieval

Search with multiple query angles, deduplicate, and return a token-budgeted content block. Designed for loading relevant memos into context without overflow.

```
POST /context
Content-Type: application/json

{
  "query": "sqlite vector search",
  "token_budget": 4000,         // max tokens in returned content
  "queries": ["embedded db", "cosine similarity"],  // optional extra angles
  "limit_per_query": 10,
  "min_score": null,
  "tags": [],
  "after": null,
  "before": null,
  "db_path": null,
  "scope": "local"
}

→ {
    "content": "## Title [tags] (score: 0.82)\n...",
    "token_count": 3821,
    "doc_count": 7,
    "truncated": false
  }
```

All query angles are embedded and searched in parallel. Results are deduplicated by ID (highest score wins), ranked, then greedily filled into the token budget.

---

## db_path

Every tool and endpoint accepts an optional `db_path` that controls which database to use:

| Value | Resolves to |
|---|---|
| `null` / omitted | Global default (`~/.memo/memo.db` or `DEFAULT_DB_PATH`) |
| Explicit `.db` / `.sqlite` / `.sqlite3` file path | That file directly |
| Directory path (e.g. `/home/user/myproject`) | `<data_dir>/home_user_myproject.memo.db` |

The directory form lets you pass the current working directory and get a stable per-project DB automatically, without managing filenames.

## scope

`memo_search`, `memo_list`, and `memo_context` accept a `scope` parameter:

| Value | Searches |
|---|---|
| `"local"` (default) | DB specified by `db_path` (or global if omitted) |
| `"global"` | Global DB only, ignoring `db_path` |
| `"all"` | Both `db_path` DB and global DB, merged and deduplicated |

---

## MCP Server

memo exposes an MCP server at `http://localhost:8000/mcp/` using the streamable-HTTP transport.

> **Note:** The trailing slash on `/mcp/` is required.

### Claude Code setup

```bash
claude mcp add --transport http --scope user memo http://localhost:8000/mcp/
```

Or copy `config/mcp.json` and reference it in your Claude Code MCP settings.

### MCP Tools

#### `memo_store`

Store a memo with automatic embedding and token count.

| Param | Type | Default | Description |
|---|---|---|---|
| `content` | string | *(required)* | Memo text |
| `title` | string | `null` | Optional title |
| `tags` | string[] | `[]` | Tags for filtering |
| `metadata` | object | `{}` | Arbitrary key-value metadata |
| `db_path` | string | `null` | DB override (file or directory path) |

Returns `{"id": "<uuid>"}`.

#### `memo_update`

Update an existing memo by ID. Only provided fields are changed.

| Param | Type | Default | Description |
|---|---|---|---|
| `id` | string | *(required)* | Memo UUID |
| `content` | string | `null` | New content — triggers re-embed + token recount |
| `title` | string | `null` | New title |
| `tags` | string[] | `null` | Replacement tag list |
| `metadata` | object | `null` | Replacement metadata |
| `db_path` | string | `null` | DB override |

Returns the updated memo, or `null` if not found.

#### `memo_search`

Search memos by semantic similarity with optional filters.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | string | *(required)* | Search query |
| `limit` | int | `10` | Max results |
| `min_score` | float | `null` | Minimum cosine similarity (0–1) |
| `tags` | string[] | `null` | Any-match tag filter |
| `after` | float | `null` | `created_at >=` (Unix timestamp) |
| `before` | float | `null` | `created_at <=` (Unix timestamp) |
| `min_tokens` | int | `null` | `token_count >=` |
| `max_tokens` | int | `null` | `token_count <=` |
| `db_path` | string | `null` | DB override |
| `scope` | string | `"local"` | `"local"` / `"global"` / `"all"` |

Returns list of `{document, score}`.

#### `memo_context`

Search with multiple query angles in parallel, deduplicate, and return a token-budgeted content block. Use this instead of `memo_search` when you want to load relevant context without flooding the caller's context window.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | string | *(required)* | Primary search query |
| `token_budget` | int | `4000` | Max tokens in returned content |
| `queries` | string[] | `null` | Additional search angles (run in parallel) |
| `limit_per_query` | int | `10` | Results fetched per query angle |
| `min_score` | float | `null` | Minimum cosine similarity |
| `tags` | string[] | `null` | Any-match tag filter |
| `after` | float | `null` | `created_at >=` |
| `before` | float | `null` | `created_at <=` |
| `db_path` | string | `null` | DB override |
| `scope` | string | `"local"` | `"local"` / `"global"` / `"all"` |

Returns `{content, token_count, doc_count, truncated}`.

#### `memo_list`

List memos in reverse-chronological order with optional filters.

| Param | Type | Default | Description |
|---|---|---|---|
| `tags` | string[] | `null` | Any-match tag filter |
| `after` | float | `null` | `created_at >=` |
| `before` | float | `null` | `created_at <=` |
| `min_tokens` | int | `null` | `token_count >=` |
| `max_tokens` | int | `null` | `token_count <=` |
| `limit` | int | `100` | Max results |
| `db_path` | string | `null` | DB override |
| `scope` | string | `"local"` | `"local"` / `"global"` / `"all"` |

#### `memo_get`

Retrieve a memo by ID.

| Param | Type | Description |
|---|---|---|
| `id` | string | Memo UUID |
| `db_path` | string | DB override |

#### `memo_delete`

Delete a memo by ID.

| Param | Type | Description |
|---|---|---|
| `id` | string | Memo UUID |
| `db_path` | string | DB override |

Returns `{"deleted": true/false}`.

---

## Claude Code Skills

Three skills are included in `.claude/skills/` for use with Claude Code. Install user-wide by copying to `~/.claude/skills/`.

### `/memorize`

Store or update a memo.

```
/memorize <content> [#tag1 #tag2]
/memorize                              # summarizes current conversation
/memorize --update <id> <new content>
/memorize --update <id> #new #tags
```

### `/recall`

Semantic search with optional tag and score filters.

```
/recall <query>
/recall <query> #tag1 #tag2
/recall <query> --limit 5
/recall <query> --min-score 0.5
```

### `/recall-context`

Retrieval subagent (`context: fork`) — runs in isolation so all search overhead stays out of the main context window. Returns a single token-budgeted content block.

```
/recall-context <topic>
/recall-context <topic> --budget 5000
/recall-context <topic> --budget 3000 --scope global
/recall-context <topic> #tag1 --budget 2000
```

The subagent formulates multiple query angles, calls `memo_context`, and returns only the final formatted block to the main agent.

---

## Development

```bash
# Install
uv venv .venv
uv pip install -e .

# Run dev server
.venv/bin/uvicorn memo.main:app --port 8002 --reload

# Quick checks
.venv/bin/python -c "import asyncio; from memo.embeddings import embed; v=asyncio.run(embed('test')); print(len(v))"
.venv/bin/python -c "from memo.db import _get_or_create_conn, _resolve_path; _get_or_create_conn(_resolve_path(None)); print('ok')"
```

### Project structure

```
src/memo/
├── main.py        # FastAPI app + FastMCP mount + all MCP tools
├── config.py      # pydantic-settings from env
├── db.py          # sqlite-vec connection cache, schema, CRUD, vector search
├── embeddings.py  # AsyncOpenAI → OpenRouter embed()
└── models.py      # Pydantic request/response models

.claude/skills/
├── memorize/      # store and update memos
├── recall/        # semantic search
└── recall-context/ # token-budgeted retrieval subagent
```
