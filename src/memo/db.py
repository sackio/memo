import asyncio
import json
import logging
import sqlite3
import struct
import threading
import uuid
from pathlib import Path
from time import time

import sqlite_vec
import tiktoken

from memo.config import settings

logger = logging.getLogger(__name__)

# Connections are THREAD-LOCAL. 2026-07-30: a single cached connection was
# shared across every thread `asyncio.to_thread` handed work to, with
# check_same_thread=False silencing the guard that would have caught it. Any
# two concurrent DB operations — a multi-angle memo_context fan-out, or just
# two sessions calling memo_search at the same instant — used one sqlite3
# connection from two threads at once. That is not merely unsupported, it
# corrupts results: it surfaced as SQLITE_MISUSE ("bad parameter or other API
# misuse"), "tuple index out of range", and json.loads(None) — three crash
# sites, one race.
_local = threading.local()
_conn_create_lock = threading.RLock()
_tokenizer = tiktoken.get_encoding("cl100k_base")
_ignored_db_path_seen: set[str] = set()  # sampling set so we don't log-spam


def _resolve_path(db_path: str | None) -> str:
    # 2026-06-29 refactor: every request routes to the single global DB on
    # server4. db_path is preserved in the request schema for backward
    # compatibility but the server ignores it. Log a sampled warning so we
    # can find callers that still pass it.
    if db_path and settings.ignored_db_path_warning:
        key = str(db_path)
        if key not in _ignored_db_path_seen:
            _ignored_db_path_seen.add(key)
            logger.warning(
                "db_path argument %r ignored — server is now single-global. "
                "Update your caller to drop the db_path argument.",
                key,
            )
    return settings.resolved_default_db_path


def global_path() -> str:
    return settings.resolved_default_db_path


