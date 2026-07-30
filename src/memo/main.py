import asyncio
import contextlib
import logging
from time import time as _now
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from memo import db, embeddings, log_queries, reaper
from memo.mediators import recall as recall_mediator
from memo.mediators import store as store_mediator
from memo.injection import set as injection_set
from memo.auditor import global_sweep as auditor_sweep
from memo.auditor import actions as auditor_actions
from memo.auditor import proposals as constitution
from memo.config import settings
from memo.db import _count_tokens
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
    MediatorStoreRequest,
    MediatorStoreResponse,
    Provenance,
    RecallRequest,
    RecallResponse,
    StoreRequest,
    StoreResponse,
    SupersedeRequest,
    SupersedeResponse,
    UpdateRequest,
)
from memo.repositories.documents import documents as documents_repo

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
    session_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    operator_directive_ref: dict[str, Any] | None = None,
    clarification_token: str | None = None,
    bypass_mediator: bool = False,
) -> dict:
    """Store a memo. Routed through the storage mediator. [001/FR-015a]

    The v1 tool NAME and its `{"id": ...}` response key are preserved, so
    existing callers keep working (FR-015a). What changed underneath: the write
    now goes through reconcile-before-write, so it may be merged into an
    existing memo, supersede one, or come back asking a question.

    IMPORTANT for callers: `id` can be null. Always check `action`.
      - "write-new"/"merge"/"supersede" -> stored; `id` is the memo to cite.
      - "clarify"  -> nothing stored yet. Read `prompt`, then call again with
                      `clarification_token` plus whatever it asked for
                      (commonly `operator_directive_ref`).
      - "reject"   -> not stored. `reason` says why; `how_to_authorize` says
                      what would make it succeed.

    db_path is accepted for backward compatibility and ignored (single-global).
    """
    await _reject_leaked_tool_call(content, tags, "memo_store")
    result = await store_mediator.store(MediatorStoreRequest(
        content=content,
        title=title,
        tags=tags or [],
        provenance=Provenance(**provenance) if provenance else None,
        session_id=session_id or "mcp:unknown",
        operator_directive_ref=operator_directive_ref,
        clarification_token=clarification_token,
        bypass_mediator=bypass_mediator,
    ))
    payload = result.model_dump(exclude_none=True)
    # v1 compatibility: callers read ["id"]. Keep it, even when null.
    payload["id"] = result.memo_id
    return payload


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
    Returns the updated memo, or null if the ID was not found.
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
    return result


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

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    scope controls which database(s) to search:
    - "local" (default): only the DB specified by db_path (or global if db_path is None)
    - "global": only the global DB, ignoring db_path
    - "all": search both db_path DB and global DB, merge results by score

    Filters:
    - tags: only return docs that have at least one of these tags
    - after/before: Unix timestamps bounding created_at
    - min_tokens/max_tokens: bound by stored token_count of content
    """
    embedding = await embeddings.embed(query)
    kwargs = dict(embedding=embedding, limit=limit, min_score=min_score, tags=tags or [],
                  after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        return await db.search(db_path=None, **kwargs)

    if scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        return await db.search_multi(paths, **kwargs)

    return await db.search(db_path=db_path, **kwargs)


@mcp.tool()
async def memo_get(id: str, db_path: str | None = None) -> dict | None:
    """Retrieve a document by ID.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    """
    return await db.get(db_path=db_path, doc_id=id)


@mcp.tool()
async def memo_delete(id: str, db_path: str | None = None) -> dict:
    """Delete a document by ID.

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    """
    deleted = await db.delete(db_path=db_path, doc_id=id)
    return {"deleted": deleted}


@mcp.tool()
async def memo_copy(
    id: str,
    to_db_path: str | None = None,
    from_db_path: str | None = None,
) -> dict | None:
    """Copy a memo to another database without re-embedding.

    from_db_path: source DB (None = global default).
    to_db_path: destination DB (None = global default).
    Returns {id: <new_uuid>} for the copy, or null if the source memo was not found.
    """
    new_id = await db.copy(from_db_path=from_db_path, doc_id=id, to_db_path=to_db_path)
    return {"id": new_id} if new_id else None


@mcp.tool()
async def memo_move(
    id: str,
    to_db_path: str | None = None,
    from_db_path: str | None = None,
) -> dict | None:
    """Move a memo to another database without re-embedding.

    Copies the memo to to_db_path then deletes it from from_db_path.
    from_db_path: source DB (None = global default).
    to_db_path: destination DB (None = global default).
    Returns {id: <new_uuid>} in the destination, or null if source memo not found.
    """
    new_id = await db.move(from_db_path=from_db_path, doc_id=id, to_db_path=to_db_path)
    return {"id": new_id} if new_id else None


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

    db_path: directory path (uses <dir>/.memo.db), explicit .db file, or None for global DB.
    scope controls which database(s) to list from:
    - "local" (default): only the DB specified by db_path (or global if db_path is None)
    - "global": only the global DB, ignoring db_path
    - "all": list from both db_path DB and global DB, merged by created_at desc

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
    token_budget: maximum tokens in the returned content string.
    scope: "local" (default), "global", or "all" (merge local + global DBs).

    Returns:
      content: formatted markdown string of results within budget
      token_count: actual token count of content
      doc_count: number of memos included
      truncated: true if results were cut off by the budget
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

    # Greedily fill token budget
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
            break
        parts.append(section)
        total_tokens += section_tokens

    content = "\n".join(parts)
    return {"content": content, "token_count": total_tokens, "doc_count": len(parts), "truncated": truncated}


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        # TTL reaper runs for the life of the app (FR-007). Stopped on the way
        # out so a reload doesn't leak a task still holding a DB connection.
        reaper.start()
        try:
            yield
        finally:
            await reaper.stop()


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
    embedding = await embeddings.embed(req.content)
    doc_id = await db.store(
        db_path=req.db_path,
        content=req.content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
    )
    return StoreResponse(id=doc_id)


# HTTP status per contracts/mediator-store.md. write-new is the only 201:
# everything else either touched an existing memo or wrote nothing at all.
_STORE_STATUS = {
    "write-new": 201,
    "merge": 200,
    "supersede": 200,
    "split": 200,
    "clarify": 409,
    "reject": 403,
}


# --- Phase 6: auditor + constitution proposals ---


@app.post("/constitution/propose", status_code=201)
async def constitution_propose(payload: dict[str, Any]):
    """Auditor files a constitutional proposal. [001/FR-023]

    Nothing enters `documents` here. Principle V: the operator owns the
    constitution, so an auditor proposes and waits.
    """
    try:
        return await constitution.propose(
            proposed_by=payload.get("proposed_by") or "",
            layer=payload.get("layer") or "constitutional",
            proposed_content=payload.get("proposed_content") or "",
            scope=payload.get("scope"),
            proposed_tags=payload.get("proposed_tags"),
            evidence=payload.get("evidence"),
            urgency=payload.get("urgency") or "medium",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/constitution/proposals")
async def constitution_list(status: str | None = Query(default="pending"),
                            limit: int = Query(default=50)):
    """Pending proposals awaiting the operator. [001/FR-023]"""
    return {"proposals": await constitution.list_proposals(status, limit)}


@app.post("/constitution/resolve")
async def constitution_resolve(payload: dict[str, Any]):
    """Operator accepts or rejects a proposal. [001/FR-023]

    On accept the constitutional memo is created here — the single point where
    auditor-originated content can become constitutional, and only with an
    explicit operator action.
    """
    pid = payload.get("proposal_id")
    if pid is None:
        raise HTTPException(status_code=400, detail="proposal_id is required")
    result = await constitution.resolve(
        proposal_id=int(pid), accept=bool(payload.get("accept")),
        resolved_by=payload.get("resolved_by") or "operator",
        note=payload.get("note"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    return result


@app.get("/answer-loop-audit")
async def answer_loop_audit(limit: int = Query(default=50),
                            session_id: str | None = Query(default=None),
                            since: float | None = Query(default=None)):
    """Mediator query -> answer -> next-turn log, for the auditor. [001/FR-035]

    Thin delegate; the logic lives in `memo.auditor.answer_loop` so it is
    testable without FastAPI.
    """
    from memo.auditor import answer_loop
    return await answer_loop.entries(limit=limit, session_id=session_id, since=since)


@app.get("/auditor/actions")
async def auditor_action_log(limit: int = Query(default=50),
                             auditor_id: str | None = Query(default=None)):
    """Auditor's action trail for operator review. [001/FR-025]"""
    return {"actions": await auditor_actions.recent(limit=limit, auditor_id=auditor_id)}


