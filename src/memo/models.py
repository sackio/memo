from typing import Any
from pydantic import BaseModel, ConfigDict


class PermissiveRequest(BaseModel):
    """Request base that ACCEPTS unknown fields rather than rejecting them — and
    whose whole point is that the endpoint then LOGS them. [Ben, 2026-08-05]

    ⛔ WHY NOT `extra="forbid"`. Ben's call, verbatim: *"better for backwards
    compatibility and live integration to not let it fail if they pass phantom
    parameters but we should log what's getting passed."* This API has live
    callers across four hosts and an MCP layer in front of it; a 422 on an
    unrecognised field would break integrations that work today, to punish a
    mistake that costs nothing to tolerate.

    ⚠️ BUT TOLERATING IT SILENTLY IS THE BUG THAT PRODUCED v0.4.0. `append=` was
    passed for weeks, discarded before it reached the handler, and `memo_update`
    then ran with every field None — a no-op that bumped `updated_at` and
    returned `updated: true`. **Accepting an unknown field quietly and accepting
    it loudly differ only in the log line, and that line is the entire
    difference between a typo and a fortnight of silent no-ops.**

    ⇒ Subclassing this is not sufficient on its own: the endpoint must call
    `_log_phantom_fields`. A model that collects extras nobody reads is the same
    silence with more machinery.
    """

    model_config = ConfigDict(extra="allow")


class Document(BaseModel):
    id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    token_count: int = 0
    created_at: float
    updated_at: float


class StoreRequest(PermissiveRequest):
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    db_path: str | None = None


class StoreResponse(BaseModel):
    """⭐ Carries enough for the caller to VERIFY the write, not merely learn its id.

    ⛔ WHY. `memo_update` returns the full stored document — that echo is a read of
    post-write server state, so the call self-verifies. `memo_store` returned
    `{id}` alone, so it was *issued*, not verified. **Same store, same session, and
    nothing at the call site distinguished them**, so callers reported both as
    "stored" and only one of those reports was justified. (`mind`, 2026-08-06,
    applying `alpaca`'s rule: *an issued write is not a touch of ground truth*.)

    ⚠️ THE FAILURE IS SILENT AND LANDS IN THE DURABLE ARTIFACT. `alpaca` had a
    `memo_store` abort at 300s mid-embed and reported it done from having issued
    it. **A timed-out call and a slow successful one are indistinguishable from the
    caller's side** — they only knew because the harness surfaced the abort.

    ⛔ `content_sha256` IS COMPUTED FROM A POST-WRITE READ, NEVER FROM THE REQUEST.
    Hashing the input would echo the caller's own string back and prove only that
    the request was parsed — the same trap as verifying a file by re-reading what
    you just sent. It must be derived from what the database actually holds, or it
    is theatre with a checksum on it.
    """

    id: str
    # sha256 of the stored content, read back from the DB after the write.
    # Caller compares against sha256 of what they sent.
    content_sha256: str = ""
    # Server-side token count of the stored content — a second, independent
    # witness that catches truncation even if a hash comparison is skipped.
    token_count: int = 0


class Filters(BaseModel):
    tags: list[str] = []
    after: float | None = None       # created_at >= after (Unix timestamp)
    before: float | None = None      # created_at <= before (Unix timestamp)
    min_tokens: int | None = None
    max_tokens: int | None = None


class SearchRequest(PermissiveRequest):
    query: str
    limit: int = 10
    min_score: float | None = None
    tags: list[str] = []
    after: float | None = None
    before: float | None = None
    min_tokens: int | None = None
    max_tokens: int | None = None
    db_path: str | None = None


class SearchResult(BaseModel):
    document: Document
    score: float


class UpdateRequest(PermissiveRequest):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    # Appends VERBATIM to existing content — no separator injected. Mutually
    # exclusive with `content`; sending both is refused, not merged. [v0.4.0]
    append: str | None = None
    db_path: str | None = None


class ContextRequest(PermissiveRequest):
    query: str
    token_budget: int = 4000
    queries: list[str] = []          # additional search angles run in parallel
    limit_per_query: int = 10
    min_score: float | None = None
    tags: list[str] = []
    after: float | None = None
    before: float | None = None
    db_path: str | None = None
    scope: str = "local"


class ContextResponse(BaseModel):
    content: str
    token_count: int
    doc_count: int
    # How many memos MATCHED, vs doc_count = how many were returned. Without
    # this, doc_count:0 reads as "the corpus has nothing on your query" whether
    # or not anything matched — the response could not report its own absence.
    matched_count: int = 0
    truncated: bool


class DeleteResponse(BaseModel):
    deleted: bool


class CopyMoveRequest(BaseModel):
    to_db_path: str | None = None
    from_db_path: str | None = None


class CopyMoveResponse(BaseModel):
    id: str


class AutoStoreRequest(BaseModel):
    content: str
    session_id: str | None = None
    db_path: str | None = None


class AutoStoreResponse(BaseModel):
    # "error" is a distinct action, not a flavour of "skipped": skipped means the
    # content was judged not worth keeping, error means NOTHING WAS STORED and the
    # caller should not believe its state is durable. [0.3.7]
    action: str          # "created", "updated", "skipped", "error"
    id: str | None = None
    title: str | None = None
    reason: str | None = None
    error_kind: str | None = None   # payment_required | rate_limited | provider_error
    retryable: bool = False         # 429 yes; 402 no — that one needs a human
