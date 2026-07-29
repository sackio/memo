# Contract: POST /log-queries (Intelligent Claude Code Log Query)

Backs FR-033. Enables provenance reconstruction, source-material lookup, and auditor cross-session pattern mining without loading whole `.jsonl` files.

## Endpoint

`POST /log-queries` — HTTP + MCP tool `memo_log_query`.

## Request

```json
{
  "host": "server4",
  "session_uuid": "abc-...",
  "project_dir": "-mnt-nas-data-code-quantum-feed",
  "line_range_start": null,
  "line_range_end": null,
  "grep_pattern": "FLAVOR-DUAL",
  "embedding_query": null,
  "context_lines": 3,
  "max_matches": 20
}
```

**Fields (all optional except one lookup key must be set):**
- `host`, `session_uuid`, `project_dir` — locate the jsonl file (memo enumerates via `find` on server4/office/server5 as needed).
- `line_range_start`, `line_range_end` — direct range fetch; skips grep.
- `grep_pattern` — bounded regex (memo enforces `--max-count` internally to protect against runaway matches, per the ugrep/OOM incident in global CLAUDE.md).
- `embedding_query` — natural-language query; memo embeds it and runs cosine similarity against a per-session line-embedding cache. Populated lazily (only when a session is queried more than once by embedding).
- `context_lines` — grep context lines around each match.
- `max_matches` — cap on returned matches; default 20.

## Response (200)

```json
{
  "host": "server4",
  "session_uuid": "abc-...",
  "matches": [
    {
      "line_range_start": 4712,
      "line_range_end": 4718,
      "content": "...raw jsonl lines...",
      "turn_role": "user",
      "turn_timestamp": 1785231234.5,
      "match_score": 0.94
    }
  ],
  "total_matches_in_file": 3,
  "search_method": "grep" | "embedding",
  "latency_ms": 87
}
```

## Response — SESSION NOT FOUND (404)

```json
{
  "error": "session_not_found",
  "detail": "no jsonl at /home/ben/.claude/projects/<project_dir>/<session_uuid>.jsonl on host <host>"
}
```

## Response — GUARD FIRE (413)

Refuses queries that would OOM the host (unbounded regex, all-file scan without pattern):

```json
{
  "error": "guard_refused",
  "detail": "grep_pattern would match >10000 lines; narrow the pattern or use line_range"
}
```

Mirrors the `command grep` shim discipline from global CLAUDE.md.

## Invariants

- Cross-host lookups use SSH port 4999 (memo caches session-uuid → host mapping to avoid probing all three).
- `grep_pattern` is enforced through `command grep -m<max> -oE ...` to bypass the ugrep shim.
- Embedding queries only fire against sessions where per-line embeddings have been indexed (opt-in via `POST /log-queries/index`).
- Never returns more than `max_matches` results; large-hit queries return a truncation notice.
- Used primarily by the storage mediator (for provenance reconstruction on legacy backfill) and the auditor (for cross-session pattern mining).