def _count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Cut text to at most max_tokens, on a token boundary."""
    if max_tokens <= 0:
        return ""
    tokens = _tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _tokenizer.decode(tokens[:max_tokens])


def _get_or_create_conn(db_path: str) -> sqlite3.Connection:
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    existing = conns.get(db_path)
    if existing is not None:
        return existing

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=True (the default) is deliberate: connections are now
    # per-thread, so a connection crossing threads is a bug, and this makes it
    # raise loudly instead of silently corrupting reads.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute("PRAGMA journal_mode=WAL")
    # ⚠️ 5000 was too tight and produced real failures. On 2026-08-03 server4
    # spent hours at ~50% iowait with load peaking above 120, and `insurance`
    # hit `database is locked` on roughly 4 bulk writes — a lock wait longer
    # than 5s is ordinary on a saturated disk, not a sign of contention worth
    # failing over. 30s costs nothing when the disk is healthy (the timeout is
    # a ceiling, not a delay) and converts a caller-visible error into a wait.
    conn.execute("PRAGMA busy_timeout=30000")

    # Schema creation is serialized across threads: WAL permits concurrent
    # readers, but two threads running CREATE TABLE IF NOT EXISTS at once on a
    # fresh DB is a race worth not having.
    with _conn_create_lock:
        _init_schema(conn)
    conns[db_path] = conn
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            title TEXT,
            tags TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{{}}',
            token_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS document_embeddings USING vec0(
            doc_id TEXT,
            embedding FLOAT[{settings.embedding_dimensions}] distance_metric=cosine
        );

        -- L3c 2026-07-05: per-doc access counters for utility-based reaping.
        -- Incremented on every GET /documents/<id>, PATCH, DELETE. Enables
        -- Phase F to reap memos never fetched in N days.
        CREATE TABLE IF NOT EXISTS doc_access (
            doc_id TEXT PRIMARY KEY,
            get_count INTEGER NOT NULL DEFAULT 0,
            patch_count INTEGER NOT NULL DEFAULT 0,
            delete_count INTEGER NOT NULL DEFAULT 0,
            last_fetched_at REAL,
            last_patched_at REAL,
            last_deleted_at REAL
        );

        -- v0.3.1 2026-07-21: log each malformed-write refusal so the
        -- client-side corruption can be diagnosed over time from a growing
        -- corpus of incidents. The corruption is upstream of the server;
        -- this is a passive detector, not a fix. Grep query:
        --   SELECT ts, matched_fragment, endpoint, user_agent, source_ip,
        --          content_head, content_tail
        --   FROM leak_incidents ORDER BY ts DESC LIMIT 20;
        CREATE TABLE IF NOT EXISTS leak_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            endpoint TEXT NOT NULL,       -- "memo_store" | "memo_update" | "POST /documents" | ...
            matched_fragment TEXT,        -- which fragment ("<parameter name=", "<tags>", "</invoke>")
            tags_state TEXT NOT NULL,     -- "None" or "empty-list"
            content_len INTEGER NOT NULL,
            content_head TEXT,            -- first 200 chars of content
            content_tail TEXT,            -- last 400 chars of content (contains the fingerprint)
            user_agent TEXT,              -- HTTP User-Agent (or MCP client indicator) — distinguishes claude-p / claude.ai / Claude Code
            source_ip TEXT                -- request source IP
        );

        -- v0.3.8 2026-08-03: query log, for replaying REAL traffic against a
        -- candidate build instead of synthetic queries. [Ben's ask, 11:23 EDT]
        --
        -- ⭐ WHY THIS IS THE MEASUREMENT THAT MATTERS. Until today every number
        -- comparing memo builds came from the own-title set — searching a memo's
        -- exact title. That is free supervision and it is unrealistically easy:
        -- nobody searches by pasting a title. The corpus of what the fleet
        -- ACTUALLY asks is the only test set that cannot be accused of being
        -- chosen to suit the thing being tested.
        --
        -- ⛔ `results` stores the returned doc ids AND scores, not just a count.
        -- A replay that can only compare "how many hits" cannot see a RANKING
        -- change, which is the entire quantity under test.
        --
        -- ⚠️ CONTAINS QUERY TEXT, which for this corpus means credentials,
        -- family, finances. It lives in the same DB as the memos it searches and
        -- goes nowhere else. Trimmed to QUERY_LOG_MAX rows.
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            op TEXT NOT NULL,             -- "search" | "context" | "store" | "update" | "delete"
            query TEXT,                   -- the query text (reads) or title (writes)
            arg_limit INTEGER,
            tags TEXT,                    -- json list, the filter actually applied
            result_ids TEXT,              -- json list of returned doc ids, IN RANK ORDER
            result_scores TEXT,           -- json list of scores, parallel to result_ids
            n_results INTEGER,
            latency_ms REAL,
            embedding_model TEXT,         -- ⭐ so a replay knows what produced the ranking
            user_agent TEXT,
            source_ip TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_query_log_ts ON query_log(ts);
        CREATE INDEX IF NOT EXISTS idx_query_log_op ON query_log(op);
    """)
    # Migration: add token_count to existing DBs that predate this column
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "token_count" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN token_count INTEGER NOT NULL DEFAULT 0")
    # Migration 2026-07-21: add user_agent + source_ip to leak_incidents
    leak_cols = {row[1] for row in conn.execute("PRAGMA table_info(leak_incidents)")}
    if leak_cols and "user_agent" not in leak_cols:
        conn.execute("ALTER TABLE leak_incidents ADD COLUMN user_agent TEXT")
    if leak_cols and "source_ip" not in leak_cols:
        conn.execute("ALTER TABLE leak_incidents ADD COLUMN source_ip TEXT")
    conn.commit()


QUERY_LOG_MAX = 200_000