@app.post("/auditor/sweep")
async def auditor_global_sweep(payload: dict[str, Any] | None = None):
    """Run one global auditor sweep. [001/FR-024]

    Endpoint rather than only a cron so the sweep can be triggered on demand —
    and so its behavior is testable without waiting a day.
    """
    return await auditor_sweep.sweep(
        auditor_id=(payload or {}).get("auditor_id") or "auditor-global",
        coalesce=bool((payload or {}).get("coalesce", True)),
    )


@app.post("/reconcile/infra-change")
async def reconcile_infra_change(payload: dict[str, Any]):
    """React to an infra change. [001/FR-031 001/FR-032]

    DRY-RUN by default: pass `apply: true` to actually supersede. Rewriting the
    corpus off a single broadcast — which may be wrong, or a staged change — is
    not something to do unprompted.
    """
    from memo import reconciler
    required = ("entity", "old_value", "new_value")
    if not all(payload.get(k) for k in required):
        raise HTTPException(status_code=400,
                            detail=f"{', '.join(required)} are required")
    return await reconciler.on_infra_change(
        entity=payload["entity"], old_value=payload["old_value"],
        new_value=payload["new_value"],
        source=payload.get("source") or "api",
        actor=payload.get("actor") or "reconciler",
        apply=bool(payload.get("apply")),
        max_updates=int(payload.get("max_updates") or 5),
    )


