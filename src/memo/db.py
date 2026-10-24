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
#
# (Both branches carried this fix independently; the "ported from v1 0.3.4"
# note the renovation branch had is dropped as meaningless post-merge.)
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


def _clear_thread_connections(close: bool = False) -> None:
    """Drop this thread's cached connections (test teardown / path switches)."""
    conns = getattr(_local, "conns", None)
    if not conns:
        return
    if close:
        for conn in conns.values():
            conn.close()
    conns.clear()



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

    # Schema creation AND migrations are serialized across threads: WAL permits
    # concurrent readers, but two threads running CREATE TABLE IF NOT EXISTS —
    # or a migration — at once on a fresh DB is a race worth not having.
    with _conn_create_lock:
        _init_schema(conn)
        _apply_migrations(conn)
    conns[db_path] = conn
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply any unapplied migrations from the ``migrations/`` directory. [001/FR-001 001/FR-002]

    Idempotent: tracked via a ``migrations_applied`` table so each file
    runs exactly once. Skips silently if the migrations dir is absent
    (allows in-tree unit tests that don't ship with the dir).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migrations_applied ("
        "  filename TEXT PRIMARY KEY, applied_at REAL NOT NULL)"
    )
    conn.commit()

    # Migration dir is copied into the container at /app/migrations.
    # In tests / dev, fall back to the working tree location.
    candidates = [Path("/app/migrations"), Path(__file__).resolve().parents[2] / "migrations"]
    migrations_dir = next((p for p in candidates if p.is_dir()), None)
    if migrations_dir is None:
        logger.info("no migrations dir found — skipping migrations")
        return

    applied = {row["filename"] for row in conn.execute("SELECT filename FROM migrations_applied")}
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text()
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as e:
            # ALTER TABLE ADD COLUMN fails with "duplicate column" when re-run
            # against a DB that already had the column added out-of-band.
            # That's benign; log + mark applied so we don't retry.
            if "duplicate column" in str(e).lower():
                logger.warning("migration %s: %s (marking applied)", path.name, e)
            else:
                logger.error("migration %s FAILED: %s", path.name, e)
                raise
        conn.execute(
            "INSERT INTO migrations_applied (filename, applied_at) VALUES (?, ?)",
            (path.name, time()),
        )
        conn.commit()
        logger.info("applied migration %s", path.name)


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

        -- 002/FR-105 — passage-level vectors. Lives beside the document-level
        -- table rather than replacing it: both retrieval paths must be live at
        -- once so the passage path can be measured against the document path
        -- before it becomes the default, and so a regression is a config change
        -- rather than a migration (002/FR-113).
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            doc_id TEXT,
            chunk_index INTEGER,
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
    # Migration 2026-09-28 (see migrations/012_token_count_units.sql): every
    # stored count names its tokenizer. Duplicated here so a DB created without
    # the migrations dir (in-tree tests) still gets the columns — the same reason
    # token_count is handled above.
    if "token_count_tokenizer" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN token_count_tokenizer "
                     "TEXT DEFAULT 'cl100k_base'")
    if "token_count_qwen3" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN token_count_qwen3 INTEGER")
    # ⛔ THE INDEX IS CREATED HERE, NOT ONLY IN 012 — AND 012'S COPY NEVER RAN.
    # `_apply_migrations` runs each file with `conn.executescript(sql)`, which
    # aborts at the FIRST failing statement. On every DB that already had the two
    # columns (which is every DB that mattered, because _init_schema above adds
    # them), 012's opening `ALTER TABLE ... ADD COLUMN` raises "duplicate column
    # name"; the runner catches that, marks the file APPLIED, and the second
    # ALTER and the CREATE INDEX below it never execute. Verified on the live
    # store 2026-10-19: `migrations_applied` holds 012, and
    # `documents_token_count_qwen3_idx` does not exist and never would have.
    # ⭐ A migration runner that records a file as applied when only its first
    # statement ran turns a partial apply into a permanent, silent gap — the log
    # says done, so nothing ever retries it. Idempotent DDL in `_init_schema`,
    # which runs before the migrations on every start, is the durable place.
    conn.execute("CREATE INDEX IF NOT EXISTS documents_token_count_qwen3_idx "
                 "ON documents(token_count_qwen3)")
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
        # ⚠️ WHAT IS PROVEN vs WHAT WAS INFERRED — corrected within the hour,
        # because the first version of this comment asserted a mechanism it had
        # not earned:
        #
        # PROVEN (measured in the built image, both directions): an INSERT
        # leaves `in_transaction` True until commit; a failed commit under the
        # old `except: pass` left it that way; this rollback clears it.
        #
        # ⛔ NOT PROVEN: that this path caused `groton`'s 2026-08-03
        # `memo_update → not_found` for an id `memo_search` had just returned.
        # It fits the symptom, and I inferred it from that fit. The next
        # consequence fails: **a pinned connection holds a write lock and blocks
        # EVERY other writer** (measured — an independent connection with
        # busy_timeout=3000 dies with `database is locked`). A pin lasting the
        # ~10 minutes between that store and that update would have failed every
        # write on the host for ten minutes, which nobody observed. So the pin
        # was short-lived, and a short pin does not explain a failure ten minutes
        # later.
        #
        # ⇒ **This rollback is correct defensively and stays regardless.** It is
        # not evidence that the reported symptom is diagnosed. If it recurs, the
        # discriminator is whether OTHER seats' writes fail in the same window —
        # pinning predicts they do.
        #
        # ⚠️ `insurance`'s concurrent `database is locked` reports were nearly
        # folded in here as confirmation. They are lock contention on a saturated
        # disk (the busy_timeout half) and that seat explicitly declined to be
        # counted as a witness for the pinning half. Folding them in would have
        # made one unproven story look like two independent confirmations of
        # itself.
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
                metadata: dict, embedding: list[float],
                doc_id: str | None = None,
                created_at: float | None = None,
                updated_at: float | None = None,
                provider_token_count: int | None = None,
                authored_by: str | None = None) -> str:
    """`doc_id`/`created_at`/`updated_at` exist for ONE caller: the v1<->v2 mirror.

    ⭐ `provider_token_count` is the count from the EMBEDDING PROVIDER's own
    tokenizer, taken from the same response whose vector we are storing. It is the
    unit `max_model_len` is enforced in; `token_count` beside it is cl100k and
    understates it by 67-4,279 (measured, n=108). ⛔ None means "not measured" and
    is stored as NULL — never coerce it to 0, which would read as an empty
    document with maximum headroom.

    ⭐ WHY IDS MUST BE PRESERVABLE. memo ids are a public reference format — rewarm
    pins cite them, memos cite other memos by id, agents pass them between seats.
    A mirror that mints fresh ids looks correct in every count and every diff, and
    then every one of those references 404s the moment the mirror is promoted to
    serve :8000. Measured 2026-08-10: doc `2ef1eabe` was 200 on v1 and 404 on v2,
    across 123 docs and growing hourly.

    ⭐ WHY TIMESTAMPS TOO. Without them every mirrored doc claims it was created
    the moment it was copied, which silently rewrites the age of the corpus. Age
    is load-bearing here — recency ranking, and `valid_from` below — which is
    exactly why the standing operator rule is that age alone must never denote
    supersession. A mirror that fabricates ages makes that rule unenforceable.

    ⛔ NOT general-purpose client fields. A caller supplying an id that already
    exists gets an IntegrityError from the primary key, which the endpoint turns
    into a 409 rather than silently overwriting someone else's document.
    """
    conn = _get_or_create_conn(db_path)
    doc_id = doc_id or str(uuid.uuid4())
    now = time()
    created = created_at if created_at is not None else now
    updated = updated_at if updated_at is not None else now
    token_count = _count_tokens(content)
    # valid_from MUST be set explicitly. Migration 001 adds the column with
    # `NOT NULL DEFAULT 0` and backfills existing rows once, but an INSERT that
    # omits the column silently takes the 0 default — which would make every
    # NEW v1-path write look valid from the epoch and break get_as_of(). (Found
    # 2026-07-29: the first row written to the fresh v2 DB had valid_from=0.)
    conn.execute(
        "INSERT INTO documents (id, content, title, tags, metadata, token_count, "
        "token_count_qwen3, token_count_tokenizer, created_at, updated_at, valid_from, "
        "authored_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, content, title, json.dumps(tags), json.dumps(metadata), token_count,
         provider_token_count, "cl100k_base",
         created, updated, created, authored_by),
    )
    conn.execute(
        "INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
        (doc_id, _serialize_vector(embedding)),
    )
    conn.commit()
    return doc_id


