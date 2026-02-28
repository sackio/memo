import asyncio
import json
import sqlite3
import struct
import uuid
from pathlib import Path
from time import time

import sqlite_vec
import tiktoken

from memo.config import settings

_connections: dict[str, sqlite3.Connection] = {}
_tokenizer = tiktoken.get_encoding("cl100k_base")


def _resolve_path(db_path: str | None) -> str:
    if db_path:
        p = Path(db_path)
        if p.suffix in (".db", ".sqlite", ".sqlite3"):
            # Explicit DB file — use as-is
            return db_path
        # Directory path — encode as a safe filename within the data volume
        # e.g. /mnt/nas/data/files → <data_dir>/mnt_nas_data_files.memo.db
        safe_name = str(p).strip("/").replace("/", "_")
        data_dir = Path(settings.resolved_default_db_path).parent
        return str(data_dir / f"{safe_name}.memo.db")
    return settings.resolved_default_db_path


def global_path() -> str:
    return settings.resolved_default_db_path


def _count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text))


def _get_or_create_conn(db_path: str) -> sqlite3.Connection:
    if db_path in _connections:
        return _connections[db_path]

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    _init_schema(conn)
    _connections[db_path] = conn
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
    """)
    # Migration: add token_count to existing DBs that predate this column
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "token_count" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN token_count INTEGER NOT NULL DEFAULT 0")
    conn.commit()


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
    updated = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_dict(updated)


def _sync_search(db_path: str, embedding: list[float], limit: int, min_score: float | None,
                 tags: list[str], after: float | None, before: float | None,
                 min_tokens: int | None, max_tokens: int | None) -> list[dict]:
    conn = _get_or_create_conn(db_path)
    has_filters = bool(tags) or after or before or min_tokens or max_tokens
    rows = conn.execute(
        "SELECT de.doc_id, de.distance "
        "FROM document_embeddings de "
        "WHERE de.embedding MATCH ? AND k = ? "
        "ORDER BY de.distance",
        (_serialize_vector(embedding), limit * 5 if has_filters else limit),
    ).fetchall()

    results = []
    for row in rows:
        doc_id, distance = row["doc_id"], row["distance"]
        score = 1.0 - distance
        if min_score is not None and score < min_score:
            continue
        doc_row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if doc_row is None:
            continue
        doc = _row_to_dict(doc_row)
        if not _matches_filters(doc, tags, after, before, min_tokens, max_tokens):
            continue
        results.append({"document": doc, "score": score})
        if len(results) >= limit:
            break
    return results


def _sync_get(db_path: str, doc_id: str) -> dict | None:
    conn = _get_or_create_conn(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _sync_delete(db_path: str, doc_id: str) -> bool:
    conn = _get_or_create_conn(db_path)
    cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    conn.commit()
    return cur.rowcount > 0


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

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    fetch_limit = limit * 3 if tags else limit
    params.append(fetch_limit)

    rows = conn.execute(
        f"SELECT * FROM documents {where} ORDER BY created_at DESC LIMIT ?", params
    ).fetchall()

    results = []
    for row in rows:
        doc = _row_to_dict(row)
        if tags and not any(t in doc["tags"] for t in tags):
            continue
        results.append(doc)
        if len(results) >= limit:
            break
    return results


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
    for result in per_db:
        if isinstance(result, Exception):
            continue
        for doc in result:
            if doc["id"] not in seen:
                seen.add(doc["id"])
                merged.append(doc)
    merged.sort(key=lambda x: x["created_at"], reverse=True)
    return merged[:limit]