# --- Phase 5: provider-facing endpoints ---


@app.post("/events")
async def conductor_pull(payload: dict[str, Any]):
    """Inbound Conductor events. [001/FR-042 001/FR-042a]

    The pull half of the Conductor contract: the transport delivers events TO
    memo here. Unknown kinds are accepted with `handled: false` rather than
    400'd — a Conductor that learns a new event kind before memo does must not
    start collecting delivery failures.
    """
    kind = (payload or {}).get("event_kind") or (payload or {}).get("kind")
    handlers = {
        "session.started": _ev_session_started,
        "session.ended": _ev_session_ended,
        "operator.directive": _ev_operator_directive,
    }
    handler = handlers.get(kind)
    if handler is None:
        logging.getLogger(__name__).info("conductor pull: unhandled event kind %r", kind)
        return {"handled": False, "event_kind": kind,
                "reason": "no handler registered for this kind"}
    try:
        return {"handled": True, "event_kind": kind,
                "result": await handler(payload or {})}
    except Exception as e:
        logging.getLogger(__name__).exception("conductor pull: %s handler failed", kind)
        return {"handled": False, "event_kind": kind, "error": str(e)}


async def _ev_session_started(payload: dict[str, Any]) -> dict[str, Any]:
    """Warm the injection-set cache so the session's first hook call is fast."""
    session_id = payload.get("session_id") or ""
    if not session_id:
        return {"warmed": False, "reason": "no session_id"}
    result = await injection_set.build(session_id=session_id,
                                       agent_family=payload.get("agent_family"),
                                       project=payload.get("project"))
    return {"warmed": True, "token_budget_used": result.get("token_budget_used", 0)}


async def _ev_session_ended(payload: dict[str, Any]) -> dict[str, Any]:
    return {"acknowledged": True, "session_id": payload.get("session_id"),
            "auditor_sweep": "deferred-to-phase-6"}