class _Unset:
    """Sentinel for "the caller did not send this field".

    ⛔ EVERY OTHER OPTIONAL ARGUMENT ON THIS PATH USES `None` FOR THAT, AND TWO
    COLUMNS CANNOT. `valid_until` and `authored_by` are both legitimately
    NULL-valued, so `None` is a value a caller may want to WRITE — under the
    usual convention, "leave it alone" and "set it to null" are the same call,
    and the handler would have to guess. It guessed wrong in the safe-looking
    direction: leaving alone. That silently makes the clear operation a no-op
    returning 200, which is the exact failure shape this file already carries
    three times (`append`, `expect_content` over HTTP, `add_tags`).
    ⇒ The distinction is carried explicitly instead of inferred.
    """

    def __repr__(self) -> str:       # so a stray sentinel in a log is obvious
        return "<UNSET>"


UNSET = _Unset()


def _sync_update(db_path: str, doc_id: str, content: str | None, title: str | None,
                 tags: list[str] | None, metadata: dict | None,
                 embedding: list[float] | None,
                 expect_content: str | None = None,
                 provider_token_count: int | None = None,
                 valid_until: float | None | _Unset = UNSET,
                 authored_by: str | None | _Unset = UNSET) -> dict | None:
    conn = _get_or_create_conn(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    existing = _row_to_dict(row)

    # ⛔ OPTIMISTIC-CONCURRENCY GUARD FOR APPEND. [v0.4.0]
    # An append is read-modify-write across two round trips, so two concurrent
    # appends silently drop one — the newer write carries the older's base text.
    # The caller passes the content it read; if the row moved underneath, REFUSE
    # rather than overwrite.
    # ⚠️ This exists because the bug it accompanies was a SILENT no-op that
    # reported success. Replacing one invisible data loss with another would
    # have been the wrong fix.
    if expect_content is not None and existing["content"] != expect_content:
        return {"conflict": True, "id": doc_id,
                "reason": "content changed between read and write",
                "current_updated_at": existing["updated_at"]}

    new_content = content if content is not None else existing["content"]
    new_title = title if title is not None else existing["title"]
    new_tags = tags if tags is not None else existing["tags"]
    new_metadata = metadata if metadata is not None else existing["metadata"]
    new_token_count = _count_tokens(new_content) if content is not None else existing["token_count"]

    # ⛔ Only overwrite the provider count when the CONTENT changed and we actually
    # measured it. A title-only update must leave it alone: titles are never
    # embedded (main.py embeds `content`), so the document's provider token count
    # is unchanged and re-deriving it from nothing would blank a good measurement.
    if content is not None and provider_token_count is not None:
        new_provider_count = provider_token_count
    else:
        new_provider_count = existing.get("token_count_qwen3")
    # ⛔ THESE TWO COLUMNS ARE APPENDED TO THE STATEMENT, NEVER FOLDED INTO THE
    # FIXED LIST ABOVE. Both are legitimately NULL, so there is no value that
    # could stand for "unchanged" in a fixed binding — writing them
    # unconditionally would clear `valid_until` on every ordinary PATCH. See
    # `_Unset`. The `is not UNSET` test is identity, so a caller passing None
    # genuinely clears the column.
    extra_cols, extra_vals = "", []
    if valid_until is not UNSET:
        extra_cols += ", valid_until=?"
        extra_vals.append(valid_until)
    if authored_by is not UNSET:
        extra_cols += ", authored_by=?"
        extra_vals.append(authored_by)
    conn.execute(
        "UPDATE documents SET content=?, title=?, tags=?, metadata=?, token_count=?, "
        "token_count_qwen3=?" + extra_cols + ", updated_at=? WHERE id=?",
        (new_content, new_title, json.dumps(new_tags), json.dumps(new_metadata),
         new_token_count, new_provider_count, *extra_vals, time(), doc_id),
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
    # ⛔ `_row_to_memo`, NOT `_row_to_dict` — this is a READ-BACK that becomes the
    # PATCH response, so it must decode the v2 JSON columns exactly as every other
    # read path does (`_sync_get` :695, `_sync_list` :843, both search paths :648
    # and :1060). It was the ONE read returning raw column strings, and the
    # divergence was invisible for as long as the `Document` response model
    # happened to drop those fields: `scope` came back as the STRING '["global"]'
    # and nothing looked at it. The moment the envelope carried them (2026-10-19,
    # memo `d7b36ca8`), every guarded PATCH 500'd on validation.
    # ⭐ Two functions reading the same table through different deserialisers is
    # the defect; widening the model only revealed it.
    return _row_to_memo(updated)


def _sync_search(db_path: str, embedding: list[float], limit: int, min_score: float | None,
                 tags: list[str], after: float | None, before: float | None,
                 min_tokens: int | None, max_tokens: int | None,
                 include_superseded: bool = False) -> list[dict]:
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
    # ⭐ SUPERSEDED DOCUMENTS ARE EXCLUDED FROM SEARCH. [002/FR-115, 2026-08-03]
    #
    # ⛔ Before this, `/supersede` worked perfectly and changed NOTHING that any
    # caller could observe. Measured live rather than read off the source: a
    # document was superseded (edge written, `valid_until` set, both verified in
    # the DB) and search returned it at rank 3 on the very next query, above
    # everything except its own replacement.
    #
    # ⇒ **The bitemporal model was complete on the WRITE side and absent from the
    # READ path.** `get_current` and `as-of` honoured `valid_until`; the one
    # function every agent's `/recall` actually goes through did not reference it
    # at all. So the corpus's 655 self-declared-stale memos could ALL have been
    # correctly superseded and every one of them would still have been served.
    #
    # ⭐ `valid_until IS NULL` is already the codebase's definition of "currently
    # true" (FR-002). This makes the default read path mean what the schema says.
    # Time-travel remains available and explicit: `/documents/{id}/as-of`.
    # ⚠️ NOT a de-rank. A superseded memo is not "less relevant" — it is a fact
    # the corpus has been told is no longer true, and returning it ranked lower
    # still returns it.
    date_token_filters = bool(after or before or min_tokens or max_tokens)
    # Over-fetch when ANY post-filter can drop rows, supersession included —
    # otherwise excluded documents silently consume top-k slots and the caller
    # gets fewer results than asked for with nothing to indicate why.
    post_filters = date_token_filters or not include_superseded

    if tags:
        # PATH B — tag-scoped semantic search.
        # 1) Get doc_ids matching tags (+ any date/token filters), no limit yet.
        clauses, params = [], []
        tag_clause = " OR ".join(
            ["EXISTS (SELECT 1 FROM json_each(documents.tags) WHERE json_each.value = ?)"] * len(tags)
        )
        clauses.append(f"({tag_clause})")
        params.extend(tags)
        if not include_superseded:
            clauses.append("valid_until IS NULL")
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
            if distance is None:
                # sqlite-vec yields NULL for an undefined cosine — a stored
                # zero-magnitude vector. Skip rather than crash the whole
                # search on one bad row.
                logger.warning("search: NULL distance for doc %s — skipping", doc_id)
                continue
            score = 1.0 - distance
            if min_score is not None and score < min_score:
                continue
            doc_row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if doc_row is None:
                continue
            results.append({"document": _row_to_memo(doc_row), "score": score})
            if len(results) >= limit:
                break
        return results

    # PATH A — no tag filter, use vec MATCH top-K.
    rows = conn.execute(
        "SELECT de.doc_id, de.distance "
        "FROM document_embeddings de "
        "WHERE de.embedding MATCH ? AND k = ? "
        "ORDER BY de.distance",
        (_serialize_vector(embedding), limit * 5 if post_filters else limit),
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
        if not include_superseded and doc_row["valid_until"] is not None:
            continue
        doc = _row_to_memo(doc_row)
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
        return _row_to_memo(row)
    return None


def _sync_delete(db_path: str, doc_id: str, *, actor: str = "unknown",
                 reason: str = "unspecified", replaced_by: str | None = None) -> bool:
    """Delete a memo, snapshotting it to `deletion_log` first. [001/FR-028a]

    The snapshot is taken in the SAME transaction as the delete, and the row is
    read before anything is removed. That ordering is the whole guarantee: a
    deletion that fails to record its snapshot must not happen at all, because
    an unrecorded delete is indistinguishable from data loss.

    Content is stored in full, never truncated — a snapshot missing the tail of
    a long memo cannot restore it, which defeats the purpose.
    """
    conn = _get_or_create_conn(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return False

    d = dict(row)
    try:
        # IMMEDIATE for the same reason as passages._sync_replace: a deferred
        # BEGIN that upgrades to a write returns SQLITE_BUSY instantly in WAL
        # mode, without honouring busy_timeout. A reap sweep racing a write is
        # exactly the concurrency this hits.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO deletion_log (doc_id, deleted_at, content, title, tags, "
            "metadata, memo_class, created_at, actor, reason, replaced_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, time(), d.get("content") or "", d.get("title"),
             d.get("tags"), d.get("metadata"), d.get("class"), d.get("created_at"),
             actor, reason, replaced_by),
        )
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM chunk_embeddings WHERE doc_id = ?", (doc_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("delete of %s failed; rolled back (memo NOT deleted)", doc_id)
        raise

    if cur.rowcount > 0:
        _bump_access(conn, doc_id, "delete")
    return cur.rowcount > 0


def _sync_restore(db_path: str, doc_id: str) -> dict | None:
    """Recover the most recent deletion-log snapshot for a memo. [001/FR-028a]

    Returns the snapshot rather than re-inserting it: restoring needs a fresh
    embedding, which is an async concern. A caller that wants the memo back
    re-stores this content.
    """
    conn = _get_or_create_conn(db_path)
    row = conn.execute(
        "SELECT * FROM deletion_log WHERE doc_id = ? ORDER BY deleted_at DESC LIMIT 1",
        (doc_id,)).fetchone()
    return dict(row) if row else None


def _sync_copy(src_path: str, doc_id: str, dst_path: str) -> str | None:
    """Copy a document to another DB, reusing raw embedding bytes (no re-embedding)."""
    conn_src = _get_or_create_conn(src_path)
    conn_dst = _get_or_create_conn(dst_path)

    row = conn_src.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    # Deliberately _row_to_dict, NOT _row_to_memo: copy/move re-INSERTS this
    # row into another DB, so the v2 JSON columns must stay in their stored
    # string form. Decoding here would hand dicts to an INSERT that expects
    # TEXT. Read paths use _row_to_memo; this is a transfer path.
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

    return [_row_to_memo(row) for row in rows]


# --- Async wrappers ---

async def _index_passages_after_write(path: str, doc_id: str,
                                      content: str | None) -> None:
    """Keep the passage index in step with a write. [002/FR-110]

    ⭐ HOOKED AT THE CHOKE POINT, NOT AT THE CALL SITES — and `find_unindexed`'s
    own docstring is the argument for it: the guarantee "every memo is indexed"
    cannot rest on eleven write call sites all remembering to call
    `index_document`, because someone adds a twelfth and the corpus quietly
    develops holes that no listing reveals. There are eleven callers of `store`
    and `update`, and exactly one of each of them.

    ⛔ NEVER FAILS THE WRITE. A memo stored-but-unindexed is recoverable by a
    backfill; a memo whose store RAISED because the embedder was unreachable is
    gone, and there is no retry queue in front of the ~50 seats writing here. So
    this logs and returns. `find_unindexed()` and `/ready`'s `passage_index_gap`
    are what keep the resulting hole visible rather than silent.

    ⚠️ GLOBAL CORPUS ONLY. `passages` resolves `db.global_path()` unconditionally,
    so indexing a project-DB write would file its passages under the wrong store —
    a mis-attribution that reads as coverage.

    ⛔ THAT GUARD IS A NO-OP TODAY AND IS KEPT ANYWAY — stated because a guard
    that silently does nothing is worse than no guard, since a reader credits it
    with protection it is not providing. Since the 2026-06-29 single-global
    refactor `_resolve_path` IGNORES its argument and returns
    `settings.resolved_default_db_path` for every caller, which is exactly what
    `global_path()` returns, so `path != global_path()` cannot currently be true.
    It stays because the day project DBs come back is the day passages start
    landing in the wrong store, and that failure would present as a coverage
    number that looks healthy.

    ⚠️ `content is None` means an update that did not touch content (a tag or
    metadata edit). The existing passages are still correct, and re-embedding
    them would spend a provider round-trip to write identical rows.
    """
    if not settings.memo_inline_passage_index:
        return
    if path != global_path():
        return
    if content is None:
        return
    try:
        from memo import passages  # local: passages imports db
        await passages.index_document(doc_id, content)
    except Exception:
        logger.exception(
            "inline passage index failed for %s — the memo IS stored and is still "
            "reachable by document embedding and FTS5, but it will not rank by "
            "passage until a backfill runs "
            "(docker exec memo-v2 python3 /app/scripts/memo-index-corpus)", doc_id)


# ⛔⛔ BOUNDED RETRY ON `database is locked` — ADDED 2026-09-10 (authorized by Ben,
# Slack #memo: "fix all, and correct all issues"). Memo `75e42a5a`.
#
# ⚠️ WHAT THIS IS NOT. The queued proposal said "add busy_timeout" — that was
# WRONG when written and is corrected here: `PRAGMA busy_timeout=30000` has been
# set on every connection since 2026-08-03 (see _get_or_create_conn). So the
# measured failure was NOT a missing timeout, and I have NOT established which
# path produced it. This is deliberately a fix to the CONSEQUENCE, not a guess at
# the cause: a speculative change to the live write path serving ~50 seats is
# worse than an honest retry.
#
# WHAT WAS MEASURED (2026-09-09): a PATCH returned a BARE HTTP 500 carrying the
# raw string "database is locked". The write cleanly did not land — byte-identical
# to never having been attempted — and the caller's first manual retry succeeded.
# ⚑ One of the two observed 500s was ANOTHER SEAT'S write, not this seat's, so a
# fan-out here degrades write availability fleet-wide.
#
# ⭐ A write that would have succeeded 40ms later should not reach the caller as a
# 500. Two extra attempts with a short backoff cost nothing when the disk is
# healthy and convert the common case into a wait, exactly as busy_timeout does
# one level down. It stays BOUNDED so a genuinely wedged database still surfaces
# rather than hanging every request thread behind it.
_LOCK_RETRY_ATTEMPTS = 3
_LOCK_RETRY_BACKOFF = (0.05, 0.25)


async def _write_with_lock_retry(fn, *args):
    """Run a sync writer in a thread, retrying only a genuine lock contention.

    ⛔ Retries ONLY `database is locked` / `database is busy`. Every other
    OperationalError — a schema error, a constraint failure, a corrupt page — is
    re-raised on the first attempt. Retrying those would turn a deterministic bug
    into an intermittent one, which is strictly harder to diagnose than the bug.
    """
    last: Exception | None = None
    for attempt in range(_LOCK_RETRY_ATTEMPTS):
        try:
            return await asyncio.to_thread(fn, *args)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "database is locked" not in msg and "database is busy" not in msg:
                raise
            last = e
            if attempt == _LOCK_RETRY_ATTEMPTS - 1:
                break
            await asyncio.sleep(_LOCK_RETRY_BACKOFF[attempt])
            logger.warning(
                "sqlite lock contention on %s, attempt %d/%d — retrying",
                getattr(fn, "__name__", fn), attempt + 1, _LOCK_RETRY_ATTEMPTS,
            )
    assert last is not None
    raise last


async def store(db_path: str | None, content: str, title: str | None,
                tags: list[str], metadata: dict, embedding: list[float],
                doc_id: str | None = None,
                created_at: float | None = None,
                updated_at: float | None = None,
                provider_token_count: int | None = None,
                authored_by: str | None = None) -> str:
    path = _resolve_path(db_path)
    stored_id = await _write_with_lock_retry(_sync_store, path, content, title, tags,
                                             metadata, embedding, doc_id, created_at,
                                             updated_at, provider_token_count,
                                             authored_by)
    await _index_passages_after_write(path, stored_id, content)
    return stored_id


def _sync_search_passages(db_path: str, embedding: list[float], limit: int,
                          min_score: float | None, tags: list[str],
                          after: float | None, before: float | None,
                          min_tokens: int | None, max_tokens: int | None,
                          overfetch: int = 8,
                          include_superseded: bool = False) -> list[dict]:
    """Passage-level semantic search. [002/FR-105 002/FR-106 002/FR-107]

    Matches narrowly and returns broadly: the vector search runs over passages,
    but results are grouped back to memos and the WHOLE memo is returned with
    its best-matching passage attached as a highlight.

    Three properties, each load-bearing:

    * **A memo scores by its BEST passage, never the mean** (FR-106). A mean
      would rebuild exactly the dilution this feature removes — the reason a
      3,000-token memo currently loses to a short one on its own title.
    * **Grouping happens before ranking** (FR-105), so overlap between adjacent
      passages cannot let one memo occupy several result slots.
    * **Tag scope is applied DB-side first**, mirroring `_sync_search` path B.
      Post-filtering a top-K window silently drops correctly-tagged memos when
      the query does not rank them into the window — the false negative found
      2026-07-21. That bug is just as reachable here, so the ordering is kept.

    `overfetch` exists because K passages collapse into fewer memos; fetching
    `limit` passages would return fewer than `limit` memos.
    """
    conn = _get_or_create_conn(db_path)
    blob = _serialize_vector(embedding)

    scoped_ids: list[str] | None = None
    if tags or after or before or min_tokens is not None or max_tokens is not None:
        clauses, params = [], []
        if tags:
            tag_clause = " OR ".join(
                ["EXISTS (SELECT 1 FROM json_each(documents.tags) "
                 "WHERE json_each.value = ?)"] * len(tags))
            clauses.append(f"({tag_clause})")
            params.extend(tags)
        if after is not None: clauses.append("created_at >= ?"); params.append(after)
        if before is not None: clauses.append("created_at <= ?"); params.append(before)
        if min_tokens is not None: clauses.append("token_count >= ?"); params.append(min_tokens)
        if max_tokens is not None: clauses.append("token_count <= ?"); params.append(max_tokens)
        if not include_superseded:
            clauses.append("valid_until IS NULL")
        rows = conn.execute(
            f"SELECT id FROM documents WHERE {' AND '.join(clauses)}", params).fetchall()
        scoped_ids = [r["id"] for r in rows]
        if not scoped_ids:
            return []

    if scoped_ids is not None:
        ph = ",".join("?" * len(scoped_ids))
        hits = conn.execute(
            f"SELECT doc_id, chunk_index, vec_distance_cosine(embedding, ?) AS distance "
            f"FROM chunk_embeddings WHERE doc_id IN ({ph}) ORDER BY distance",
            [blob] + scoped_ids).fetchall()
    else:
        hits = conn.execute(
            "SELECT doc_id, chunk_index, distance FROM chunk_embeddings "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (blob, limit * overfetch)).fetchall()

    # Group to memos, keeping each memo's BEST passage.
    best: dict[str, tuple[float, int]] = {}
    for h in hits:
        distance = h["distance"]
        if distance is None:
            logger.warning("passage search: NULL distance for %s#%s — skipping",
                           h["doc_id"], h["chunk_index"])
            continue
        score = 1.0 - distance
        prev = best.get(h["doc_id"])
        if prev is None or score > prev[0]:
            best[h["doc_id"]] = (score, h["chunk_index"])

    results: list[dict] = []
    for doc_id, (score, chunk_index) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        if min_score is not None and score < min_score:
            continue
        doc_row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc_row is None:
            continue
        # Same exclusion as the document path — a superseded memo's PASSAGES are
        # just as stale as the memo, and the passage path is the one the
        # benchmark says we should be defaulting to.
        if not include_superseded and doc_row["valid_until"] is not None:
            continue
        prow = conn.execute(
            "SELECT text, token_start, token_end FROM document_chunks "
            "WHERE doc_id = ? AND chunk_index = ?", (doc_id, chunk_index)).fetchone()
        results.append({
            "document": _row_to_memo(doc_row),      # FR-107: the WHOLE memo
            "score": score,
            "passage": ({"text": prow["text"], "chunk_index": chunk_index,
                         "token_start": prow["token_start"],
                         "token_end": prow["token_end"]} if prow else None),
        })
        if len(results) >= limit:
            break
    return results


class EmbeddingModelMismatch(RuntimeError):
    """The configured model is not the one that wrote the stored vectors."""


_write_model_cache: dict[str, str | None] = {}


def _sync_stored_write_model(db_path: str) -> str | None:
    conn = _get_or_create_conn(db_path)
    try:
        row = conn.execute(
            "SELECT embedding_model FROM document_chunks LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None                     # no passage index yet — nothing to compare
    return row[0] if row else None


def assert_read_model_matches(db_path: str | None = None) -> None:
    """Refuse to search with a model that did not write the vectors. [002/FR-108]

    **Why this exists, and why the obvious defence is not enough.** `vec0` bakes
    the vector width into the table and rejects a mismatched QUERY vector — I
    verified that (`2560 query on a 3072 table → "Dimension mismatch for query
    vector"`). But that invariant keys on *dimension*, and dimension is only a
    proxy for model. It is faithful today because 2560 is an unusual width; it is
    a property of the model landscape rather than of memo, and the landscape is
    not ours to control.

    ⇒ Point `EMBEDDING_MODEL` at any OTHER 2560-dimension model and every read
    silently compares vectors across models: no exception, no width error, and
    plausible confidently-wrong results. That is precisely the failure this
    feature exists to prevent, and it is the one case the width guard misses.

    So the model is recorded beside the vectors and checked HERE, on the read
    path. A write-side check is the half that already works — writes fail loudly
    because a store has a shape; reads are the silent side. (Raised by the
    `embeddings` seat 2026-08-02 during the fleet inventory; `mind` gets this
    property structurally by resolving the model from the active collection,
    which memo does not do.)

    Cached after the first call: this is a per-process invariant, and a query-time
    round-trip to check it would be a real cost for a value that cannot change
    without a restart.
    """
    key = _resolve_path(db_path)
    if key not in _write_model_cache:
        _write_model_cache[key] = _sync_stored_write_model(key)
    stored = _write_model_cache[key]
    if stored and stored != settings.embedding_model:
        raise EmbeddingModelMismatch(
            f"refusing to search: vectors were written by {stored!r} but "
            f"EMBEDDING_MODEL is {settings.embedding_model!r}. Comparing "
            f"embeddings across models yields plausible, confidently wrong "
            f"results — re-embed the corpus or restore the original model.")


async def search_passages(db_path: str | None, embedding: list[float], limit: int,
                          min_score: float | None = None, tags: list[str] | None = None,
                          after: float | None = None, before: float | None = None,
                          min_tokens: int | None = None,
                          max_tokens: int | None = None,
                          include_superseded: bool = False) -> list[dict]:
    """Passage-level search. [002/FR-105]"""
    path = _resolve_path(db_path)
    assert_read_model_matches(path)
    return await asyncio.to_thread(
        _sync_search_passages, path, embedding, limit, min_score, tags or [],
        after, before, min_tokens, max_tokens,
        # ⛔ KEYWORD, NOT POSITIONAL. `_sync_search_passages` takes
        # `overfetch: int = 8` BEFORE `include_superseded`, so appending this
        # positionally put `False` into the OVERFETCH slot — `limit * False` is
        # `k = 0`, and passage search returned 0 hits with HTTP 200 for every
        # query. A silent, plausible "no results" rather than an error.
        # ⇒ **Appending a positional argument is unsafe whenever the callee has
        # an intervening default.** `to_thread` forwards positionally and cannot
        # warn. Caught only because a positive control on an unrelated query
        # failed; the superseded-doc test it was meant to confirm PASSED, and
        # would have shipped this.
        include_superseded=include_superseded)


async def search(db_path: str | None, embedding: list[float], limit: int,
                 min_score: float | None, tags: list[str], after: float | None,
                 before: float | None, min_tokens: int | None, max_tokens: int | None,
                 include_superseded: bool = False) -> list[dict]:
    path = _resolve_path(db_path)
    assert_read_model_matches(path)     # see the docstring there
    return await asyncio.to_thread(
        _sync_search, path, embedding, limit, min_score, tags, after, before,
        min_tokens, max_tokens, include_superseded
    )


async def get(db_path: str | None, doc_id: str) -> dict | None:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_get, path, doc_id)


def _sync_resolve_id(db_path: str, doc_id: str) -> dict:
    """Resolve a possibly-abbreviated id to exactly one full uuid.

    Short ids are what actually circulate — pins, commit messages, agent DMs and
    this codebase's own comments all use the 8-char form, while the store is
    keyed on the full uuid. So the common call was the one that failed.

    Returns `{"status": "exact"|"prefix"|"ambiguous"|"not_found", ...}`; the two
    resolving statuses carry `id`, and `ambiguous` carries `candidates` so the
    caller can pick rather than guess. ⛔ Ambiguity is reported, never resolved
    by picking the first — two memos sharing a prefix is exactly when silently
    returning one of them is worst.
    """
    conn = _get_or_create_conn(db_path)
    if conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone():
        return {"status": "exact", "id": doc_id}
    # Only a plausible prefix is worth a scan: a uuid fragment, not arbitrary text.
    frag = (doc_id or "").strip()
    if len(frag) < 4 or not all(c in "0123456789abcdefABCDEF-" for c in frag):
        return {"status": "not_found"}
    rows = conn.execute(
        "SELECT id, title FROM documents WHERE id LIKE ? || '%' LIMIT 11",
        (frag.lower(),),
    ).fetchall()
    if not rows:
        return {"status": "not_found"}
    if len(rows) == 1:
        return {"status": "prefix", "id": rows[0][0]}
    return {"status": "ambiguous",
            "candidates": [{"id": r[0], "title": r[1]} for r in rows[:10]],
            "candidate_count": len(rows)}


def _sync_link_supersession(db_path: str, old_id: str, new_id: str, actor: str,
                            reason: str | None = None,
                            operator_directive_ref: dict | None = None) -> dict:
    """Record that an EXISTING document supersedes another EXISTING document.

    ⭐⭐ WHY THIS EXISTS, MEASURED 2026-09-28. `supersede()` above does
    `new_id = str(uuid.uuid4())` and `SupersedeRequest` requires `content`: the only
    supersession path in memo MINTS A NEW DOCUMENT. So a seat holding two documents
    that already exist had NO WAY, on any surface, to say "B supersedes A".
    ⇒ That is why supersession is recorded in TITLE PROSE across this corpus: 137
    titles assert a redirect while only 117 edges exist. The prose is not laziness,
    it is the only expressive option a caller had. A `memo_supersede` MCP tool
    wrapping the minting call would have inherited the same limitation and fixed
    nothing.

    ⛔ THIS SETS `valid_until` ON `old_id`, WHICH REMOVES IT FROM DEFAULT SEARCH
    (FR-115). That is what supersession means and it is not a de-rank. It is
    reachable again only via `include_superseded=true` or `/documents/{id}/as-of`.
    ⇒ Do NOT call this for a PARTIAL CORRECTION. A title reading "CORRECTION to X"
    usually narrows one claim in X; retiring X would hide a mostly-correct document.
    Measured on the 2026-09-28 backfill candidates: of 119 single-id directive
    titles, 91 were true supersessions and 28 were partial corrections, including
    one whose mechanical application would have retired an entire NFSv4 root-cause
    document because a single claim in it had been narrowed.

    Refuses rather than guessing, and returns a `reason` for each refusal:
      · either document missing
      · old already superseded (same guard as `supersede()`)
      · old and new are the same document
      · new is itself superseded — pointing a reader at a retired successor
      · the edge already exists
      · the link would close a cycle (new already reaches old going forward)
    """
    conn = _get_or_create_conn(db_path)
    if old_id == new_id:
        return {"linked": False, "reason": "old and new are the same document"}
    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, valid_until FROM documents WHERE id IN (?, ?)", (old_id, new_id))}
    if old_id not in rows:
        return {"linked": False, "reason": f"old document {old_id} does not exist"}
    if new_id not in rows:
        return {"linked": False, "reason": f"new document {new_id} does not exist"}
    if rows[old_id]["valid_until"] is not None:
        return {"linked": False, "reason": f"{old_id} is already superseded"}
    if rows[new_id]["valid_until"] is not None:
        return {"linked": False,
                "reason": f"successor {new_id} is itself superseded — link to the "
                          "current version instead; follow its lineage forward"}
    if conn.execute("SELECT 1 FROM supersede_edges WHERE old_id = ? AND new_id = ?",
                    (old_id, new_id)).fetchone():
        return {"linked": False, "reason": "edge already exists"}
    # Cycle guard: walk FORWARD from new; if we reach old, this link closes a loop
    # and `get_current`'s lineage walk would not terminate on a fixed point.
    seen, frontier = {new_id}, [new_id]
    while frontier:
        cur = frontier.pop()
        if cur == old_id:
            return {"linked": False,
                    "reason": "would create a supersession cycle "
                              f"({new_id} already leads to {old_id})"}
        for r in conn.execute("SELECT new_id FROM supersede_edges WHERE old_id = ?", (cur,)):
            if r["new_id"] not in seen:
                seen.add(r["new_id"]); frontier.append(r["new_id"])

    now = time()
    try:
        conn.execute("UPDATE documents SET valid_until = ?, updated_at = ? WHERE id = ?",
                     (now, now, old_id))
        conn.execute(
            "INSERT INTO supersede_edges "
            "(old_id, new_id, superseded_at, actor, reason, operator_directive_ref) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (old_id, new_id, now, actor, reason,
             json.dumps(operator_directive_ref) if operator_directive_ref else None),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"linked": True, "old_id": old_id, "new_id": new_id,
            "superseded_at": now, "actor": actor}


async def link_supersession(db_path: str | None, old_id: str, new_id: str, actor: str,
                            reason: str | None = None,
                            operator_directive_ref: dict | None = None) -> dict:
    path = _resolve_path(db_path)
    return await _write_with_lock_retry(_sync_link_supersession, path, old_id, new_id,
                                        actor, reason, operator_directive_ref)


async def resolve_id(db_path: str | None, doc_id: str) -> dict:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_resolve_id, path, doc_id)