def log_query(db_path: str | None, op: str, *, query: str | None = None,
              arg_limit: int | None = None, tags: list[str] | None = None,
              result_ids: list[str] | None = None,
              result_scores: list[float] | None = None,
              latency_ms: float | None = None,
              user_agent: str | None = None,
              source_ip: str | None = None) -> None:
    """Record one request for later replay. BEST-EFFORT — never raises. [v0.3.8]

    ⛔ **THE `except: pass` IS THE MOST IMPORTANT LINE HERE, NOT A LAZY ONE.**
    This runs inside every read on LIVE FLEET INFRASTRUCTURE — every agent's
    `/recall` goes through it. A logging bug that propagated would take out
    retrieval for the whole fleet to collect a benchmark. **The observation must
    never be able to damage the thing it observes.**

    ⚠️ It is deliberately called AFTER the response is computed, so a slow or
    failing log cannot delay or fail the query it is recording.
    """
    conn = None
    try:
        conn = _get_or_create_conn(_resolve_path(db_path))
        conn.execute(
            "INSERT INTO query_log (ts, op, query, arg_limit, tags, result_ids, "
            "result_scores, n_results, latency_ms, embedding_model, user_agent, source_ip) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time(), op, (query or "")[:2000], arg_limit,
             json.dumps(tags or []),
             json.dumps(result_ids or []),
             json.dumps([round(s, 6) for s in (result_scores or [])]),
             len(result_ids or []), latency_ms, settings.embedding_model,
             user_agent, source_ip))
        # Bounded growth. Trimmed rarely rather than on every insert.
        if int(time()) % 500 == 0:
            conn.execute(
                "DELETE FROM query_log WHERE id < (SELECT MAX(id) - ? FROM query_log)",
                (QUERY_LOG_MAX,))
        conn.commit()
    except Exception:
        # ⛔⛔ ROLLBACK IS NOT OPTIONAL, AND `pass` ALONE WAS A BUG. [v0.3.9]
        #
        # Python's sqlite3 opens a transaction implicitly before DML and holds
        # it until commit. If the INSERT above succeeds and the COMMIT then
        # fails — which is exactly what a 5s `busy_timeout` does on a host at
        # 50% iowait — swallowing the exception leaves the transaction OPEN on
        # this thread's connection. **Every subsequent read on that connection
        # then serves a snapshot from before other connections' commits.**
        #
        # Reported independently 2026-08-03 by two seats on the same host and
        # in the same hour:
        #   - `groton`: memo_update → not_found for an id memo_search returned
        #     moments earlier; an unchanged retry succeeded.
        #   - `insurance`: `database is locked` on bulk writes, also recovering
        #     on retry.
        # Both are this shape: a stale read snapshot pinned by a connection
        # whose failed commit was never rolled back.
        #
        # ⚠️ Introduced by the query logging added in v0.3.8 — i.e. by the
        # observation, running inside every read on live fleet infrastructure.
        # **The instrument damaged the thing it was measuring, in precisely the
        # way this function's own docstring says it must never do.** Best-effort
        # means the log may be lost; it does not mean the connection may be left
        # in a state that corrupts later reads.
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        pass  # see the docstring — this must never break a live request


async def log_query_async(*a, **kw) -> None:
    """Off the event loop. `log_query` does sync SQLite; a live read path should
    not wait on it even for a few ms, and `to_thread` also means a stalled write
    lock cannot hold up the response that was already computed."""
    try:
        await asyncio.to_thread(log_query, *a, **kw)
    except Exception:
        pass


def _bump_access(conn: sqlite3.Connection, doc_id: str, kind: str) -> None:
    """Increment access counter (best-effort; never raises)."""
    now = time()
    try:
        if kind == "get":
            conn.execute("""INSERT INTO doc_access (doc_id, get_count, last_fetched_at) VALUES (?, 1, ?)
                            ON CONFLICT(doc_id) DO UPDATE SET
                                get_count = get_count + 1,
                                last_fetched_at = excluded.last_fetched_at""", (doc_id, now))
        elif kind == "patch":
            conn.execute("""INSERT INTO doc_access (doc_id, patch_count, last_patched_at) VALUES (?, 1, ?)
                            ON CONFLICT(doc_id) DO UPDATE SET
                                patch_count = patch_count + 1,
                                last_patched_at = excluded.last_patched_at""", (doc_id, now))
        elif kind == "delete":
            conn.execute("""INSERT INTO doc_access (doc_id, delete_count, last_deleted_at) VALUES (?, 1, ?)
                            ON CONFLICT(doc_id) DO UPDATE SET
                                delete_count = delete_count + 1,
                                last_deleted_at = excluded.last_deleted_at""", (doc_id, now))
        conn.commit()
    except Exception:
        pass  # never let access-log failures break the actual request