async def _ev_operator_directive(payload: dict[str, Any]) -> dict[str, Any]:
    """Record an operator directive. [001/FR-026 001/FR-029]

    Two jobs. It is how an `operator_directive_ref` enters memo at all
    (FR-015c wants one on a fact refutation), and it is the FR-026 override
    channel — "auditor, undo that reinjection".

    An override is captured as a `decision-in-progress` memo, not merely obeyed.
    FR-026 wants overrides to feed auditor CALIBRATION: an obeyed-and-forgotten
    correction teaches nothing and the auditor makes the same call next week.
    """
    content = (payload.get("content") or "").strip()
    ref = {"kind": "atc", "from": payload.get("from"),
           "at": payload.get("event_time"), "id": payload.get("event_id")}
    if not content:
        return {"recorded": False, "reason": "empty directive", "ref": ref}

    from memo import db, embeddings
    from memo.auditor import actions as _actions
    embedding = await embeddings.embed(content)
    memo_id = await db.store(db_path=None, content=content,
                             title="[operator directive] " + content[:60],
                             tags=["operator-directive", "auditor-calibration"],
                             metadata={"directive_ref": ref}, embedding=embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class='decision-in-progress' WHERE id=?",
                 (memo_id,))
    conn.commit()
    await _actions.record(action="override-recorded", auditor_id="memo",
                          target=memo_id, rationale=content[:200], details={"ref": ref})
    return {"recorded": True, "memo_id": memo_id, "ref": ref}


@app.get("/providers")
async def providers_status():
    """Which providers are active. [001/FR-045]

    Deliberately exposed: "why did nothing get notified" is otherwise a
    log-diving exercise, and `null` is easy to end up on by accident.
    """
    from memo.providers.registry import get_agent_controller, get_conductor
    from memo.providers.llm import get_llm_provider
    return {
        "conductor": get_conductor().name,
        "agent_controller": get_agent_controller().name,
        "llm": get_llm_provider().name,
        "standalone": (get_conductor().name == "null"
                       and get_agent_controller().name == "null"),
    }


# --- Phase 4: Layer 2 injection, hooks, flush, log queries ---


@app.get("/injection-set")
async def get_injection_set(
    session_id: str = Query(...),
    agent_family: str | None = Query(default=None),
    project: str | None = Query(default=None),
    pid: int | None = Query(default=None),
    current_time: float | None = Query(default=None),
    flush_generation: int | None = Query(default=None),
    cwd: str | None = Query(default=None),
):
    """Layer 2 gap-fill for a session. [001/FR-016]

    See contracts/injection-set.md. A SESSION_GUIDE or DB hiccup degrades to a
    smaller set rather than 500-ing — a failed session start is worse than a
    session that starts with less memory.
    """
    try:
        return await injection_set.build(
            session_id=session_id, agent_family=agent_family, project=project,
            pid=pid, current_time=current_time, flush_generation=flush_generation,
            cwd=cwd,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "injection-set failed for %s — returning empty set", session_id)
        return {"session_id": session_id, "forcible_constitutional": [],
                "forcible_current_focus": [], "transclusions": [],
                "token_budget_used": 0,
                "token_budget_ceiling": injection_set.DEFAULT_BUDGET,
                "degraded": True, "computed_at": _now()}


@app.post("/hooks/session-start")
async def hook_session_start(payload: dict[str, Any]):
    """SessionStart hook. [001/FR-017 001/FR-044]

    Returns `additionalContext` for Claude Code to inject verbatim. Hooks fire
    on the critical path of starting a session, so this NEVER raises: on any
    failure it returns empty additionalContext and the session starts without
    Layer 2 rather than not starting.
    """
    return await _hook_injection(payload, fire_point="session-start")


@app.post("/hooks/post-compact")
async def hook_post_compact(payload: dict[str, Any]):
    """SessionStart:compact hook. [001/FR-018 001/FR-036 001/FR-044]

    Replaces the atc-precompact-beacon.py subagent dance (C58): the
    post-compact session gets its Layer 2 set back directly, including the
    previous generation's ephemeral-flush slots when `flush_generation` is
    passed.
    """
    return await _hook_injection(payload, fire_point="post-compact")