def _sync_count_docs(db_path: str) -> int:
    conn = _get_or_create_conn(db_path)
    return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


async def count_docs(db_path: str | None) -> int:
    """Corpus size, so a windowed listing can say whether it was truncated."""
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_count_docs, path)


async def update(db_path: str | None, doc_id: str, content: str | None, title: str | None,
                 tags: list[str] | None, metadata: dict | None,
                 embedding: list[float] | None,
                 expect_content: str | None = None,
                 provider_token_count: int | None = None,
                 valid_until: float | None | _Unset = UNSET,
                 authored_by: str | None | _Unset = UNSET) -> dict | None:
    """`expect_content` is an optimistic-concurrency guard for append. [v0.4.0]

    An append is read-modify-write, so two concurrent appends can silently drop
    one. Passing the content the caller read makes that collision VISIBLE —
    `_sync_update` returns `{"conflict": True}` instead of overwriting. Silence
    is the failure this whole change exists to remove.
    """
    path = _resolve_path(db_path)
    result = await _write_with_lock_retry(_sync_update, path, doc_id, content, title,
                                          tags, metadata, embedding, expect_content,
                                          provider_token_count, valid_until, authored_by)
    # ⛔ Only re-index a write that actually landed. `None` is "no such doc" and
    # `{"conflict": True}` is an optimistic-concurrency REFUSAL — in both cases the
    # stored content is unchanged, and re-chunking `content` would install passages
    # for text the document does not contain. That is strictly worse than the stale
    # chunks this hook exists to replace: it would rank a memo on words no version
    # of it ever held.
    if result is not None and not result.get("conflict"):
        await _index_passages_after_write(path, doc_id, content)
    return result