def _serialize_vector(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d["tags"])
    d["metadata"] = json.loads(d["metadata"])
    return d


def _matches_filters(doc: dict, tags: list[str], after: float | None, before: float | None,
                     min_tokens: int | None, max_tokens: int | None) -> bool:
    if tags and not any(t in doc["tags"] for t in tags):
        return False
    if after is not None and doc["created_at"] < after:
        return False
    if before is not None and doc["created_at"] > before:
        return False
    if min_tokens is not None and doc["token_count"] < min_tokens:
        return False
    if max_tokens is not None and doc["token_count"] > max_tokens:
        return False
    return True


# --- Sync DB operations (called via asyncio.to_thread) ---

def _sync_store(db_path: str, content: str, title: str | None, tags: list[str],
                metadata: dict, embedding: list[float]) -> str:
    conn = _get_or_create_conn(db_path)
    doc_id = str(uuid.uuid4())
    now = time()
    token_count = _count_tokens(content)
    conn.execute(
        "INSERT INTO documents (id, content, title, tags, metadata, token_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, content, title, json.dumps(tags), json.dumps(metadata), token_count, now, now),
    )
    conn.execute(
        "INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
        (doc_id, _serialize_vector(embedding)),
    )
    conn.commit()
    return doc_id


def _sync_update(db_path: str, doc_id: str, content: str | None, title: str | None,
                 tags: list[str] | None, metadata: dict | None,
                 embedding: list[float] | None) -> dict | None:
    conn = _get_or_create_conn(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    existing = _row_to_dict(row)

    new_content = content if content is not None else existing["content"]
    new_title = title if title is not None else existing["title"]
    new_tags = tags if tags is not None else existing["tags"]
    new_metadata = metadata if metadata is not None else existing["metadata"]
    new_token_count = _count_tokens(new_content) if content is not None else existing["token_count"]

    conn.execute(
        "UPDATE documents SET content=?, title=?, tags=?, metadata=?, token_count=?, updated_at=? WHERE id=?",
        (new_content, new_title, json.dumps(new_tags), json.dumps(new_metadata), new_token_count, time(), doc_id),
    )
    if embedding is not None:
        conn.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
        conn.execute(
            "INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
            (doc_id, _serialize_vector(embedding)),
        )
    conn.commit()
    _bump_access(conn, doc_id, "patch")
    updated = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_dict(updated)


def _sync_search(db_path: str, embedding: list[float], limit: int, min_score: float | None,
                 tags: list[str], after: float | None, before: float | None,
                 min_tokens: int | None, max_tokens: int | None) -> list[dict]:
    """Semantic search with tag scope (v0.3.1+).

    Two paths:
      A) No tag filter: fetch top-K nearest embeddings, then post-filter dates/tokens.
      B) Tag filter present: first collect candidate doc_ids matching tags (via json_each),
         then rank ONLY those candidates by embedding distance.

    Path B fixes a false-negative alpaca surfaced 2026-07-21: previously the tag list was
    a post-filter over a limit*5 top-K, so a correctly-tagged memo could vanish from
    results because the query didn't rank it high enough into the candidate window.
    Tag-scoped queries need a true DB-side scope; that's what this does.
    """
    conn = _get_or_create_conn(db_path)
    date_token_filters = bool(after or before or min_tokens or max_tokens)

    if tags:
        # PATH B — tag-scoped semantic search.
        # 1) Get doc_ids matching tags (+ any date/token filters), no limit yet.
        clauses, params = [], []
        tag_clause = " OR ".join(
            ["EXISTS (SELECT 1 FROM json_each(documents.tags) WHERE json_each.value = ?)"] * len(tags)
        )
        clauses.append(f"({tag_clause})")
        params.extend(tags)
        if after is not None: clauses.append("created_at >= ?"); params.append(after)
        if before is not None: clauses.append("created_at <= ?"); params.append(before)
        if min_tokens is not None: clauses.append("token_count >= ?"); params.append(min_tokens)
        if max_tokens is not None: clauses.append("token_count <= ?"); params.append(max_tokens)
        where = " AND ".join(clauses)
        candidate_rows = conn.execute(
            f"SELECT id FROM documents WHERE {where}", params
        ).fetchall()
        candidate_ids = [r["id"] for r in candidate_rows]
        if not candidate_ids:
            return []

        # 2) Fetch embeddings for the candidates + compute distance manually.
        # We use sqlite-vec's vec_distance_cosine so scoring is consistent with path A.
        placeholders = ",".join("?" * len(candidate_ids))
        rank_rows = conn.execute(
            f"SELECT doc_id, vec_distance_cosine(embedding, ?) AS distance "
            f"FROM document_embeddings WHERE doc_id IN ({placeholders}) "
            f"ORDER BY distance",
            [_serialize_vector(embedding)] + candidate_ids,
        ).fetchall()

        results = []
        for row in rank_rows:
            doc_id, distance = row["doc_id"], row["distance"]
            score = 1.0 - distance
            if min_score is not None and score < min_score:
                continue
            doc_row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if doc_row is None:
                continue
            results.append({"document": _row_to_dict(doc_row), "score": score})
            if len(results) >= limit:
                break
        return results

    # PATH A — no tag filter, use vec MATCH top-K.
    rows = conn.execute(
        "SELECT de.doc_id, de.distance "
        "FROM document_embeddings de "
        "WHERE de.embedding MATCH ? AND k = ? "
        "ORDER BY de.distance",
        (_serialize_vector(embedding), limit * 5 if date_token_filters else limit),
    ).fetchall()

    results = []
    for row in rows:
        doc_id, distance = row["doc_id"], row["distance"]
        if distance is None:
            # vec0 yields NULL for an undefined cosine distance — e.g. against a
            # zero-magnitude embedding. Skipping keeps one unusable row from
            # crashing every search that happens to rank near it.
            logger.warning("search: NULL distance for doc %s — skipping", doc_id)
            continue
        score = 1.0 - distance
        if min_score is not None and score < min_score:
            continue
        doc_row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if doc_row is None:
            continue
        doc = _row_to_dict(doc_row)
        if not _matches_filters(doc, [], after, before, min_tokens, max_tokens):
            continue
        results.append({"document": doc, "score": score})
        if len(results) >= limit:
            break
    return results


def _sync_get(db_path: str, doc_id: str) -> dict | None:
    conn = _get_or_create_conn(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row:
        _bump_access(conn, doc_id, "get")
        return _row_to_dict(row)
    return None


def _sync_delete(db_path: str, doc_id: str) -> bool:
    conn = _get_or_create_conn(db_path)
    cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    conn.commit()
    if cur.rowcount > 0:
        _bump_access(conn, doc_id, "delete")
    return cur.rowcount > 0


def _sync_copy(src_path: str, doc_id: str, dst_path: str) -> str | None:
    """Copy a document to another DB, reusing raw embedding bytes (no re-embedding)."""
    conn_src = _get_or_create_conn(src_path)
    conn_dst = _get_or_create_conn(dst_path)

    row = conn_src.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    doc = _row_to_dict(row)

    emb_row = conn_src.execute(
        "SELECT embedding FROM document_embeddings WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if emb_row is None:
        return None
    embedding_bytes = emb_row["embedding"]

    new_id = str(uuid.uuid4())
    now = time()
    conn_dst.execute(
        "INSERT INTO documents (id, content, title, tags, metadata, token_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id, doc["content"], doc["title"], json.dumps(doc["tags"]),
         json.dumps(doc["metadata"]), doc["token_count"], now, now),
    )
    conn_dst.execute(
        "INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
        (new_id, embedding_bytes),
    )
    conn_dst.commit()
    return new_id


def _sync_move(src_path: str, doc_id: str, dst_path: str) -> str | None:
    """Copy a document to another DB then delete from source."""
    new_id = _sync_copy(src_path, doc_id, dst_path)
    if new_id is None:
        return None
    _sync_delete(src_path, doc_id)
    return new_id


def _sync_list(db_path: str, tags: list[str], limit: int, after: float | None,
               before: float | None, min_tokens: int | None, max_tokens: int | None) -> list[dict]:
    conn = _get_or_create_conn(db_path)

    # Build SQL WHERE clauses for indexed columns (dates, token_count)
    clauses, params = [], []
    if after is not None:
        clauses.append("created_at >= ?")
        params.append(after)
    if before is not None:
        clauses.append("created_at <= ?")
        params.append(before)
    if min_tokens is not None:
        clauses.append("token_count >= ?")
        params.append(min_tokens)
    if max_tokens is not None:
        clauses.append("token_count <= ?")
        params.append(max_tokens)

    # L3c fix 2026-07-05: when tags are provided, the previous version fetched
    # only limit*3 newest rows and post-filtered, so rare tags on older memos
    # returned 0. Fix: push tag matching into SQL via json_each so pagination
    # is correct end-to-end.
    if tags:
        # Build an OR match: at least one supplied tag must appear in the doc's tags JSON.
        tag_clause = " OR ".join(["EXISTS (SELECT 1 FROM json_each(documents.tags) WHERE json_each.value = ?)"] * len(tags))
        clauses.insert(0, f"({tag_clause})")
        # Tag params go BEFORE the WHERE-derived params in the SQL, so prepend.
        params = list(tags) + params

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM documents {where} ORDER BY created_at DESC LIMIT ?", params
    ).fetchall()

    return [_row_to_dict(row) for row in rows]


# --- Async wrappers ---

async def store(db_path: str | None, content: str, title: str | None,
                tags: list[str], metadata: dict, embedding: list[float]) -> str:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_store, path, content, title, tags, metadata, embedding)


