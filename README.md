# memo

Lightweight HTTP API and MCP server for semantic document storage. Stores documents with vector embeddings in SQLite ([sqlite-vec](https://github.com/asg017/sqlite-vec)) and retrieves them by semantic similarity. Supports per-call `db_path` overrides for project-local databases.

## Stack

- **Python 3.12** + **uv**
- **FastAPI** + **uvicorn**
- **FastMCP** (streamable-HTTP transport at `/mcp/`)
- **sqlite-vec** for vector storage and cosine similarity search
- **tiktoken** for token counting at store time
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

### Store a document

```
POST /documents
Content-Type: application/json

{
  "content": "SQLite is an embedded relational database",
  "title": "SQLite overview",          // optional
  "tags": ["db", "sqlite"],            // optional
  "metadata": {"source": "notes"},     // optional
  "db_path": "/path/to/custom.db"      // optional, overrides default
}

→ {"id": "<uuid>"}
```

`token_count` is computed automatically from `content` using the `cl100k_base` tokenizer.

### List documents

```
GET /documents
  ?tags=db&tags=sqlite    // any-match tag filter
  &after=1700000000.0     // created_at >= (Unix timestamp)
  &before=1800000000.0    // created_at <= (Unix timestamp)
  &min_tokens=10          // token_count >=
  &max_tokens=500         // token_count <=
  &limit=100              // default 100
  &db_path=...            // optional

→ [{document}, ...]
```

### Get a document

```
GET /documents/{id}?db_path=...
→ {document}
```

### Delete a document

```
DELETE /documents/{id}?db_path=...
→ {"deleted": true}
```

### Semantic search

```
POST /search
Content-Type: application/json

{
  "query": "serverless relational storage",
  "limit": 10,             // default 10
  "min_score": 0.4,        // optional, 0–1 cosine similarity threshold
  "tags": ["db"],          // optional any-match tag filter
  "after": 1700000000.0,   // optional Unix timestamp
  "before": 1800000000.0,  // optional Unix timestamp
  "min_tokens": 10,        // optional
  "max_tokens": 500,       // optional
  "db_path": "..."         // optional
}

→ [{"document": {document}, "score": 0.82}, ...]
```

Results are ordered by descending similarity score. All filters are applied after the vector search.

### Document schema

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

---

## MCP Server

memo exposes an MCP server at `http://localhost:8000/mcp/` using the streamable-HTTP transport.

### Claude Code setup

Copy `config/mcp.json` to `~/.claude/mcp/memo.json`:

```bash
cp config/mcp.json ~/.claude/mcp/memo.json
```

Or configure manually in your MCP settings:

```json
{
  "type": "http",
  "url": "http://localhost:8000/mcp/",
  "name": "memo",
  "description": "Semantic document storage and retrieval"
}
```

> **Note:** The trailing slash on `/mcp/` is required.

### MCP Tools

#### `memo_store`

Store a document with automatic embedding and token count.

| Param | Type | Default | Description |
|---|---|---|---|
| `content` | string | *(required)* | Document text |
| `title` | string | `null` | Optional title |
| `tags` | string[] | `[]` | Tags for filtering |
| `metadata` | object | `{}` | Arbitrary key-value metadata |
| `db_path` | string | `null` | Override database path |

Returns `{"id": "<uuid>"}`.

#### `memo_search`

Search documents by semantic similarity with optional filters.

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
| `db_path` | string | `null` | Override database path |

Returns list of `{document, score}`.

#### `memo_list`

List documents with optional filters. No semantic search — returns in reverse-chronological order.

| Param | Type | Default | Description |
|---|---|---|---|
| `tags` | string[] | `null` | Any-match tag filter |
| `after` | float | `null` | `created_at >=` (Unix timestamp) |
| `before` | float | `null` | `created_at <=` (Unix timestamp) |
| `min_tokens` | int | `null` | `token_count >=` |
| `max_tokens` | int | `null` | `token_count <=` |
| `limit` | int | `100` | Max results |
| `db_path` | string | `null` | Override database path |

#### `memo_get`

Retrieve a document by ID.

| Param | Type | Description |
|---|---|---|
| `id` | string | Document UUID |
| `db_path` | string | Override database path |

#### `memo_delete`

Delete a document by ID.

| Param | Type | Description |
|---|---|---|
| `id` | string | Document UUID |
| `db_path` | string | Override database path |

Returns `{"deleted": true/false}`.

---

## Per-project Databases

Every tool and endpoint accepts an optional `db_path`. When provided, memo creates (or opens) a SQLite database at that path instead of the default. This lets each project maintain its own isolated memory:

```python
# Store in project-local DB
memo_store(content="...", db_path="/home/user/myproject/.memo/memo.db")

# Search only that project's memories
memo_search(query="...", db_path="/home/user/myproject/.memo/memo.db")
```

---

## Development

```bash
# Install
uv venv .venv
uv pip install -e .

# Run dev server (auto-reload)
.venv/bin/uvicorn memo.main:app --port 8002 --reload

# Quick checks
python -c "import asyncio; from memo.embeddings import embed; v=asyncio.run(embed('test')); print(len(v))"
python -c "from memo.db import _get_or_create_conn, _resolve_path; _get_or_create_conn(_resolve_path(None)); print('ok')"
```

### Project structure

```
src/memo/
├── main.py        # FastAPI app + FastMCP mount + all MCP tools
├── config.py      # pydantic-settings from env
├── db.py          # sqlite-vec connection cache, schema, CRUD, vector search
├── embeddings.py  # AsyncOpenAI → OpenRouter embed()
└── models.py      # Pydantic request/response models
```