async def delete(db_path: str | None, doc_id: str, *, actor: str = "unknown",
                 reason: str = "unspecified", replaced_by: str | None = None) -> bool:
    """Delete a memo, snapshotting it first. [001/FR-028a]

    `actor` and `reason` default to "unknown"/"unspecified" rather than being
    required, so no existing caller breaks — but an unattributed deletion is
    exactly what the log exists to make visible, and those defaults are meant to
    show up in an audit as work still to do.
    """
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_delete, path, doc_id, actor=actor,
                                   reason=reason, replaced_by=replaced_by)


def _sync_record_injection(db_path: str, session_id: str, *, fire_point: str,
                            agent_family: str | None, project: str | None,
                            injected_ok: bool, injected_tokens: int | None) -> None:
    conn = _get_or_create_conn(db_path)
    conn.execute(
        "INSERT INTO injection_log (session_id, observed_at, fire_point, "
        "agent_family, project, injected_ok, injected_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, time(), fire_point, agent_family, project,
         1 if injected_ok else 0, injected_tokens),
    )
    conn.commit()


def _sync_injection_log(db_path: str, since: float | None, limit: int) -> list[dict]:
    conn = _get_or_create_conn(db_path)
    sql = "SELECT * FROM injection_log"
    params: list = []
    if since is not None:
        sql += " WHERE observed_at >= ?"
        params.append(since)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