async def search(db_path: str | None, embedding: list[float], limit: int,
                 min_score: float | None, tags: list[str], after: float | None,
                 before: float | None, min_tokens: int | None, max_tokens: int | None) -> list[dict]:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(
        _sync_search, path, embedding, limit, min_score, tags, after, before, min_tokens, max_tokens
    )


async def get(db_path: str | None, doc_id: str) -> dict | None:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_get, path, doc_id)


async def update(db_path: str | None, doc_id: str, content: str | None, title: str | None,
                 tags: list[str] | None, metadata: dict | None,
                 embedding: list[float] | None) -> dict | None:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_update, path, doc_id, content, title, tags, metadata, embedding)


async def delete(db_path: str | None, doc_id: str) -> bool:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_delete, path, doc_id)


async def list_docs(db_path: str | None, tags: list[str], limit: int, after: float | None,
                    before: float | None, min_tokens: int | None, max_tokens: int | None) -> list[dict]:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_list, path, tags, limit, after, before, min_tokens, max_tokens)


async def search_multi(
    paths: list[str], embedding: list[float], limit: int, min_score: float | None,
    tags: list[str], after: float | None, before: float | None,
    min_tokens: int | None, max_tokens: int | None,
) -> list[dict]:
    """Search multiple DBs concurrently, merge by score, deduplicate by doc id."""
    tasks = [
        asyncio.to_thread(_sync_search, p, embedding, limit, min_score, tags, after, before, min_tokens, max_tokens)
        for p in paths
    ]
    per_db = await asyncio.gather(*tasks, return_exceptions=True)
    seen: set[str] = set()
    merged: list[dict] = []
    failures = [r for r in per_db if isinstance(r, Exception)]
    if failures:
        # Never swallow the whole fan-out. A query that dies in every DB and
        # returns [] is indistinguishable from a corpus with no matches — the
        # silent twin of the loud crash, where the caller reads "nothing here"
        # and re-grounds blind. Partial failure is logged and survivable; total
        # failure must raise.
        for exc in failures:
            logger.warning("fan-out: a per-DB query failed: %r", exc)
        if len(failures) == len(per_db):
            raise failures[0]
    for result in per_db:
        if isinstance(result, Exception):
            continue
        for item in result:
            doc_id = item["document"]["id"]
            if doc_id not in seen:
                seen.add(doc_id)
                merged.append(item)
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:limit]


