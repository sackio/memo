import asyncio
import contextlib
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from memo import db, embeddings
from memo.config import settings
from memo.db import _count_tokens, _truncate_to_tokens
from memo.models import (
    AutoStoreRequest,
    AutoStoreResponse,
    ContextRequest,
    ContextResponse,
    CopyMoveRequest,
    CopyMoveResponse,
    DeleteResponse,
    Document,
    SearchRequest,
    SearchResult,
    StoreRequest,
    StoreResponse,
    UpdateRequest,
)

# --- MCP server ---

mcp = FastMCP(
    "memo",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
mcp_starlette = mcp.streamable_http_app()
mcp_starlette.router.lifespan_context = lambda app: contextlib.AsyncExitStack()


# --- Malformed-write guard (v0.3.1, 2026-07-21) ---

# Observed 2026-07-21: memo_store/memo_update calls arriving with the caller's tool-call syntax
# leaked into `content` — the body ended with a literal `</content><parameter name="tags">[...]`
# blob (or a `<tags>[...]</tags></invoke>` variant) — and `tags` arrived empty. The corruption
# happens UPSTREAM of this server (we do no parsing; we store what we're handed), so we cannot
# prevent it here. But storing the result silently produces a memo that IS unreachable by
# tag-filtered search: a silent hole in the knowledge base that nothing reports.
#
# Sweep-repair of the corruption is unsafe — a predicate that matches the marker cannot
# distinguish a memo *about* the bug from a memo *containing* it, and auto-repair through the
# same write path we're guarding is what damaged alpaca's memo 3fce1547 on 2026-07-21. So this
# guard REFUSES the write instead of repairing it. Loud failure the caller can retry beats a
# silent unreachable record.
#
# Deliberately NARROW to avoid blocking legitimate writes (including memos that discuss this
# very bug). All three conditions must hold:
#   1. `</content>` appears in the LAST 400 chars
#   2. Any of the associated tool-call fragments (`<parameter name=`, `<tags>`, `</invoke>`)
#      appears in that same tail window
#   3. Tags are empty (None or [])
#
# Condition 3 is LOAD-BEARING — a memo about tool-call syntax with any tag survives. Do NOT
# relax it while widening (2) (alpaca 2026-07-21).

_LEAK_MARKER = "</content>"
_LEAK_FRAGMENTS = ("<parameter name=", "<tags>", "</invoke>")


async def _reject_leaked_tool_call(content: str | None, tags: list[str] | None,
                                    endpoint: str = "unknown",
                                    user_agent: str | None = None,
                                    source_ip: str | None = None) -> None:
    """Refuse a write whose content shows the truncated-tool-call fingerprint.

    All three conditions must hold to reject:
      - `</content>` in the last 400 chars
      - Any of `<parameter name=`, `<tags>`, `</invoke>` in that same tail
      - Tags is None or empty list (this condition is load-bearing — see comment above)

    Every rejection is logged to `leak_incidents` for later diagnosis of the
    upstream client-side corruption (v0.3.1 addition — the guard is the
    mitigation, this log is the passive detector).
    """
    if not content or tags:
        return
    tail = content[-400:]
    if _LEAK_MARKER not in tail:
        return
    matched_fragment = next((f for f in _LEAK_FRAGMENTS if f in tail), None)
    if matched_fragment is None:
        return
    tags_state = "None" if tags is None else "empty-list"
    # Log first so we always have the incident on disk even if the caller ignores the raise.
    try:
        await db.log_leak(endpoint, matched_fragment, tags_state, content,
                          user_agent=user_agent, source_ip=source_ip)
    except Exception:
        pass  # never let logging break the guard's primary job
    raise ValueError(
        "memo: refusing a malformed write. The content ends with leaked tool-call syntax "
        f"({_LEAK_MARKER}...{matched_fragment}...) and `tags` arrived empty, which means the "
        "call was corrupted in transit and the `tags` argument was absorbed into `content`. "
        "Storing it would create a memo that is UNREACHABLE by tag-filtered search. "
        "Re-send the call with `content` and `tags` as separate arguments, then verify with "
        "memo_get AND a tag-filtered memo_search before trusting the record."
    )


# --- MCP Tools ---

@mcp.tool()
async def memo_store(
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> dict:
    """Store a document with automatic embedding and token count.

    db_path: ACCEPTED AND IGNORED. There is one database — the global DB on
    server4 — and every request routes to it (2026-06-29 single-global
    refactor). The parameter is kept so existing callers don't break; passing
    a path does NOT write to a per-directory .memo.db.
    """
    await _reject_leaked_tool_call(content, tags, "memo_store")
    _t0 = _time.time()
    embedding = await embeddings.embed(content)
    doc_id = await db.store(
        db_path=db_path,
        content=content,
        title=title,
        tags=tags or [],
        metadata=metadata or {},
        embedding=embedding,
    )
    await db.log_query_async(
        db_path, "store", query=title or (content or "")[:200], tags=tags or [],
        result_ids=[doc_id], latency_ms=(_time.time() - _t0) * 1000,
        user_agent="mcp:memo_store")
    return {"id": doc_id}


@mcp.tool()
async def memo_update(
    id: str,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> dict | None:
    """Update an existing memo by ID. Only provided fields are changed.

    If content is updated, the embedding and token_count are recomputed automatically.

    Returns the updated memo with `updated: true`. If the ID matched nothing,
    returns `{updated: false, reason: "not_found", requested_id: <id>}` —
    NOT null. A bare null could not distinguish a mistyped id from a memo that
    has genuinely vanished, and two of those readings are alarming while one is
    a typo. Check `updated`; a false means re-look-up the id, not that the memo
    is gone.
    """
    await _reject_leaked_tool_call(content, tags, "memo_update")
    embedding = await embeddings.embed(content) if content is not None else None
    result = await db.update(
        db_path=db_path,
        doc_id=id,
        content=content,
        title=title,
        tags=tags,
        metadata=metadata,
        embedding=embedding,
    )
    await db.log_query_async(
        db_path, "update", query=title or id, tags=tags or [],
        result_ids=[id] if result is not None else [], user_agent="mcp:memo_update")
    if result is None:
        # 2026-07-30: was a bare null, which collapsed "bad id", "memo absent"
        # and (from the caller's seat) "applied, nothing returned" into one
        # answer. Session-ids and memo-ids are both 36-char UUIDs, so passing
        # the wrong KIND of id lands here too and looked identical.
        return {"updated": False, "reason": "not_found", "requested_id": id}
    return {**result, "updated": True}


@mcp.tool()
async def memo_search(
    query: str,
    limit: int = 10,
    min_score: float | None = None,
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    db_path: str | None = None,
    scope: str = "local",
) -> list[dict]:
    """Search documents by semantic similarity with optional filters.

    db_path / scope: ACCEPTED AND IGNORED. Since the 2026-06-29 single-global
    refactor there is exactly ONE database, and "local", "global" and "all"
    all search it — they are the same code path, not three. Both parameters
    are kept for backward compatibility. If you are choosing a scope to
    control WHICH memos you see, that choice has no effect; filter on tags
    instead.

    Filters:
    - tags: only return docs that have at least one of these tags
    - after/before: Unix timestamps bounding created_at
    - min_tokens/max_tokens: bound by stored token_count of content
    """
    # ⛔ LOGGED HERE, NOT ONLY ON THE HTTP ROUTE. Agents reach memo through the
    # MCP tool, which calls db directly — logging only POST /search would
    # capture a small, unrepresentative slice and the bias would be invisible
    # in the resulting numbers. [v0.3.8]
    _t0 = _time.time()
    embedding = await embeddings.embed(query)
    kwargs = dict(embedding=embedding, limit=limit, min_score=min_score, tags=tags or [],
                  after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        out = await db.search(db_path=None, **kwargs)
    elif scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        out = await db.search_multi(paths, **kwargs)
    else:
        out = await db.search(db_path=db_path, **kwargs)

    await db.log_query_async(
        db_path, "search", query=query, arg_limit=limit, tags=tags or [],
        result_ids=[r["document"]["id"] for r in out],
        result_scores=[r["score"] for r in out],
        latency_ms=(_time.time() - _t0) * 1000, user_agent="mcp:memo_search")
    return out


@mcp.tool()
async def memo_get(id: str, db_path: str | None = None) -> dict | None:
    """Retrieve a document by ID.

    db_path: ACCEPTED AND IGNORED — one global DB since 2026-06-29.
    """
    return await db.get(db_path=db_path, doc_id=id)


@mcp.tool()
async def memo_delete(id: str, db_path: str | None = None) -> dict:
    """Delete a document by ID.

    db_path: ACCEPTED AND IGNORED — one global DB since 2026-06-29.
    """
    deleted = await db.delete(db_path=db_path, doc_id=id)
    return {"deleted": deleted}


@mcp.tool()
async def memo_copy(
    id: str,
    to_db_path: str | None = None,
    from_db_path: str | None = None,
) -> dict | None:
    """NO-OP since the 2026-06-29 single-global refactor — there is only one
    database, so there is nowhere to copy TO.

    Returns {id: <the same id>, copied: false, reason: "single_global_db"} if
    the memo exists, or {copied: false, reason: "not_found"} if it doesn't.
    It does NOT duplicate the memo and never has since June. The id coming back
    is the ORIGINAL — do not read it as a new copy.
    """
    new_id = await db.copy(from_db_path=from_db_path, doc_id=id, to_db_path=to_db_path)
    if not new_id:
        return {"copied": False, "reason": "not_found", "requested_id": id}
    # The response carries the truth because the docstring can't: MCP tool
    # descriptions are cached per session at startup, so a long-running session
    # still reads the pre-2026-07-30 text promising a new uuid. The response is
    # the only channel that reaches a caller with stale docs.
    return {"id": new_id, "copied": False, "reason": "single_global_db"}


@mcp.tool()
async def memo_move(
    id: str,
    to_db_path: str | None = None,
    from_db_path: str | None = None,
) -> dict | None:
    """NO-OP since the 2026-06-29 single-global refactor — there is only one
    database, so there is nowhere to move TO.

    Returns {id: <the same id>, moved: false, reason: "single_global_db"} if the
    memo exists, or {moved: false, reason: "not_found"} if it doesn't. The memo
    is not relocated and nothing is deleted.
    """
    new_id = await db.move(from_db_path=from_db_path, doc_id=id, to_db_path=to_db_path)
    if not new_id:
        return {"moved": False, "reason": "not_found", "requested_id": id}
    return {"id": new_id, "moved": False, "reason": "single_global_db"}


@mcp.tool()
async def memo_list(
    query: str | None = None,
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    limit: int = 100,
    min_score: float | None = None,
    db_path: str | None = None,
    scope: str = "local",
) -> list[dict]:
    """List documents with optional filters.

    When query is provided, uses OpenRouter vector embeddings to rank results by
    semantic similarity (same engine as memo_search). Without query, returns
    documents in reverse-chronological order via SQL.

    db_path / scope: ACCEPTED AND IGNORED — one global DB since the 2026-06-29
    single-global refactor. "local", "global" and "all" all list from it.

    Filters:
    - tags: only return docs that have at least one of these tags
    - after/before: Unix timestamps bounding created_at
    - min_tokens/max_tokens: bound by stored token_count of content
    - min_score: minimum cosine similarity (only applies when query is provided)
    """
    if query is not None:
        embedding = await embeddings.embed(query)
        kwargs = dict(embedding=embedding, limit=limit, min_score=min_score, tags=tags or [],
                      after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens)
        if scope == "global" or (scope == "local" and db_path is None):
            results = await db.search(db_path=None, **kwargs)
        elif scope == "all" and db_path is not None:
            paths = list({db.global_path(), db._resolve_path(db_path)})
            results = await db.search_multi(paths, **kwargs)
        else:
            results = await db.search(db_path=db_path, **kwargs)
        return [r["document"] for r in results]

    kwargs = dict(tags=tags or [], limit=limit, after=after, before=before,
                  min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        return await db.list_docs(db_path=None, **kwargs)

    if scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        return await db.list_docs_multi(paths, **kwargs)

    return await db.list_docs(db_path=db_path, **kwargs)


@mcp.tool()
async def memo_context(
    query: str,
    token_budget: int = 4000,
    queries: list[str] | None = None,
    limit_per_query: int = 10,
    min_score: float | None = None,
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    db_path: str | None = None,
    scope: str = "local",
) -> dict:
    """Search memo with one or more query angles in parallel, deduplicate results,
    and return content formatted to fit within a token budget.

    Designed for context retrieval without flooding the caller's context window —
    all intermediate results are collapsed into a single budgeted string.

    queries: optional list of additional search angles run alongside query.
             More angles = better recall at the cost of more embedding calls.
    token_budget: maximum tokens in the returned content string. If the
                  top-ranked memo alone exceeds it, you get that memo excerpted
                  and marked, never an empty result.
    db_path / scope: ACCEPTED AND IGNORED — one global DB since the 2026-06-29
                  single-global refactor. "local", "global" and "all" all
                  search it.

    Returns:
      content: formatted markdown string of results within budget
      token_count: actual token count of content
      doc_count: number of memos INCLUDED in content
      matched_count: number of memos that MATCHED the query. doc_count 0 with
                     matched_count > 0 means the budget was too small for any
                     single memo — NOT that the corpus has nothing on the topic.
      truncated: true if any match was left out or excerpted
    """
    all_queries = [query] + (queries or [])
    search_kwargs = dict(
        limit=limit_per_query, min_score=min_score, tags=tags or [],
        after=after, before=before, min_tokens=None, max_tokens=None,
    )

    # Embed all queries concurrently
    embedding_list = await asyncio.gather(*[embeddings.embed(q) for q in all_queries])

    # Determine search function based on scope
    if scope == "global" or db_path is None:
        async def _search(emb):
            return await db.search(db_path=None, embedding=emb, **search_kwargs)
    elif scope == "all":
        paths = list({db.global_path(), db._resolve_path(db_path)})
        async def _search(emb):
            return await db.search_multi(paths, embedding=emb, **search_kwargs)
    else:
        async def _search(emb):
            return await db.search(db_path=db_path, embedding=emb, **search_kwargs)

    all_results = await asyncio.gather(*[_search(emb) for emb in embedding_list])

    # Deduplicate: keep highest score per doc_id
    best: dict[str, dict] = {}
    for results in all_results:
        for item in results:
            doc_id = item["document"]["id"]
            if doc_id not in best or item["score"] > best[doc_id]["score"]:
                best[doc_id] = item

    ranked = sorted(best.values(), key=lambda x: x["score"], reverse=True)

    # Greedily fill token budget. A doc that doesn't fit is SKIPPED, not
    # terminal: one oversized top-ranked memo must not starve every smaller
    # one below it (2026-07-30 — `break` here returned zero docs whenever the
    # top hit exceeded the budget).
    parts: list[str] = []
    total_tokens = 0
    truncated = False

    for item in ranked:
        doc = item["document"]
        title = doc.get("title") or doc["id"]
        tags_str = (", ".join(doc["tags"]) + " ") if doc["tags"] else ""
        section = f"## {title} {tags_str}(score: {item['score']:.2f})\n{doc['content']}\n"
        section_tokens = _count_tokens(section)
        if total_tokens + section_tokens > token_budget:
            truncated = True
            continue
        parts.append(section)
        total_tokens += section_tokens

    # Nothing fit whole, but the corpus DID match: return the top hit excerpted
    # to the budget rather than "". An empty content string is indistinguishable
    # from "no memos match your query", and that false negative silently strips
    # a caller of grounding it was entitled to.
    if not parts and ranked:
        top = ranked[0]
        doc = top["document"]
        title = doc.get("title") or doc["id"]
        header = f"## {title} (score: {top['score']:.2f}) [excerpt — truncated to fit token_budget]\n"
        # Budget the body against the section's real overhead (header + trailing
        # newline), then re-measure: tokens can merge across a join boundary, so
        # the assembled section is the only count worth trusting.
        allowance = token_budget - _count_tokens(header + "\n")
        section = header + _truncate_to_tokens(doc["content"], allowance) + "\n"
        overage = _count_tokens(section) - token_budget
        if overage > 0:
            section = header + _truncate_to_tokens(doc["content"], allowance - overage) + "\n"
        if _count_tokens(section) <= token_budget and section.strip() != header.strip():
            parts.append(section)
            total_tokens = _count_tokens(section)
            truncated = True

    content = "\n".join(parts)
    # ⭐ `ranked` is what retrieval chose; `parts` is what survived the token
    # budget. Logging the RANKED ids (not the packed ones) keeps this comparable
    # to a plain search — otherwise a replay would be measuring the packer's
    # budget arithmetic and calling it a retrieval difference. [v0.3.8]
    await db.log_query_async(
        db_path, "context", query=query, arg_limit=limit_per_query,
        tags=tags or [],
        result_ids=[r["document"]["id"] for r in ranked],
        result_scores=[r["score"] for r in ranked],
        user_agent="mcp:memo_context")
    return {
        "content": content,
        "token_count": total_tokens,
        "doc_count": len(parts),
        "matched_count": len(ranked),
        "truncated": truncated,
    }


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="memo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/mcp", mcp_starlette)

import os as _os
_ui_dist = Path(_os.environ.get("MEMO_UI_DIST", "/app/ui/dist"))
if _ui_dist.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui_dist), html=True), name="ui")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/documents", response_model=StoreResponse)
async def store_document(req: StoreRequest, request: Request):
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "POST /documents",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _t0 = _time.time()
    embedding = await embeddings.embed(req.content)
    doc_id = await db.store(
        db_path=req.db_path,
        content=req.content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
    )
    await db.log_query_async(
        req.db_path, "store", query=req.title or (req.content or "")[:200],
        tags=req.tags, result_ids=[doc_id],
        latency_ms=(_time.time() - _t0) * 1000,
        user_agent=request.headers.get("user-agent"),
        source_ip=request.client.host if request.client else None)
    return StoreResponse(id=doc_id)


@app.get("/documents", response_model=list[Document])
async def list_documents(
    query: str | None = Query(default=None),
    tags: list[str] = Query(default=[]),
    after: float | None = Query(default=None),
    before: float | None = Query(default=None),
    min_tokens: int | None = Query(default=None),
    max_tokens: int | None = Query(default=None),
    min_score: float | None = Query(default=None),
    limit: int = Query(default=100),
    db_path: str | None = Query(default=None),
):
    if query is not None:
        embedding = await embeddings.embed(query)
        results = await db.search(
            db_path=db_path, embedding=embedding, limit=limit, min_score=min_score,
            tags=tags, after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens,
        )
        return [Document(**r["document"]) for r in results]
    docs = await db.list_docs(
        db_path=db_path, tags=tags, limit=limit,
        after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens,
    )
    return [Document(**d) for d in docs]


@app.get("/documents/{doc_id}", response_model=Document)
async def get_document(doc_id: str, db_path: str | None = Query(default=None)):
    doc = await db.get(db_path=db_path, doc_id=doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return Document(**doc)


@app.patch("/documents/{doc_id}", response_model=Document)
async def update_document(doc_id: str, req: UpdateRequest, request: Request):
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "PATCH /documents/{id}",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    embedding = await embeddings.embed(req.content) if req.content is not None else None
    result = await db.update(
        db_path=req.db_path,
        doc_id=doc_id,
        content=req.content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Memo not found")
    return Document(**result)


@app.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str, db_path: str | None = Query(default=None)):
    deleted = await db.delete(db_path=db_path, doc_id=doc_id)
    return DeleteResponse(deleted=deleted)


@app.post("/documents/{doc_id}/copy", response_model=CopyMoveResponse)
async def copy_document(doc_id: str, req: CopyMoveRequest):
    new_id = await db.copy(from_db_path=req.from_db_path, doc_id=doc_id, to_db_path=req.to_db_path)
    if new_id is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return CopyMoveResponse(id=new_id)


@app.post("/documents/{doc_id}/move", response_model=CopyMoveResponse)
async def move_document(doc_id: str, req: CopyMoveRequest):
    new_id = await db.move(from_db_path=req.from_db_path, doc_id=doc_id, to_db_path=req.to_db_path)
    if new_id is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return CopyMoveResponse(id=new_id)


@app.post("/search", response_model=list[SearchResult])
async def search_documents(req: SearchRequest, request: Request = None):
    # Timed around the work, logged AFTER the response is built. [v0.3.8]
    # ⛔ The log must never delay or fail the query it records — see
    # db.log_query's docstring. This is live fleet infrastructure.
    _t0 = _time.time()
    embedding = await embeddings.embed(req.query)
    results = await db.search(
        db_path=req.db_path,
        embedding=embedding,
        limit=req.limit,
        min_score=req.min_score,
        tags=req.tags,
        after=req.after,
        before=req.before,
        min_tokens=req.min_tokens,
        max_tokens=req.max_tokens,
    )
    out = [SearchResult(document=Document(**r["document"]), score=r["score"]) for r in results]
    await db.log_query_async(
        req.db_path, "search", query=req.query, arg_limit=req.limit,
        tags=req.tags, result_ids=[r["document"]["id"] for r in results],
        result_scores=[r["score"] for r in results],
        latency_ms=(_time.time() - _t0) * 1000,
        user_agent=(request.headers.get("user-agent") if request else None),
        source_ip=(request.client.host if request and request.client else None))
    return out


@app.get("/index")
async def index_documents(
    db_path: str | None = Query(default=None),
    limit: int = Query(default=200),
):
    docs = await db.list_docs(db_path=db_path, tags=[], limit=limit, after=None, before=None,
                               min_tokens=None, max_tokens=None)
    return [{"id": d["id"], "title": d["title"], "tags": d["tags"],
             "created_at": d["created_at"], "updated_at": d["updated_at"],
             "token_count": d["token_count"]} for d in docs]


@app.post("/admin/recount-tokens")
async def recount_tokens(db_path: str | None = Query(default=None)):
    """Recalculate token_count for docs that have content but token_count=0.

    This fixes documents stored before the token_count column existed or
    created via paths that bypassed token counting.
    """
    result = await db.recount_tokens(db_path=db_path)
    return result


@app.get("/admin/access-stats")
async def access_stats(
    stale_days: int = Query(default=90, description="Days of no access before considered stale"),
    limit: int = Query(default=100),
):
    """L3c 2026-07-05: expose per-doc access counters for utility-based reaping.

    Returns docs that have not been fetched in `stale_days` days AND have no
    writes in that same window — reap candidates for Phase F.
    Also returns a hot-list (most-fetched) + no-fetch-ever docs.
    """
    result = await db.access_stats(stale_days=stale_days, limit=limit)
    return result


@app.get("/admin/leak-incidents")
async def leak_incidents_endpoint(limit: int = Query(default=50, le=500)):
    """v0.3.1 2026-07-21: expose the malformed-write rejection log.

    Every time `_reject_leaked_tool_call` refuses a write, a row is inserted
    into the `leak_incidents` table with the endpoint, matched fragment,
    tag state, and content head/tail. This endpoint returns the most recent
    N incidents so the upstream client-side corruption can be diagnosed
    against a growing corpus rather than one incident at a time.
    """
    return await db.leak_incidents(limit=limit)


@app.post("/auto-store", response_model=AutoStoreResponse)
async def auto_store(req: AutoStoreRequest):
    """Extract knowledge from raw content (e.g. a conversation exchange), deduplicate against
    existing memos, then create a new memo or merge into the closest existing one.

    The LLM first decides if the content is worth storing at all.  If yes, it embeds the
    extracted content and searches for near-duplicates (cosine similarity >=
    settings.auto_store_similarity_threshold).  If a similar memo is found a second LLM call
    decides whether to merge, create a separate memo, or skip entirely.
    """
    from memo.auto_store import analyze_for_store, analyze_for_merge

    # 1. LLM: is this worth storing?
    analysis = await analyze_for_store(req.content)
    if analysis.get("error"):
        # A provider failure is NOT a skip. Nothing was stored and the caller must
        # be able to tell — an agent that reads this as "skipped" will compact or
        # respawn believing it banked state it never banked.
        err = analysis["error"]
        return AutoStoreResponse(
            action="error", error_kind=err.get("kind"), retryable=err.get("retryable", False),
            reason=f"auto-store analysis failed ({err.get('kind')}): {err.get('detail')}",
        )
    if not analysis.get("should_store"):
        return AutoStoreResponse(action="skipped", reason=analysis.get("reason", "not worth storing"))

    extracted = analysis.get("content") or req.content
    title = analysis.get("title")
    tags = analysis.get("tags") or []
    await _reject_leaked_tool_call(extracted, tags, "auto_store:extracted")

    # 2. Embed extracted content and look for near-duplicates
    embedding = await embeddings.embed(extracted)
    similar = await db.search(
        db_path=req.db_path,
        embedding=embedding,
        limit=3,
        min_score=settings.auto_store_similarity_threshold,
        tags=[],
        after=None,
        before=None,
        min_tokens=None,
        max_tokens=None,
    )

    if similar:
        best = similar[0]["document"]

        # 3. LLM: merge into existing, create separate, or skip?
        merge = await analyze_for_merge(best["content"], extracted)
        if merge.get("error"):
            # Falling through to "create" here would silently duplicate the memo
            # we just found. The question "merge or not?" went unanswered, so say
            # so rather than guessing the more destructive way.
            err = merge["error"]
            return AutoStoreResponse(
                action="error", error_kind=err.get("kind"), retryable=err.get("retryable", False),
                reason=f"auto-store merge analysis failed ({err.get('kind')}): {err.get('detail')}",
            )
        action = merge.get("action", "create")

        if action == "skip":
            return AutoStoreResponse(action="skipped", reason=merge.get("reason", "already covered"))

        if action == "merge":
            merged_content = merge.get("merged_content") or extracted
            merged_title = merge.get("title") or title or best.get("title")
            merged_tags = merge.get("tags") or list(dict.fromkeys(tags + best.get("tags", [])))
            await _reject_leaked_tool_call(merged_content, merged_tags, "auto_store:merge")
            merged_embedding = await embeddings.embed(merged_content)
            await db.update(
                db_path=req.db_path,
                doc_id=best["id"],
                content=merged_content,
                title=merged_title,
                tags=merged_tags,
                metadata=None,
                embedding=merged_embedding,
            )
            return AutoStoreResponse(
                action="updated", id=best["id"], title=merged_title,
                reason=merge.get("reason"),
            )
        # action == "create" — fall through

    # 4. Create new memo
    doc_id = await db.store(
        db_path=req.db_path,
        content=extracted,
        title=title,
        tags=tags,
        metadata={},
        embedding=embedding,
    )
    return AutoStoreResponse(action="created", id=doc_id, title=title, reason=analysis.get("reason"))


@app.post("/context", response_model=ContextResponse)
async def context_documents(req: ContextRequest):
    result = await memo_context(
        query=req.query,
        token_budget=req.token_budget,
        queries=req.queries or None,
        limit_per_query=req.limit_per_query,
        min_score=req.min_score,
        tags=req.tags or None,
        after=req.after,
        before=req.before,
        db_path=req.db_path,
        scope=req.scope,
    )
    return ContextResponse(**result)


def main():
    uvicorn.run("memo.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