async def record_injection(session_id: str, *, fire_point: str,
                            agent_family: str | None = None,
                            project: str | None = None,
                            injected_ok: bool = True,
                            injected_tokens: int | None = None) -> None:
    """Record that a session came back from a compaction. [001/FR-044]

    Best-effort and NEVER raises: this rides the session-start critical path,
    and a ledger write failing must not stop a session from getting its rules.
    An observer that can break the thing it observes is worse than no observer.
    """
    try:
        await asyncio.to_thread(_sync_record_injection, global_path(), session_id,
                                fire_point=fire_point, agent_family=agent_family,
                                project=project, injected_ok=injected_ok,
                                injected_tokens=injected_tokens)
    except Exception:
        logger.exception("compaction ledger write failed for %s — continuing", session_id)


async def injection_log(since: float | None = None, limit: int = 500) -> list[dict]:
    """Read the ledger, for reconciliation against ATC's delivery record."""
    return await asyncio.to_thread(_sync_injection_log, global_path(), since, limit)


def _sync_tombstone(db_path: str, doc_id: str) -> dict | None:
    """What a DELETED memo can still tell a caller. [Ben 2026-10-20, Slack #memo 19:36]

    ⛔ WHY A DELETED MEMO MUST SAY MORE THAN 404. FastAPI answers an UNKNOWN PATH
    and a DELETED DOCUMENT with the same bare 404, so a caller holding a dead id
    cannot tell "this never existed" from "this is gone", and when it IS gone
    nothing says where the replacement is. Measured 2026-10-20: two independent
    hosts invented `GET /documents/<id>/lineage` rather than find `/current`,
    because a retired memo answered 200 and named no successor (memo f93eba87).

    ⭐ TWO OUTCOMES, KEPT DISTINCT, because Ben named them as two cases: a
    deletion that IS a supersession carries `replaced_by`; a dead or wrong memo
    carries none and says so. "Deleted and not replaced" is a real answer and
    must not read as "the pointer was lost". Measured on the live corpus the day
    this shipped: 4,846 deletion-log rows, 21 with a successor and 4,825 without
    — so the no-successor branch is the COMMON one, not the edge case.

    ⚠️ Prefix-tolerant on purpose: an 8-char id is what actually circulates, and
    a deleted memo's id is exactly the kind that arrives abbreviated in a pin or
    a DM. Ambiguity returns None rather than guessing — the caller then gets the
    ordinary 404, which is honest, instead of a tombstone for the wrong memo.
    """
    conn = _get_or_create_conn(db_path)
    cols = ("doc_id, deleted_at, title, actor, reason, replaced_by")
    row = conn.execute(
        f"SELECT {cols} FROM deletion_log WHERE doc_id = ? "
        "ORDER BY deleted_at DESC LIMIT 1", (doc_id,)).fetchone()
    if row is None and len(doc_id) < 36 and doc_id:
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT doc_id FROM deletion_log WHERE doc_id LIKE ? LIMIT 2",
            (doc_id + "%",))]
        if len(ids) != 1:
            return None
        row = conn.execute(
            f"SELECT {cols} FROM deletion_log WHERE doc_id = ? "
            "ORDER BY deleted_at DESC LIMIT 1", (ids[0],)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["replaced"] = d.get("replaced_by") is not None
    return d


def _sync_successor(db_path: str, doc_id: str) -> dict:
    """The next version of this memo, and the newest, from `supersede_edges`.

    ⭐ `superseded_by` is the DIRECT successor; `lineage_tip` is the end of the
    chain. They differ whenever a memo has been replaced more than once, and the
    tip is what a reader actually wants — following one hop at a time through a
    three-link chain is how a caller ends up quoting the middle version.
    `lineage_tip` is None on a live tip so the field never says "go here" about
    the document you are already reading.
    """
    conn = _get_or_create_conn(db_path)
    chain = _lineage_chain(conn, doc_id)
    if doc_id not in chain:
        return {"superseded_by": None, "lineage_tip": None}
    i = chain.index(doc_id)
    nxt = chain[i + 1] if i + 1 < len(chain) else None
    tip = chain[-1] if chain[-1] != doc_id else None
    return {"superseded_by": nxt, "lineage_tip": tip}


async def tombstone(db_path: str | None, doc_id: str) -> dict | None:
    """Deletion record for a dead id, or None if nothing was ever deleted under it."""
    return await asyncio.to_thread(_sync_tombstone, _resolve_path(db_path), doc_id)


async def successor(db_path: str | None, doc_id: str) -> dict:
    """`{superseded_by, lineage_tip}` for a live memo."""
    return await asyncio.to_thread(_sync_successor, _resolve_path(db_path), doc_id)


def _sync_successor_map(db_path: str, doc_ids: list[str]) -> dict[str, dict]:
    """`{superseded_by, lineage_tip}` for MANY ids, in one pass over the edges.

    ⛔⛔ WHY THIS EXISTS, AND IT IS A DEFECT I SHIPPED. `GET /documents/<id>`
    computed these two fields; the LIST route did not, and `Document` defaults
    them to None. So the list answered `superseded_by: null` for all 11,599
    documents, including the 402 that have a real successor edge — correct one
    at a time, false in bulk.

    ⭐ THAT IS WORSE THAN OMITTING THE FIELD. A null here is indistinguishable
    from "no successor", and scanning `/documents` is the mandated way to make
    an existence claim about this corpus, precisely because `/search` can be
    silent for other reasons. A field that is present and always empty converts
    every bulk reader into a confident wrong answer, with nothing to notice.

    One SELECT over the whole edge table (402 rows) rather than a walk per
    document: the per-id helper is O(chain) per call and the list can ask for
    20,000 ids.
    """
    conn = _get_or_create_conn(db_path)
    nxt: dict[str, str] = {}
    # ⛔ ORDER BY superseded_at IS LOAD-BEARING, NOT TIDINESS. `_lineage_chain`
    # resolves a fork with `ORDER BY superseded_at LIMIT 1`, so the bulk pass
    # must apply the identical rule or the two surfaces disagree about exactly
    # the ids where it matters. Without it, SQLite's row order is unspecified
    # and the list could contradict the single GET intermittently — the worst
    # version of this bug, because it would not reproduce.
    for old, new in conn.execute(
            "SELECT old_id, new_id FROM supersede_edges ORDER BY superseded_at"):
        # An append-only log with no FK constraints can hold more than one edge
        # out of a node, and the corpus has one: c2ae186a was continued by TWO
        # memos, both of which said so in their own titles, and a 2026-09-28
        # backfill recorded both 5ms apart. Neither is "the" successor -- that
        # is a one->many continuation, which this edge table cannot express.
        # Taking the earliest is arbitrary but it is the SAME arbitrary choice
        # the per-id walk makes.
        nxt.setdefault(old, new)

    out: dict[str, dict] = {}
    for doc_id in doc_ids:
        tip, seen = doc_id, {doc_id}
        while tip in nxt and nxt[tip] not in seen:
            tip = nxt[tip]
            seen.add(tip)
        out[doc_id] = {
            "superseded_by": nxt.get(doc_id),
            "lineage_tip": tip if tip != doc_id else None,
        }
    return out


async def successor_map(db_path: str | None, doc_ids: list[str]) -> dict[str, dict]:
    return await asyncio.to_thread(_sync_successor_map, _resolve_path(db_path), doc_ids)


async def restore_snapshot(db_path: str | None, doc_id: str) -> dict | None:
    """The most recent deletion snapshot for a memo, or None. [001/FR-028a]"""
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_restore, path, doc_id)


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
    # ⛔ JOIN TO documents. doc_access rows SURVIVE deletion by design (they are the
    # read history of a doc that no longer exists), so counting the table alone put
    # deleted docs in the numerator and live docs in the denominator: coverage_pct
    # read 141.7% on 2026-10-20, from 16,442 access rows over 11,599 live docs, all
    # 4,846 of the excess being rows whose doc_id is in deletion_log. A percentage
    # over 100 is at least loud; the same mismatch at 95% would have been believed.
    total_with_access = conn.execute(
        """SELECT COUNT(*) FROM doc_access a JOIN documents d ON d.id = a.doc_id
            WHERE a.get_count > 0"""
    ).fetchone()[0]
    # Kept rather than dropped: reads against since-deleted docs are real traffic,
    # and silently discarding them trades a wrong number for a missing one.
    reads_on_deleted = conn.execute(
        """SELECT COUNT(*) FROM doc_access a
            LEFT JOIN documents d ON d.id = a.doc_id
            WHERE d.id IS NULL AND a.get_count > 0"""
    ).fetchone()[0]

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
            LIMIT ?""",
        (hot_cutoff, limit),
    ).fetchall()

    return {
        "as_of": time(),
        "total_docs": total_docs,
        "total_with_any_get": total_with_access,
        "coverage_pct": round(100.0 * total_with_access / max(total_docs, 1), 1),
        "reads_on_deleted_docs": reads_on_deleted,
        "stale_days_threshold": stale_days,
        "hot_limit": limit,
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


# ==============================================================================
# v2 bi-temporal helpers (T019)
#
# Versioning model, per data-model.md: a supersession does NOT mutate a row's
# id. Each version is its OWN `documents` row with its own uuid, and the
# old->new transition is recorded in `supersede_edges`. So "the memo" is a
# CHAIN of rows, and any id in that chain is a valid handle to the lineage.
# That is why get_current()/get_as_of() resolve the chain rather than doing a
# bare `WHERE id = ?` — a caller holding a superseded id still deserves the
# right answer instead of None.
# ==============================================================================

# Columns added by migration 001 that hold JSON and therefore need decoding.
_V2_JSON_COLUMNS = (
    "scope", "provenance", "time_scope", "reopenability",
    "derived_from", "constitution_meta",
)


def _row_to_memo(row: sqlite3.Row) -> dict:
    """Row -> dict with v1 *and* v2 JSON columns decoded.

    ``_row_to_dict`` only knows about the v1 ``tags``/``metadata`` columns; the
    v2 additions are also JSON-in-TEXT and would otherwise leak raw strings to
    callers. A column holding non-JSON is downgraded to None with a warning
    rather than raising — one malformed legacy row must not fail a whole read.
    """
    d = _row_to_dict(row)
    for col in _V2_JSON_COLUMNS:
        raw = d.get(col)
        if isinstance(raw, str):
            try:
                d[col] = json.loads(raw)
            except ValueError:
                logger.warning(
                    "memo %s: column %s held non-JSON %r — coercing to None",
                    d.get("id"), col, raw[:120],
                )
                d[col] = None
    return d


def _lineage_chain(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    """Ordered supersede chain (oldest -> newest) containing ``doc_id``.

    Walks ``supersede_edges`` backwards to the lineage root, then forwards to
    the tip. ``doc_id`` itself is always included even when it has no edges (a
    never-superseded memo is a one-element chain). Both walks carry a seen-set
    so a malformed/cyclic edge set terminates instead of spinning forever —
    edges are an append-only audit log with no FK constraints, so a cycle is
    possible in principle and must not hang a request.
    """
    seen = {doc_id}
    root = doc_id
    while True:
        row = conn.execute(
            "SELECT old_id FROM supersede_edges WHERE new_id = ? "
            "ORDER BY superseded_at LIMIT 1",
            (root,),
        ).fetchone()
        if row is None or row["old_id"] in seen:
            break
        root = row["old_id"]
        seen.add(root)

    chain = [root]
    while True:
        row = conn.execute(
            "SELECT new_id FROM supersede_edges WHERE old_id = ? "
            "ORDER BY superseded_at LIMIT 1",
            (chain[-1],),
        ).fetchone()
        if row is None or row["new_id"] in chain:
            break
        chain.append(row["new_id"])
    return chain


def _sync_get_current(db_path: str, doc_id: str) -> dict | None:
    """Newest currently-valid version of the lineage containing ``doc_id``. [001/FR-002]

    ``valid_until IS NULL`` is the definition of "currently true" (FR-002).
    Searched tip-first so the freshest current row wins.
    """
    conn = _get_or_create_conn(db_path)
    for candidate in reversed(_lineage_chain(conn, doc_id)):
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND valid_until IS NULL",
            (candidate,),
        ).fetchone()
        if row is not None:
            return _row_to_memo(row)
    return None


def _sync_get_as_of(db_path: str, doc_id: str, t: float) -> dict | None:
    """Version of the ``doc_id`` lineage that was true at time ``t``. [001/FR-002]

    Window is half-open — ``valid_from <= t < valid_until`` — so the instant of
    a supersession belongs to the NEW version, never to both. A NULL
    ``valid_until`` means the window is still open.
    """
    conn = _get_or_create_conn(db_path)
    for candidate in reversed(_lineage_chain(conn, doc_id)):
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND valid_from <= ? "
            "AND (valid_until IS NULL OR valid_until > ?)",
            (candidate, t, t),
        ).fetchone()
        if row is not None:
            return _row_to_memo(row)
    return None


def _sync_supersede(db_path: str, old_id: str, new_memo: dict,
                    embedding: list[float], actor: str, reason: str | None,
                    operator_directive_ref: dict | None) -> dict | None:
    """Atomically close out ``old_id`` and write its replacement. [001/FR-003]

    Per FR-003 the close and the create are ONE transaction: ``old.valid_until``
    and ``new.valid_from`` are the same instant, so a reader can never observe a
    gap (both rows superseded) or an overlap (both rows current). Returns None if
    ``old_id`` does not exist or is already superseded — the caller turns that
    into a 404/409 rather than silently forking the lineage.
    """
    conn = _get_or_create_conn(db_path)
    old = conn.execute(
        "SELECT id, valid_until FROM documents WHERE id = ?", (old_id,)
    ).fetchone()
    if old is None:
        return None
    if old["valid_until"] is not None:
        logger.warning("supersede: %s is already superseded — refusing", old_id)
        return None

    new_id = str(uuid.uuid4())
    now = time()
    content = new_memo["content"]
    token_count = _count_tokens(content)

    try:
        conn.execute(
            "UPDATE documents SET valid_until = ?, updated_at = ? WHERE id = ?",
            (now, now, old_id),
        )
        conn.execute(
            "INSERT INTO documents ("
            "  id, content, title, tags, metadata, token_count,"
            "  created_at, updated_at, class, injection_mode, scope, provenance,"
            "  valid_from, valid_until, expires_at, time_scope, reopenability,"
            "  derived_from, constitution_meta"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                new_id,
                content,
                new_memo.get("title"),
                json.dumps(new_memo.get("tags") or []),
                json.dumps(new_memo.get("metadata") or {}),
                token_count,
                now,
                now,
                new_memo.get("class") or "fact",
                new_memo.get("injection_mode") or "on-recall",
                json.dumps(new_memo.get("scope") or ["global"]),
                json.dumps(new_memo["provenance"]) if new_memo.get("provenance") is not None else None,
                now,
                new_memo.get("expires_at"),
                json.dumps(new_memo["time_scope"]) if new_memo.get("time_scope") is not None else None,
                json.dumps(new_memo["reopenability"]) if new_memo.get("reopenability") is not None else None,
                json.dumps(new_memo.get("derived_from") or []),
                json.dumps(new_memo["constitution_meta"]) if new_memo.get("constitution_meta") is not None else None,
            ),
        )
        conn.execute(
            "INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
            (new_id, _serialize_vector(embedding)),
        )
        cur = conn.execute(
            "INSERT INTO supersede_edges ("
            "  old_id, new_id, superseded_at, actor, reason, operator_directive_ref"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                old_id, new_id, now, actor, reason,
                json.dumps(operator_directive_ref) if operator_directive_ref is not None else None,
            ),
        )
        edge_id = cur.lastrowid
        conn.commit()
    except Exception:
        # Roll the close-out back with the create — a half-applied supersede
        # would leave the lineage with either two current rows or none.
        conn.rollback()
        logger.exception("supersede %s -> %s failed; rolled back", old_id, new_id)
        raise

    return {
        "old_id": old_id,
        "new_id": new_id,
        "superseded_at": now,
        "edge_id": edge_id,
    }


def _sync_reap_expired(db_path: str, now: float | None = None) -> list[str]:
    """Hard-delete rows whose ``expires_at`` has passed. [001/FR-007]

    Returns the reaped ids. Embeddings go with the row — leaving them behind
    would keep reaped content semantically searchable, which is the whole point
    of a TTL. ``now`` is injectable so tests need not sleep.
    """
    conn = _get_or_create_conn(db_path)
    cutoff = time() if now is None else now
    rows = conn.execute(
        "SELECT id FROM documents WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (cutoff,),
    ).fetchall()
    reaped = [r["id"] for r in rows]
    if not reaped:
        return []
    for doc_id in reaped:
        conn.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    logger.info("reaper: swept %d expired memo(s)", len(reaped))
    return reaped


# --- Async wrappers for the v2 helpers ---

async def get_current(db_path: str | None, doc_id: str) -> dict | None:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_get_current, path, doc_id)


async def get_as_of(db_path: str | None, doc_id: str, t: float) -> dict | None:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_get_as_of, path, doc_id, t)


async def supersede(db_path: str | None, old_id: str, new_memo: dict,
                    embedding: list[float], actor: str,
                    reason: str | None = None,
                    operator_directive_ref: dict | None = None) -> dict | None:
    """Close out ``old_id`` and write its replacement, INDEXING THE REPLACEMENT.

    ⛔⛔ THE THIRD WRITE PATH. `_index_passages_after_write` calls itself hooked
    "at the choke point, not at the call sites" — but the choke point was `store`
    and `update`, and supersede CREATES A DOCUMENT without going through either.
    From 2026-08-19 (when inline indexing landed) to 2026-08-23 every memo born
    here had ZERO passages: measured 2 of the corpus's 4 supersede-created docs
    still unindexed, and the other 2 saved only by a later edit that happened to
    run `update`. `8358fc6a` (2026-08-20) was written up as an *unexplained*
    indexing miss and sat open for three days; it was this.

    ⭐ THE HOOK'S OWN DOCSTRING PREDICTED THIS AND MISCOUNTED. It says the
    guarantee "cannot rest on eleven write call sites all remembering" and then
    names "eleven callers of `store` and `update`, and exactly one of each of
    them" — true, and it does not cover a writer that calls NEITHER. **A choke
    point is only a choke point for the paths that flow through it**; hooking two
    functions and calling it centralised is the same bet as hooking eleven, just
    with a shorter list. The check that finds this is enumerating the writers of
    `documents`, not counting the callers of the hook.

    ⚠️ Deliberately AFTER the transaction, not inside it, and non-fatal for the
    same reason as `store`'s: a supersession that RAISED because the embedder was
    unreachable would leave the caller's correction unrecorded, and there is no
    retry queue. Stored-but-unindexed is recoverable; lost is not. `/ready`'s
    `passage_index_gap` is what keeps the hole visible.
    """
    path = _resolve_path(db_path)
    result = await asyncio.to_thread(
        _sync_supersede, path, old_id, new_memo, embedding, actor, reason,
        operator_directive_ref,
    )
    if result is not None:
        await _index_passages_after_write(
            path, result["new_id"], new_memo.get("content"))
    return result


async def reap_expired(db_path: str | None = None, now: float | None = None) -> list[str]:
    path = _resolve_path(db_path)
    return await asyncio.to_thread(_sync_reap_expired, path, now)