async def copy(from_db_path: str | None, doc_id: str, to_db_path: str | None) -> str | None:
    # 2026-06-29 refactor: both paths resolve to the single global DB.
    # Copy is a no-op now — the doc is already where the caller wants it.
    src = _resolve_path(from_db_path)
    dst = _resolve_path(to_db_path)
    if src == dst:
        # Verify the doc exists; return its id if so, else None.
        conn = _get_or_create_conn(src)
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return row["id"] if row else None
    return await asyncio.to_thread(_sync_copy, src, doc_id, dst)


async def move(from_db_path: str | None, doc_id: str, to_db_path: str | None) -> str | None:
    # 2026-06-29 refactor: both paths resolve to the single global DB.
    # Move is a no-op now (the doc is already at the destination).
    src = _resolve_path(from_db_path)
    dst = _resolve_path(to_db_path)
    if src == dst:
        conn = _get_or_create_conn(src)
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return row["id"] if row else None
    return await asyncio.to_thread(_sync_move, src, doc_id, dst)


def _sync_recount_tokens(db_path: str) -> dict:
    """Recalculate token_count for docs where token_count=0 but content is non-empty."""
    conn = _get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT id, content FROM documents WHERE token_count = 0 AND content != ''"
    ).fetchall()
    updated = 0
    for row in rows:
        count = _count_tokens(row["content"])
        if count > 0:
            conn.execute("UPDATE documents SET token_count = ? WHERE id = ?", (count, row["id"]))
            updated += 1
    if updated:
        conn.commit()
    return {"fixed": updated, "scanned": len(rows)}


