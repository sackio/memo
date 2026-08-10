import asyncio
import contextlib
import hashlib
import logging
import time as _time
from time import time as _now
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from memo import db, embeddings, log_queries, passages, reaper
from memo.mediators import recall as recall_mediator
from memo.mediators import store as store_mediator
from memo.injection import set as injection_set
from memo.auditor import global_sweep as auditor_sweep
from memo.auditor import actions as auditor_actions
from memo.auditor import proposals as constitution
from memo.config import settings
from memo.db import _count_tokens, _truncate_to_tokens
from memo.models import (
    AutoStoreRequest,
    AutoStoreResponse,
    ContextRequest,
    ContextResponse,
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


async def _store_receipt(db_path: str | None, doc_id: str) -> dict:
    """Post-write read of what the DB actually holds, for the caller to verify against.

    ⛔ READS THE DOCUMENT BACK. Does NOT hash the request body. Hashing the input
    would confirm only that the request was parsed and would pass unchanged if the
    write silently truncated, wrote elsewhere, or did nothing — the exact failure
    this exists to catch. [mind, 2026-08-06]

    ⚠️ This is a receipt, not a durability guarantee: it proves the row is readable
    now, not that it survived to disk. That is a strictly weaker claim than callers
    may want and is deliberately not overstated — but it is unboundedly stronger
    than `{id}`, which proves only that a request was accepted.

    Returns {} rather than raising if the read-back fails: a store that succeeded
    must not be reported as failed because its receipt could not be produced.
    """
    try:
        doc = await db.get(db_path, doc_id)
        if not doc:
            return {}
        content = doc.get("content") or ""
        return {
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "token_count": int(doc.get("token_count") or 0),
        }
    except Exception:
        return {}


def _resolve_doc_token_cap(max_tokens: int | None,
                           max_doc_tokens: int | None) -> int | None:
    """One filter, two names. [2026-08-09, reported by `models`]

    ⭐ WHY THE ALIAS. `max_tokens` reads as *cap the size of the response* and
    actually means *only return documents smaller than N*. Measured: three
    independent misuses in four hours by two seats who both know better, each
    reaching for it to bound context cost, each getting an empty list that reads
    as "no such memos". **Three instances is a property of the name, not of the
    callers.** `max_doc_tokens` cannot form the wrong mental model.

    ⛔ `max_tokens` KEEPS WORKING. There are live callers across four hosts and an
    MCP layer in front; renaming outright would break working integrations to
    punish a name I chose. The old name is never removed.

    ⚠️ If both arrive and DISAGREE, the explicit one wins and the collision is
    logged — silently picking one would reproduce the original bug in a new place.
    """
    if max_doc_tokens is None:
        return max_tokens
    if max_tokens is not None and max_tokens != max_doc_tokens:
        print(f"PARAM-COLLISION memo: max_tokens={max_tokens} and "
              f"max_doc_tokens={max_doc_tokens} disagree — using max_doc_tokens "
              f"({max_doc_tokens}). They are the same filter.", flush=True)
    return max_doc_tokens


def _thin(rows: list[dict], ids_only: bool) -> list[dict]:
    """Drop document bodies when the caller only wants to know WHICH memos.

    ⭐ THIS IS THE THING EVERY `max_tokens` MISUSE WAS ACTUALLY REACHING FOR:
    *"return me ids cheaply, don't dump full documents into my window."* There was
    no way to ask for it, so callers reached for the nearest-sounding parameter
    and got a silent empty result. **The fix that removes the motive beats the
    fix that redirects it.**

    ⚠️ Keeps `id`, `title`, `tags`, `token_count`, `created_at` and the score —
    enough to decide what to fetch — and drops only `content` and `metadata`.
    An ids-only mode that returned bare uuids would send everyone straight back
    to a second round-trip, which is the cost they were trying to avoid.
    """
    if not ids_only:
        return rows
    keep = ("id", "title", "tags", "token_count", "created_at", "updated_at")
    out = []
    for r in rows:
        doc = r.get("document", r) or {}
        thin = {k: doc[k] for k in keep if k in doc}
        out.append({**{k: v for k, v in r.items() if k != "document"},
                    "document": thin} if "document" in r else thin)
    return out


def _log_phantom_fields(req, endpoint: str, doc_id: str | None = None,
                        user_agent: str | None = None) -> None:
    """Record any field a caller sent that this API does not have. [Ben, 2026-08-05]

    ⛔ IT DOES NOT REJECT, DELIBERATELY. Ben: *"better for backwards compatibility
    and live integration to not let it fail if they pass phantom parameters but we
    should log what's getting passed."* A 422 would break live callers to punish a
    harmless typo; the caller keeps working and the mistake stops being invisible.

    ⚠️ THIS IS THE DETECTOR FOR THE BUG THAT PRODUCED v0.4.0. `append=` was sent for
    weeks, dropped before the handler saw it, and the update ran with every field
    None — a no-op that bumped `updated_at` and returned `updated: true`. Nothing
    anywhere recorded that a parameter had been discarded. **The fix for `append`
    was one parameter; this is the fix for the next one.**

    ⚠️ SCOPE, STATED SO NOBODY READS MORE INTO A QUIET LOG THAN IT MEANS: this sees
    only what reaches the HTTP layer. **MCP tool calls are validated against the
    Python signature by FastMCP and unknown kwargs are dropped upstream of here**,
    so an MCP caller's phantom parameter still vanishes silently and this log
    stays empty. ⇒ **An empty phantom log is NOT evidence that nobody is passing
    phantom parameters** — it is evidence about the HTTP path only.
    """
    extra = getattr(req, "model_extra", None) or {}
    if not extra:
        return
    # Log keys and value TYPES, never values: a phantom field on a write endpoint
    # may carry the very content the caller meant to store.
    shape = ", ".join(f"{k}:{type(v).__name__}" for k, v in sorted(extra.items()))
    print(f"PHANTOM-FIELD {endpoint}"
          f"{f' doc={doc_id}' if doc_id else ''}"
          f"{f' ua={user_agent}' if user_agent else ''}"
          f" ignored={{{shape}}} — accepted and DISCARDED; this call did not do what "
          f"the caller thinks it did", flush=True)


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

    ⭐ **On a stored action you also get `content_sha256` and `token_count`, read
    back from the database AFTER the write.** Compare the hash against sha256 of
    what you sent to confirm the store landed intact. Without that you know the
    call was *issued*, not that it *succeeded*: a timed-out call and a slow
    successful one look identical from the caller's side, and the difference
    lands in the durable artifact where nothing surfaces it later.
    [v0.4.2; mind, 2026-08-06]

    ⚠️ An empty `content_sha256` means the read-back itself failed, NOT that the
    store failed. Re-read with `memo_get` before concluding anything.

    ⚠️ **`action` and the receipt answer different questions.** `action` says what
    the mediator DECIDED; the receipt says what the database HOLDS. A merge that
    decided correctly and wrote partially is only visible in the second.

    ⛔ **SIZE CEILING: documents over ~8,192 tokens are REJECTED and cannot be
    stored. Split before storing.** The embedding provider refuses them and this
    raises a 500 carrying the provider's own message —
    `maximum context length is 8192 tokens`.

    ⚠️ **That error is loud about the wrong subject.** It reads as *your query is
    too long* or *the model's context is full*; it actually means *this document
    will never land*. A caller who takes it at face value goes looking in the wrong
    place. [mind, 2026-08-06 — who also noted the useful framing: an undocumented
    misdirecting error is worse than a documented constraint.]

    ⚠️ **This is a HARD SIZE limit, distinct from the OTHER failure mode**: a large
    store can also abort on timeout mid-embed (~300s observed), and *that* one is
    silent — indistinguishable from a slow success, which is what
    `content_sha256` exists to catch. Over-8k fails loudly; the timeout fails
    quietly. Do not diagnose one as the other.

    db_path is accepted for backward compatibility and ignored (single-global).
    """
    await _reject_leaked_tool_call(content, tags, "memo_store")
    _t0 = _time.time()
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
    # ⛔ The v0.4.2 receipt, carried onto the mediator path. Without this the
    # merge would have silently regressed a verified fix: the branch predates
    # it, so taking the branch's return wholesale looks like a clean resolution
    # and quietly removes the only thing that distinguishes an issued write from
    # a landed one. Skipped when nothing was stored (clarify / reject).
    if result.memo_id:
        payload.update(await _store_receipt(db_path, result.memo_id))
    await db.log_query_async(
        db_path, "store", query=title or (content or "")[:200], tags=tags or [],
        result_ids=[result.memo_id] if result.memo_id else [],
        latency_ms=(_time.time() - _t0) * 1000,
        user_agent="mcp:memo_store")
    return payload


@mcp.tool()
async def memo_update(
    id: str,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    append: str | None = None,
    db_path: str | None = None,
) -> dict | None:
    """Update an existing memo by ID. Only provided fields are changed.

    If content is updated, the embedding and token_count are recomputed automatically.

    ⭐ `append` adds text to the END of the existing content, re-embedding the
    result. [v0.4.0]

    ⛔ WHY IT EXISTS. Callers were already passing `append=` — it was not a
    parameter, the MCP layer silently DISCARDED the unknown kwarg, and
    `memo_update` then ran with every field None: a no-op that still bumped
    `updated_at` and returned `updated: true`. Reported by `agents` 2026-08-03
    mid corpus-migration, reproduced here in one call. **Any invented parameter
    name behaved this way; `append` is simply the one people reached for.**
    ⇒ The fix is not a guard. It is to make the call people were already making
    do the thing they meant.

    ⛔ APPENDS VERBATIM — no separator is inserted. If you want a newline, send
    one. A silently injected character is the same class of surprise as the bug
    this replaces, and a write API should be predictable before it is convenient.

    ⛔ `append` and `content` together is REFUSED, not merged. One replaces and
    one extends; guessing which the caller meant is how a write API loses data.

    ⚠️ Append is read-modify-write, so a concurrent append could drop one side.
    The read content is passed back as a compare-and-set guard: if the memo
    changed underneath, this returns `{updated: false, reason: "conflict"}`
    rather than overwriting. Re-read and re-apply.

    Returns the updated memo with `updated: true`. If the ID matched nothing,
    returns `{updated: false, reason: "not_found", requested_id: <id>}` —
    NOT null. A bare null could not distinguish a mistyped id from a memo that
    has genuinely vanished, and two of those readings are alarming while one is
    a typo. Check `updated`; a false means re-look-up the id, not that the memo
    is gone.
    """
    await _reject_leaked_tool_call(content, tags, "memo_update")

    expect_content = None
    if append is not None:
        if content is not None:
            return {"updated": False, "reason": "ambiguous_content_and_append",
                    "detail": "`content` replaces and `append` extends. Send one. "
                              "Guessing which you meant is how a write API loses data.",
                    "requested_id": id}
        current = await db.get(db_path, id)
        if current is None:
            return {"updated": False, "reason": "not_found", "requested_id": id}
        expect_content = current.get("content") or ""
        # ⛔ Verbatim — no separator injected. See the docstring.
        content = expect_content + append

    # embed_document, not embed_query: this is stored text. The two encode
    # differently on an asymmetric model and mixing them fails SILENTLY —
    # plausible, slightly-wrong neighbours forever, with no error to notice.
    embedding = await embeddings.embed_document(content) if content is not None else None
    result = await db.update(
        db_path=db_path,
        doc_id=id,
        content=content,
        title=title,
        tags=tags,
        metadata=metadata,
        embedding=embedding,
        expect_content=expect_content,
    )
    if isinstance(result, dict) and result.get("conflict"):
        # ⚠️ A LOUD refusal. The bug this replaces was a silent no-op reporting
        # success; overwriting a concurrent append would be the same data loss
        # with a different cause.
        return {"updated": False, "reason": "conflict", "requested_id": id,
                "detail": "the memo changed between read and write; re-read and "
                          "re-apply your append",
                "current_updated_at": result.get("current_updated_at")}
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
    max_doc_tokens: int | None = None,
    ids_only: bool = False,
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
    - min_tokens/max_tokens: ⛔ **FILTERS WHICH DOCUMENTS COME BACK, BY THEIR
      STORED SIZE. IT DOES NOT CAP OUTPUT LENGTH — for that use `limit`, or
      `ids_only=True`.** Reported by `models` 2026-08-09: three independent
      misuses in four hours by two seats who both know better, every one of them
      reaching for it to bound context cost and every one getting an EMPTY LIST
      that reads as "no such memos" and means "no memos under N tokens".
      ⚠️ The worst instance was `quantum-data` verifying their own memo tag
      coverage — a false null ABOUT THE EXACT PROPERTY BEING MEASURED, plausible
      and self-critical, which they nearly acted on.
      ⭐ `max_doc_tokens` is the same parameter under a name that cannot form the
      wrong mental model; prefer it. `max_tokens` is kept working forever.
    """
    # ⛔ LOGGED HERE, NOT ONLY ON THE HTTP ROUTE. Agents reach memo through the
    # MCP tool, which calls db directly — logging only POST /search would
    # capture a small, unrepresentative slice and the bias would be invisible
    # in the resulting numbers. [v0.3.8]
    _t0 = _time.time()
    max_tokens = _resolve_doc_token_cap(max_tokens, max_doc_tokens)
    embedding = await embeddings.embed_query(query)
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
    # ⛔ AFTER the log, so the query record still shows what retrieval actually
    # returned. Trimming the response must not trim the evidence.
    return _thin(out, ids_only)


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
async def memo_list(
    query: str | None = None,
    tags: list[str] | None = None,
    after: float | None = None,
    before: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    limit: int = 100,
    min_score: float | None = None,
    max_doc_tokens: int | None = None,
    ids_only: bool = False,
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
    - min_tokens/max_tokens: ⛔ **FILTERS WHICH DOCUMENTS COME BACK, BY THEIR
      STORED SIZE. IT DOES NOT CAP OUTPUT LENGTH — for that use `limit`, or
      `ids_only=True`.** Reported by `models` 2026-08-09: three independent
      misuses in four hours by two seats who both know better, every one of them
      reaching for it to bound context cost and every one getting an EMPTY LIST
      that reads as "no such memos" and means "no memos under N tokens".
      ⚠️ The worst instance was `quantum-data` verifying their own memo tag
      coverage — a false null ABOUT THE EXACT PROPERTY BEING MEASURED, plausible
      and self-critical, which they nearly acted on.
      ⭐ `max_doc_tokens` is the same parameter under a name that cannot form the
      wrong mental model; prefer it. `max_tokens` is kept working forever.
    - min_score: minimum cosine similarity (only applies when query is provided)
    """
    max_tokens = _resolve_doc_token_cap(max_tokens, max_doc_tokens)
    if query is not None:
        embedding = await embeddings.embed_query(query)
        kwargs = dict(embedding=embedding, limit=limit, min_score=min_score, tags=tags or [],
                      after=after, before=before, min_tokens=min_tokens, max_tokens=max_tokens)
        if scope == "global" or (scope == "local" and db_path is None):
            results = await db.search(db_path=None, **kwargs)
        elif scope == "all" and db_path is not None:
            paths = list({db.global_path(), db._resolve_path(db_path)})
            results = await db.search_multi(paths, **kwargs)
        else:
            results = await db.search(db_path=db_path, **kwargs)
        return _thin([r["document"] for r in results], ids_only)

    kwargs = dict(tags=tags or [], limit=limit, after=after, before=before,
                  min_tokens=min_tokens, max_tokens=max_tokens)

    if scope == "global" or (scope == "local" and db_path is None):
        return _thin(await db.list_docs(db_path=None, **kwargs), ids_only)

    if scope == "all" and db_path is not None:
        paths = list({db.global_path(), db._resolve_path(db_path)})
        return _thin(await db.list_docs_multi(paths, **kwargs), ids_only)

    return _thin(await db.list_docs(db_path=db_path, **kwargs), ids_only)


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
    embedding_list = await asyncio.gather(*[embeddings.embed_query(q) for q in all_queries])

    # ⭐⭐ RANK BY PASSAGE, DELIVER THE DOCUMENT. [002/FR-117]
    #
    # Measured 2026-08-03 on the pinned sample (research.md R-20), 256 questions
    # whose answer is a literal carried by exactly one memo:
    #
    #   path        ranks right doc   answer in SPAN   answer in FULL DOC
    #   document          74.5%            95.8%             95.8%
    #   passages          84.0%            77.3%             98.7%
    #
    # ⇒ Passage retrieval is the best RETRIEVER and the worst DELIVERER. It
    # locates the right memo ~10pt more often, and the memo it finds contains the
    # answer 98.7% of the time — but the SPAN it matched misses the answer 21pt
    # of the time, because the chunk that matches the query and the chunk holding
    # the fact are frequently different chunks of the same document.
    # ⇒ So take the ranking and discard the span. The dedupe below already keys
    # on document id, so several passages of one memo collapse to its best score
    # and what gets packed is the whole document. This is the same narrowing
    # `/search` already does for the passages path (FR-113).
    #
    # ⛔ Until now `memo_context` called `db.search` DIRECTLY and never consulted
    # `settings.memo_retrieval_path`. The setting governed `/search` while the
    # endpoint agents actually consume ignored it — so the better index existed,
    # was measured, and was unreachable from the surface that matters.
    use_passages = settings.memo_retrieval_path == "passages"

    if scope == "all":
        # ⚠️ No passage equivalent for the multi-path merge, so this stays on
        # document search REGARDLESS of the setting. Recorded rather than
        # silently inherited: a caller passing scope="all" gets a different
        # retrieval path than the same caller passing scope="global", and
        # nothing in the response would otherwise say so.
        paths = list({db.global_path(), db._resolve_path(db_path)})
        async def _search(emb):
            return await db.search_multi(paths, embedding=emb, **search_kwargs)
    elif use_passages:
        target = None if (scope == "global" or db_path is None) else db_path
        async def _search(emb):
            # ⛔ include_superseded is KEYWORD. `_sync_search_passages` takes
            # `overfetch: int = 8` before it, and passing it positionally lands
            # it in the overfetch slot — which yielded k=0, zero hits, HTTP 200,
            # on every query, with the supersede test still green (FR-115).
            # `search_passages` calls `_resolve_path` itself — pass the raw
            # target rather than resolving twice.
            return await db.search_passages(
                target, emb,
                limit=search_kwargs["limit"],
                min_score=search_kwargs["min_score"],
                tags=search_kwargs["tags"],
                after=search_kwargs["after"], before=search_kwargs["before"],
                min_tokens=None, max_tokens=None)
    elif scope == "global" or db_path is None:
        async def _search(emb):
            return await db.search(db_path=None, embedding=emb, **search_kwargs)
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
    # ⭐ DEDUPE BEFORE PACKING. [002/FR-119]
    #
    # The corpus holds 203 same-title groups covering 524 memos and ~212k tokens
    # of duplicated text — cross-host writes from before the 2026-06-29
    # single-global refactor, plus repeated checkpoint/pin memos. Measured:
    # 87-89% of /context calls hit the token ceiling and only ~6 of ~10 matched
    # memos are delivered, so a budget spent twice on one fact costs a DIFFERENT
    # memo its place in the response.
    #
    # ⛔ EXACT text equality only, deliberately. A similarity threshold would
    # collapse memos that merely look alike, and the failure is SILENT and
    # UNRECOVERABLE from the caller's side: they receive a confident answer with
    # the distinguishing memo removed and nothing saying it existed. Near-dupes
    # are the bigger prize and need a rule that can be checked, not a cutoff.
    # ⚠️ So this is a LOWER BOUND on the waste, and is meant to be.
    #
    # Whitespace-normalised because a trailing newline is not a different memo.
    seen_bodies: set[str] = set()
    duplicates_dropped = 0
    spans_windowed = 0
    span_window = settings.memo_context_span_window

    for item in ranked:
        doc = item["document"]
        body_key = " ".join((doc.get("content") or "").split())
        if body_key and body_key in seen_bodies:
            # Same text, already packed at an equal-or-better score. Dropping it
            # frees budget for a memo that says something else.
            duplicates_dropped += 1
            continue
        title = doc.get("title") or doc["id"]
        tags_str = (", ".join(doc["tags"]) + " ") if doc["tags"] else ""
        body, windowed = _pack_body(doc, item, span_window)
        if windowed:
            spans_windowed += 1
        marker = " [window]" if windowed else ""
        section = (f"## {title} {tags_str}(score: {item['score']:.2f})"
                   f"{marker}\n{body}\n")
        section_tokens = _count_tokens(section)
        if total_tokens + section_tokens > token_budget:
            truncated = True
            continue
        # ⚠️ Recorded only once it actually FITS. Marking it seen on the skip
        # path would suppress a later, smaller copy that would have fitted —
        # turning a budget miss into a permanent omission.
        seen_bodies.add(body_key)
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
        # ⭐ Observable, or the fix is unfalsifiable. A dedupe that silently
        # collapses sections is indistinguishable from a corpus that had no
        # duplicates — and those two want opposite next actions.
        "duplicates_dropped": duplicates_dropped,
        # ⛔ Reported so "windowing did nothing" and "windowing never ran" stay
        # distinguishable — the document path carries no passage offsets and
        # falls back silently by necessity. [002/FR-120]
        "spans_windowed": spans_windowed,
        "truncated": truncated,
    }


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


def _pack_body(doc: dict, item: dict, window: int) -> tuple[str, bool]:
    """What text to spend budget on for one hit. [002/FR-120]

    Returns `(body, windowed)`.

    ⭐ WHY A WINDOW AND NOT THE SPAN. R-20: the matched passage carries the
    answer only **77.3%** of the time while the whole document carries it
    **98.7%** — the chunk that matches the query and the chunk holding the fact
    are routinely different chunks of one memo. So packing the bare span would
    cut tokens and lose answers, which is the wrong trade in the direction that
    matters. Packing the matched chunk **plus `window` tokens either side** aims
    to keep the fact while dropping the parts of a long memo that no query
    touched.

    ⛔ SMALL MEMOS ARE PACKED WHOLE. Windowing a memo that already fits spends
    the same budget and can only lose information — there is nothing to save.
    ⛔ AND A WINDOW THAT WOULD COVER MOST OF THE DOCUMENT IS NOT WORTH TAKING:
    below ~1.3× saving it trades a real risk of dropping the answer for a few
    tokens. Falls back to the whole document.

    ⚠️ Returns the whole document whenever the hit carries no passage offsets —
    the `document` retrieval path has none. That is a silent fallback by
    necessity, so the caller counts `windowed` and the benchmark reports it;
    otherwise "windowing did nothing" and "windowing never ran" look identical.
    """
    content = doc.get("content") or ""
    if window <= 0:
        return content, False
    p = item.get("passage")
    if not isinstance(p, dict):
        return content, False
    start, end = p.get("token_start"), p.get("token_end")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return content, False

    toks = db._tokenizer.encode(content)
    if len(toks) <= 2 * window:
        return content, False           # already small; nothing to save
    lo = max(0, start - window)
    hi = min(len(toks), end + window)
    if (hi - lo) * 1.3 >= len(toks):
        return content, False           # saving too little to risk the answer
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(toks) else ""
    return prefix + db._tokenizer.decode(toks[lo:hi]) + suffix, True


@app.get("/health")
async def health():
    """Liveness. Deliberately constant — it answers "is this process up".

    ⛔ IT CANNOT TELL YOU MEMO IS WORKING, AND ON 2026-08-03 IT DID NOT.
    During the server4 IO storm this endpoint returned 200 in 0.27s while
    `POST /search` HUNG PAST 90 SECONDS. `/recall` was dead fleet-wide and every
    monitor watching memo was green, because the process genuinely was alive —
    it just could not do the one thing it exists to do. The outage was found by a
    benchmark harness that happened to call `/search` and got a TimeoutError.
    No alert fired, and none would have.
    ⇒ **Absence of an alert was evidence about the CHECK, not about the SERVICE.**
    Use `/ready` for "is it doing its job". Keep both: they answer different
    questions and collapsing them loses the ability to tell a wedged disk from a
    dead process.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready(timeout_s: float = 5.0):
    """Readiness — exercises the ACTUAL read path, bounded.

    ⭐ Touches what a search touches: the sqlite connection AND the vec0 index.
    A `SELECT count(*)` on `documents` alone would not have caught 2026-08-03,
    because the wedge was in IO against the vector index, so this reads a row
    back out of `document_embeddings` too.

    ⛔ DELIBERATELY DOES NOT CALL THE EMBEDDING PROVIDER. A probe that embeds
    fails whenever the VENDOR is slow, which is a different outage with a
    different owner, and it would page us for OpenRouter latency while telling us
    nothing about this host. **Probe your own dependencies, not your vendors'.**
    (Measured during the same incident: OpenRouter answered in 1.3s while memo
    was unusable — a provider-touching probe would have been green too, for the
    opposite reason.)

    ⚠️ BOUNDED, because an unbounded readiness probe becomes a load source under
    exactly the conditions it exists to detect. On 2026-08-03 `docker exec`-based
    monitoring stacked instead of returning and the monitoring itself became part
    of the storm. A probe that cannot time out is a probe that piles up.
    """
    started = _now()

    def _probe():
        conn = db._get_or_create_conn(db._resolve_path(None))
        n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        # Read an actual vector back — the index, not just the table beside it.
        row = conn.execute(
            "SELECT doc_id FROM document_embeddings LIMIT 1").fetchone()
        return n, (row[0] if row else None)

    try:
        n, probe_id = await asyncio.wait_for(
            asyncio.to_thread(_probe), timeout=timeout_s)
    except asyncio.TimeoutError:
        # ⛔ 503, not 200-with-a-field. A monitor that has to parse the body to
        # learn it is broken is the failure this endpoint exists to prevent.
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready",
                     "reason": f"store did not respond within {timeout_s}s",
                     "elapsed_ms": round((_now() - started) * 1000, 1)})
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"{type(e).__name__}: {e}",
                     "elapsed_ms": round((_now() - started) * 1000, 1)})
    elapsed = round((_now() - started) * 1000, 1)
    return {"status": "ready", "documents": n,
            "vector_index_readable": probe_id is not None,
            "elapsed_ms": elapsed}