@app.get("/injection-log")
async def get_injection_log(since: float | None = Query(default=None),
                                limit: int = Query(default=500, le=5000)):
    """What memo delivered, whenever memo's hook fired. [001/FR-044]

    memo reports its own PRESENCE-AND-OUTCOME, not its own absence — it cannot
    observe the latter, and an earlier draft of this endpoint pretended it
    could. The agent coordinator counts compactions (it triggers them, so it
    knows one happened without depending on any hook) and reconciles: a
    compaction with no row here is memo's hook silently not firing.
    """
    rows = await db.injection_log(since=since, limit=limit)
    return {"count": len(rows), "injections": rows}


@app.post("/hooks/instructions-loaded")
async def hook_instructions_loaded(payload: dict[str, Any]):
    """InstructionsLoaded hook — resolves `memo:<uuid>` transclusions. [001/FR-017 001/FR-044]

    `instruction_files` is a list of `{path, content}`; each is scanned for memo
    references and the resolved bodies come back as additionalContext.
    """
    files = payload.get("instruction_files") or []
    resolved: list[dict] = []
    for f in files:
        text = (f or {}).get("content") or ""
        if not text:
            continue
        try:
            from memo.injection import transclude
            resolved.extend(await transclude.resolve(
                text, source_file=(f or {}).get("path") or "instructions"))
        except Exception:
            logging.getLogger(__name__).exception(
                "instructions-loaded: transclusion failed for %s", (f or {}).get("path"))

    if not resolved:
        return {"additionalContext": "", "transclusions": []}
    body = "\n".join(
        f"[{t['referenced_uuid'][:8]} from {t['source_file']}] {t['resolved_content'].strip()}"
        for t in resolved
    )
    return {"additionalContext": f"[MEMO transclusions]\n{body}",
            "transclusions": resolved}


@app.post("/hooks/pre-compact")
async def hook_pre_compact(payload: dict[str, Any]):
    """PreCompact hook. [001/FR-044]

    Retained as an endpoint so the wired hook does not 404, but it no longer
    flushes session state. Distilling working state across a compaction moved
    to ATC (operator decision 2026-07-30): ATC already does TTL'd
    hold-and-redeliver, memo's `flush.py` duplicated it, and two mechanisms
    carrying "what was this session doing" is a place they can disagree.

    memo's half of the compaction loop is now only the DURABLE layer —
    constitutional and behavioral rules, served pre-first-turn by
    `/hooks/post-compact`. Session-specific working state is ATC's, delivered
    by the coordinator's inject().
    """
    return {"flushed": False, "reason": "session working state moved to ATC"}



@app.post("/hooks/session-stop")
async def hook_session_stop(payload: dict[str, Any]):
    """Stop hook — end-of-turn checkpoint. [001/FR-044]

    Distinct from session-end: Stop fires when a turn finishes, SessionEnd when
    the session terminates. Accepts an optional slot-set so a session can
    checkpoint without waiting for a compaction.
    """
    session_id = payload.get("session_id") or ""
    slots = payload.get("slots") or {}
    if slots and session_id:
        try:
            result = await flush_mod.flush(
                session_id=session_id,
                flush_generation=int(payload.get("flush_generation") or 0),
                slots=slots, provenance=payload.get("provenance"))
            return {"acknowledged": True, "flushed": True, **result}
        except Exception as e:
            logging.getLogger(__name__).exception("session-stop flush failed")
            return {"acknowledged": True, "flushed": False, "error": str(e)}
    return {"acknowledged": True, "flushed": False, "session_id": session_id}


@app.post("/hooks/session-end")
async def hook_session_end(payload: dict[str, Any]):
    """SessionEnd hook — fire-and-forget auditor sweep. [001/FR-025 001/FR-044]

    Returns no additionalContext: the session is ending, so there is nobody to
    inject into. Deliberately does not await the sweep — blocking session
    teardown on an audit would make shutdown slow and could hang it.
    """
    session_id = payload.get("session_id")
    logging.getLogger(__name__).info("session-end: %s (auditor sweep pending)", session_id)
    # Auditor lands in Phase 6; until then this records the fire point so the
    # hook contract is stable and hooks need not be re-deployed later.
    return {"acknowledged": True, "session_id": session_id,
            "auditor_sweep": "deferred-to-phase-6"}