async def recount_tokens(db_path: str | None) -> dict:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_recount_tokens, path)


def _sync_access_stats(db_path: str, stale_days: int, limit: int) -> dict:
    """L3c 2026-07-05: aggregate per-doc access counters for utility-based reaping."""
    conn = _get_or_create_conn(db_path)
    cutoff = time() - (stale_days * 86400)

    # Totals
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_with_access = conn.execute("SELECT COUNT(*) FROM doc_access WHERE get_count > 0").fetchone()[0]

    # Reap candidates: docs OLDER than stale_days with no access in that window.
    reap_candidates = conn.execute(
        """SELECT d.id, d.title, d.created_at, d.updated_at,
                  COALESCE(a.get_count, 0) AS get_count,
                  COALESCE(a.patch_count, 0) AS patch_count,
                  a.last_fetched_at, a.last_patched_at
             FROM documents d
             LEFT JOIN doc_access a ON a.doc_id = d.id
            WHERE d.created_at < ?
              AND (a.last_fetched_at IS NULL OR a.last_fetched_at < ?)
              AND (a.last_patched_at IS NULL OR a.last_patched_at < ?)
            ORDER BY COALESCE(a.last_fetched_at, d.created_at) ASC
            LIMIT ?""",
        (cutoff, cutoff, cutoff, limit),
    ).fetchall()

    # Hot list: most fetched in the last 30d.
    hot_cutoff = time() - 30 * 86400
    hot = conn.execute(
        """SELECT d.id, d.title, a.get_count, a.last_fetched_at
             FROM doc_access a
             JOIN documents d ON d.id = a.doc_id
            WHERE a.last_fetched_at >= ?
            ORDER BY a.get_count DESC
            LIMIT 20""",
        (hot_cutoff,),
    ).fetchall()

    return {
        "as_of": time(),
        "total_docs": total_docs,
        "total_with_any_get": total_with_access,
        "coverage_pct": round(100.0 * total_with_access / max(total_docs, 1), 1),
        "stale_days_threshold": stale_days,
        "reap_candidates": [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "get_count": r["get_count"],
                "patch_count": r["patch_count"],
                "last_fetched_at": r["last_fetched_at"],
                "last_patched_at": r["last_patched_at"],
            }
            for r in reap_candidates
        ],
        "hot_last_30d": [
            {
                "id": r["id"],
                "title": r["title"],
                "get_count": r["get_count"],
                "last_fetched_at": r["last_fetched_at"],
            }
            for r in hot
        ],
    }