@app.post("/documents", response_model=StoreResponse)
async def store_document(req: StoreRequest, request: Request):
    _log_phantom_fields(req, "POST /documents",
                        user_agent=request.headers.get("user-agent"))
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "POST /documents",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _t0 = _time.time()
    embedding = await embeddings.embed_document(req.content)
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
    return StoreResponse(id=doc_id, **await _store_receipt(req.db_path, doc_id))


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
    embedding = await embeddings.embed_document(content)
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
    embedding = await embeddings.embed_document(req.content)
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
        embedding = await embeddings.embed_query(query)
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
    _log_phantom_fields(req, "PATCH /documents/{id}", doc_id,
                        user_agent=request.headers.get("user-agent"))
    try:
        await _reject_leaked_tool_call(
            req.content, req.tags, "PATCH /documents/{id}",
            user_agent=request.headers.get("user-agent"),
            source_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # ⭐ append: same semantics as the MCP tool — verbatim, mutually exclusive
    # with content, and compare-and-set so a concurrent append is REFUSED rather
    # than silently overwritten. [v0.4.0]
    new_content, expect_content = req.content, None
    if req.append is not None:
        if req.content is not None:
            raise HTTPException(
                status_code=422,
                detail="`content` replaces and `append` extends — send one, not both")
        current = await db.get(req.db_path, doc_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Memo not found")
        expect_content = current.get("content") or ""
        new_content = expect_content + req.append

    embedding = await embeddings.embed_document(new_content) if new_content is not None else None
    result = await db.update(
        db_path=req.db_path,
        doc_id=doc_id,
        content=new_content,
        title=req.title,
        tags=req.tags,
        metadata=req.metadata,
        embedding=embedding,
        expect_content=expect_content,
    )
    if isinstance(result, dict) and result.get("conflict"):
        # 409, not a silent overwrite. The bug this accompanies was a no-op that
        # reported success; losing a concurrent append would be the same class.
        raise HTTPException(
            status_code=409,
            detail="memo changed between read and write; re-read and re-apply")
    if result is None:
        raise HTTPException(status_code=404, detail="Memo not found")
    return Document(**result)


@app.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str, db_path: str | None = Query(default=None)):
    deleted = await db.delete(db_path=db_path, doc_id=doc_id)
    return DeleteResponse(deleted=deleted)


@app.post("/search-passages")
async def search_passages_endpoint(req: SearchRequest):
    """Passage-level search, ALWAYS — never affected by config. [002/FR-105 002/FR-113]

    Both paths stay addressable at their own endpoint so that a measurement
    always names the path it measured. When `/search` became configurable
    (T260), routing the bench through it would have meant a config change could
    silently alter what `--path document` was measuring — which is the same
    shape as every wrong number this feature has produced. So the explicit
    endpoints are the measurement surface and `/search` is the product surface.
    """
    embedding = await embeddings.embed_query(req.query)
    results = await db.search_passages(
        req.db_path, embedding, req.limit, req.min_score, req.tags,
        req.after, req.before, req.min_tokens, req.max_tokens)
    return results


async def _document_search(req: SearchRequest,
                           request: Request = None) -> list[SearchResult]:
    """Document-path search, plus the v0.3.8 query logging.

    ⚠️ MERGE NOTE. On main this body WAS the `/search` route; on the renovation
    branch `/search` became a config-driven dispatcher and this became a helper.
    Both definitions survived the merge as two `@app.post("/search")` decorators
    — and FastAPI matches routes in registration order, so main's would have won
    and the configurable one would have been **dead code that still reads as
    live**. ⛔ Two routes on one path do not error; the loser simply never runs.

    ⇒ The dispatcher is the product surface (see `/search` below). The logging
    that main had on the route moves here, so it still covers the document path
    however that path is reached.
    """
    # Timed around the work, logged AFTER the response is built. [v0.3.8]
    # ⛔ The log must never delay or fail the query it records — see
    # db.log_query's docstring. This is live fleet infrastructure.
    _t0 = _time.time()
    embedding = await embeddings.embed_query(req.query)
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


@app.post("/search-documents", response_model=list[SearchResult])
async def search_documents_endpoint(req: SearchRequest):
    """Document-level search, ALWAYS — never affected by config. [002/FR-113]

    The counterpart to `/search-passages`. See that docstring for why both exist
    independently of what `/search` is configured to serve.
    """
    return await _document_search(req)


@app.post("/search", response_model=list[SearchResult])
async def search_documents(req: SearchRequest, response: Response,
                           request: Request = None):
    """The product surface: serves whichever path is configured. [002/FR-113]

    `settings.memo_retrieval_path` selects it; the default is `document` and
    flipping it is an operator decision gated on SC-101/SC-103, neither of which
    passes yet (research.md R-07).

    The served path is echoed in the `X-Memo-Retrieval-Path` response header.
    Without it, a caller cannot tell which index answered — and a system whose
    behaviour changed silently under a config edit is one nobody can debug from
    the outside.

    Passage results are narrowed to the `{document, score}` contract here. The
    matching passage and its offsets are deliberately NOT carried through yet:
    that is the result-shape question (FR-107/FR-107a, T240–T241) still open with
    the operator as T201, and inventing an answer would pre-empt it.
    """
    _log_phantom_fields(req, "POST /search")
    path = settings.memo_retrieval_path
    response.headers["X-Memo-Retrieval-Path"] = path
    if path == "size-routed":
        return await _size_routed_search(req)
    if path == "passages":
        embedding = await embeddings.embed_query(req.query)
        results = await db.search_passages(
            req.db_path, embedding, req.limit, req.min_score, req.tags,
            req.after, req.before, req.min_tokens, req.max_tokens)
        return [SearchResult(document=Document(**r["document"]), score=r["score"])
                for r in results]
    return await _document_search(req, request)


async def _size_routed_search(req: SearchRequest) -> list[SearchResult]:
    """Serve each result from whichever index is better for THAT memo. [002/FR-113]

    T273, and the reason it exists is SC-102 rather than SC-101. Measured by full
    census (research.md R-09), neither path dominates:

        band          document   passages
        0-200            78.5%      77.1%
        200-500          76.9%      72.0%
        500-1000         76.6%      74.5%
        1000-2000        51.8%      68.5%
        2000+            18.8%      47.6%

    Replacing one path with the other therefore trades a large win above 1000
    tokens for a real 1.5-4.9 point loss below it. That loss is what SC-102
    forbids, and it is not noise — it is measured at n=1216-2293 per band.

    **The routing decision is made PER RESULT, not per query, and that is the
    whole trick.** At query time the target's size is unknown — that is precisely
    what is being searched for. But every *candidate* arrives with its own
    `token_count`, so the choice can be made where the information actually
    exists. Both indexes are queried, results are merged by document id, and each
    document keeps the score from the path that measures better for its own size.

    Deliberately NOT a hybrid score. No reciprocal-rank fusion, no weighted blend
    — those mix two models' score distributions, and a number whose units change
    between paths cannot be compared or thresholded. Each document keeps one
    path's score, unmodified.
    """
    embedding = await embeddings.embed_query(req.query)
    over = max(req.limit * 3, 30)  # overfetch: the merge discards duplicates

    doc_hits = await db.search(
        db_path=req.db_path, embedding=embedding, limit=over,
        min_score=req.min_score, tags=req.tags, after=req.after,
        before=req.before, min_tokens=req.min_tokens, max_tokens=req.max_tokens)
    passage_hits = await db.search_passages(
        req.db_path, embedding, over, req.min_score, req.tags,
        req.after, req.before, req.min_tokens, req.max_tokens)

    # One rule: a memo is served by its preferred path if that path found it, and
    # by the other path otherwise. Taking the preferred score is NOT the same as
    # taking the better score — a document whose preferred path scores it lower
    # still keeps that score, because the point is to use the index that is more
    # often RIGHT for that size, not to flatter each result.
    merged: dict[str, tuple[dict, float]] = {}
    for hits, source in ((doc_hits, "document"), (passage_hits, "passages")):
        for r in hits:
            doc = r["document"] if "document" in r else r
            preferred = ("document"
                         if (doc.get("token_count") or 0) < settings.memo_size_route_threshold
                         else "passages")
            if doc["id"] not in merged or source == preferred:
                merged[doc["id"]] = (doc, r["score"])

    ranked = sorted(merged.values(), key=lambda dv: dv[1], reverse=True)
    return [SearchResult(document=Document(**d), score=s)
            for d, s in ranked[:req.limit]]


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


@app.get("/admin/passage-indexed-ids")
async def passage_indexed_ids():
    """Doc ids with at least one passage. [002/FR-111]

    Read-only, for `memo-retrieval-bench --both-indexed-only`. Restricting the
    sample to memos in BOTH indexes is what makes a document-vs-passage number
    a comparison rather than a measurement of rollout progress.
    """
    return await passages.indexed_ids()


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
    #
    # ⚠️ DOCUMENT, even though the very next call is a search. This is the one site
    # where the split needed a judgment rather than a reading, so the reasoning is
    # here: qwen3's query instruction means "find documents matching this QUERY".
    # What happens below is document-to-document similarity — content about to be
    # stored, compared against stored document vectors. Prefixing it would put a
    # query-shaped vector against document-shaped ones, which is the exact
    # asymmetry mismatch that caused R-10, arrived at from the other direction.
    #
    # "It is passed to db.search" is the wrong test. The test is what the vector is
    # being compared WITH. [002/FR-111]
    embedding = await embeddings.embed_document(extracted)
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
            merged_embedding = await embeddings.embed_document(merged_content)
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
    _log_phantom_fields(req, "POST /context")
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