async def _ledger(session_id: str, fire_point: str, payload: dict[str, Any],
                  *, ok: bool, tokens: int | None) -> None:
    """Record a compaction, and NEVER let doing so affect the caller. [001/FR-044]

    Swallows here as well as inside `db.record_injection`, deliberately. The
    belt-and-braces is not paranoia: the first version called the ledger from
    BOTH the success path and the failure handler, so a raising ledger meant the
    error handler re-invoked the thing that had just failed. Caught by its own
    test.

    This sits on the session-start critical path. An observer that can break the
    thing it observes is worse than no observer at all.
    """
    if fire_point != "post-compact":
        return                       # ordinary session starts are not compactions
    try:
        await db.record_injection(
            session_id, fire_point=fire_point,
            agent_family=payload.get("agent_family"),
            project=payload.get("project"),
            injected_ok=ok, injected_tokens=tokens)
    except Exception:
        logging.getLogger(__name__).exception(
            "compaction ledger failed for %s — continuing", session_id)


async def _hook_injection(payload: dict[str, Any], *, fire_point: str) -> dict:
    """Shared body for the injecting hooks."""
    log = logging.getLogger(__name__)
    session_id = payload.get("session_id") or ""
    try:
        result = await injection_set.build(
            session_id=session_id,
            agent_family=payload.get("agent_family"),
            project=payload.get("project"),
            pid=payload.get("pid"),
            flush_generation=payload.get("flush_generation"),
            cwd=payload.get("cwd"),
        )
        rendered = injection_set.render(result)
        await _ledger(session_id, fire_point, payload, ok=bool(rendered),
                      tokens=result.get("token_budget_used", 0))
        return {"additionalContext": rendered,
                "fire_point": fire_point,
                "opt_out": bool(result.get("opt_out")),
                "token_budget_used": result.get("token_budget_used", 0)}
    except Exception:
        log.exception("%s hook failed for %s — starting without Layer 2",
                      fire_point, session_id)
        # Record the FAILURE too — memo cannot report its own absence, but it
        # can report its own presence-and-failure, which is the half it owns.
        await _ledger(session_id, fire_point, payload, ok=False, tokens=None)
        return {"additionalContext": "", "fire_point": fire_point, "degraded": True}



@app.post("/log-query")
async def log_query_endpoint(payload: dict[str, Any]):
    """Search a Claude Code transcript for provenance linking. [001/FR-033]"""
    try:
        return await log_queries.query(
            project_dir=payload.get("project_dir") or "",
            session_uuid=payload.get("session_uuid") or "",
            pattern=payload.get("pattern") or payload.get("query") or "",
            host=payload.get("host"),
            max_matches=payload.get("max_matches") or log_queries.DEFAULT_MAX_MATCHES,
        )
    except log_queries.LogQueryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/store")
async def store_mediated(req: MediatorStoreRequest, request: Request):
    """Storage mediator — reconcile-before-write. [001/FR-015a]

    The mediated write path. Raw `POST /documents` is left in place for v1
    clients and for deliberate bypass; agents should use this.

    Non-2xx here are ordinary parts of the protocol, not server faults: 409
    carries a clarification token the caller answers and retries with, 403 says
    the write needs operator authority. Both are returned as bodies rather than
    raised as HTTPException so the caller always gets the full typed response.
    """
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "POST /store",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    result = await store_mediator.store(req)
    return JSONResponse(
        status_code=_STORE_STATUS.get(result.action, 200),
        content=result.model_dump(exclude_none=True),
    )


@app.post("/recall", response_model=RecallResponse)
async def recall_endpoint(req: RecallRequest):
    """Retrieval mediator — returns a reconciled answer + citations. [001/FR-010]

    Agents call this instead of raw `POST /search`: they get an ANSWER, not a
    pile of rows to interpret. See contracts/mediator-recall.md.

    Note what is NOT here: no 503 when the LLM is down. Per R-17 the mediator
    degrades to a search-only answer and reports it in `anomalies`, so this
    endpoint's failure modes are only "bad request" and "DB unavailable".
    """
    return await recall_mediator.recall(req)