async def access_stats(stale_days: int, limit: int) -> dict:
    path = _resolve_path(None)
    return await asyncio.to_thread(_sync_access_stats, path, stale_days, limit)


def _sync_log_leak(db_path: str, endpoint: str, matched_fragment: str | None,
                   tags_state: str, content: str, user_agent: str | None = None,
                   source_ip: str | None = None) -> None:
    """Record a malformed-write rejection to leak_incidents. Best-effort — never raises."""
    try:
        conn = _get_or_create_conn(db_path)
        conn.execute(
            "INSERT INTO leak_incidents (ts, endpoint, matched_fragment, tags_state, "
            "content_len, content_head, content_tail, user_agent, source_ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time(), endpoint, matched_fragment, tags_state,
             len(content or ""), (content or "")[:200], (content or "")[-400:],
             user_agent, source_ip),
        )
        conn.commit()
    except Exception:
        pass  # never let logging failures propagate into the request path


async def log_leak(endpoint: str, matched_fragment: str | None, tags_state: str,
                    content: str, user_agent: str | None = None,
                    source_ip: str | None = None) -> None:
    path = _resolve_path(None)
    await asyncio.to_thread(_sync_log_leak, path, endpoint, matched_fragment,
                             tags_state, content, user_agent, source_ip)


def _sync_leak_incidents(db_path: str, limit: int) -> list[dict]:
    conn = _get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT id, ts, endpoint, matched_fragment, tags_state, content_len, "
        "content_head, content_tail, user_agent, source_ip "
        "FROM leak_incidents ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


async def leak_incidents(limit: int) -> list[dict]:
    path = _resolve_path(None)
    return await asyncio.to_thread(_sync_leak_incidents, path, limit)


async def list_docs_multi(
    paths: list[str], tags: list[str], limit: int, after: float | None,
    before: float | None, min_tokens: int | None, max_tokens: int | None,
) -> list[dict]:
    """List documents from multiple DBs, merge by created_at desc, deduplicate by doc id."""
    tasks = [
        asyncio.to_thread(_sync_list, p, tags, limit, after, before, min_tokens, max_tokens)
        for p in paths
    ]
    per_db = await asyncio.gather(*tasks, return_exceptions=True)
    seen: set[str] = set()
    merged: list[dict] = []
    failures = [r for r in per_db if isinstance(r, Exception)]
    if failures:
        # Never swallow the whole fan-out. A query that dies in every DB and
        # returns [] is indistinguishable from a corpus with no matches — the
        # silent twin of the loud crash, where the caller reads "nothing here"
        # and re-grounds blind. Partial failure is logged and survivable; total
        # failure must raise.
        for exc in failures:
            logger.warning("fan-out: a per-DB query failed: %r", exc)
        if len(failures) == len(per_db):
            raise failures[0]
    for result in per_db:
        if isinstance(result, Exception):
            continue
        for doc in result:
            if doc["id"] not in seen:
                seen.add(doc["id"])
                merged.append(doc)
    merged.sort(key=lambda x: x["created_at"], reverse=True)
    return merged[:limit]