@app.post("/supersede", response_model=SupersedeResponse)
async def supersede_document(req: SupersedeRequest, request: Request):
    """Atomically close out a memo and write its replacement. [001/FR-003]

    Per FR-003 the old memo's ``valid_until`` and the new memo's ``valid_from``
    are set to the SAME instant inside one transaction, so no reader can catch
    the lineage with two current versions or none.

    404 when ``old_id`` is unknown; 409 when it is already superseded — both
    surface as a None from the repository, so they are disambiguated with a
    follow-up read rather than by guessing.
    """
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "POST /supersede",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    new_memo = req.model_dump(
        by_alias=True,
        exclude={"old_id", "actor", "reason", "operator_directive_ref"},
    )
    embedding = await embeddings.embed(req.content)
    result = await documents_repo.supersede(
        req.old_id, new_memo, embedding, req.actor,
        reason=req.reason,
        operator_directive_ref=req.operator_directive_ref,
    )
    if result is None:
        existing = await documents_repo.get(req.old_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"memo {req.old_id} not found")
        raise HTTPException(
            status_code=409,
            detail=(
                f"memo {req.old_id} is already superseded "
                f"(valid_until={existing.get('valid_until')}); "
                "supersede the current version instead"
            ),
        )
    return SupersedeResponse(**result)


@app.get("/documents/{doc_id}/current", response_model=Document)
async def get_document_current(doc_id: str, db_path: str | None = Query(default=None)):
    """Currently-valid version of this memo's lineage. [001/FR-002]

    Accepts a superseded id and follows the supersede chain forward, so a
    caller holding a stale id still gets the current truth.
    """
    doc = await documents_repo.get_current(doc_id, db_path=db_path)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no current version for {doc_id}")
    return doc


@app.get("/documents/{doc_id}/as-of", response_model=Document)
async def get_document_as_of(
    doc_id: str,
    t: float = Query(description="Epoch seconds — the instant to read as of"),
    db_path: str | None = Query(default=None),
):
    """Version of this memo's lineage that was true at ``t``. [001/FR-002]"""
    doc = await documents_repo.get_as_of(doc_id, t, db_path=db_path)
    if doc is None:
        raise HTTPException(
            status_code=404, detail=f"no version of {doc_id} was valid at {t}"
        )
    return doc


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
async def search_documents(req: SearchRequest):
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
    return [SearchResult(document=Document(**r["document"]), score=r["score"]) for r in results]


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

    # 4. Create new memo — through the storage mediator, not a raw insert. [001/FR-015a]
    #
    # FR-015a makes the mediator the single write path, so auto_store gets the
    # same canonical tags, class inference and provenance handling as any other
    # caller instead of quietly bypassing all three.
    #
    # KNOWN OVERLAP (Phase 3 note): steps 2-3 above run auto_store's own
    # LLM-driven dedup, and the mediator then reconciles again. Usually
    # harmless — if auto_store found nothing similar the mediator generally
    # won't either. But where they disagree the mediator wins, since it is the
    # write path. Its merge is also weaker than auto_store's: it unions tags
    # only, where auto_store rewrites merged CONTENT. Collapsing the two into
    # the mediator is the right cleanup, but it is a behavior change beyond
    # T036's scope, so it is left explicit rather than done silently.
    result = await store_mediator.store(MediatorStoreRequest(
        content=extracted,
        title=title,
        tags=tags,
        session_id=req.session_id or "auto_store",
    ))
    if result.action in ("clarify", "reject"):
        # Auto-store is unattended background capture — there is no agent
        # waiting to answer a clarification, so report it rather than stranding
        # a token nobody will ever redeem.
        return AutoStoreResponse(
            action="skipped",
            reason=f"storage mediator returned {result.action}: "
                   f"{result.prompt or result.reason}",
        )
    return AutoStoreResponse(action="created", id=result.memo_id, title=title,
                             reason=analysis.get("reason"))


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
