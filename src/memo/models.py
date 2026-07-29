"""Pydantic models for memo v2. [001/FR-001 001/FR-002 001/FR-004 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009]

v1 request/response models remain unchanged (Document, StoreRequest, ...)
so v1 clients keep working during transition. v2 adds Memo, Provenance,
TimeScope, Reopenability, ConstitutionMeta, InjectionSet, and
TransclusionResolution — see data-model.md.

Marker anchored at module level because the FRs listed genuinely span
the whole module (every entity here maps to one of those FRs).
"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


class Document(BaseModel):
    id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    token_count: int = 0
    created_at: float
    updated_at: float


class StoreRequest(BaseModel):
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    db_path: str | None = None


class StoreResponse(BaseModel):
    id: str


class Filters(BaseModel):
    tags: list[str] = []
    after: float | None = None       # created_at >= after (Unix timestamp)
    before: float | None = None      # created_at <= before (Unix timestamp)
    min_tokens: int | None = None
    max_tokens: int | None = None


class SearchRequest(BaseModel):
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


class UpdateRequest(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    db_path: str | None = None


class ContextRequest(BaseModel):
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
    action: str          # "created", "updated", "skipped"
    id: str | None = None
    title: str | None = None
    reason: str | None = None


# ==============================================================================
# v2 models below — additive; v1 models above stay unchanged.
# ==============================================================================


# Provenance nested types (FR-004; C34)
class ClaudeLogRef(BaseModel):
    host: str
    project_dir: str
    session_uuid: str
    line_range_start: int
    line_range_end: int


class GitRef(BaseModel):
    repo: str
    sha: str
    file: str
    line_start: int
    line_end: int


class PhonyRef(BaseModel):
    record_type: Literal["sms", "call", "voicemail"]
    record_id: str


class ATCRef(BaseModel):
    kind: Literal["message", "beacon", "status"]
    id: str
    from_: str = Field(alias="from")
    zones: list[str] = []

    class Config:
        populate_by_name = True


class Provenance(BaseModel):
    """First-class provenance block per Principle III + C34.

    At least one of the sub-fields MUST be set for a new memo — else it
    must be classified as legacy-unattributed. Validated at write time
    by the storage mediator, not here (mediator has caller context to
    reject vs. reclassify).
    """
    claude_log_ref: ClaudeLogRef | None = None
    git_ref: GitRef | None = None
    gmail_msg_id: str | None = None
    phony_ref: PhonyRef | None = None
    atc_ref: ATCRef | None = None
    url: str | None = None
    derived_from: list[str] = []


# Time-scope + reopenability + constitution meta (FR-005, FR-009)
class TimeScope(BaseModel):
    start: float
    end: float
    trip_id: str | None = None
    calendar_event_id: str | None = None


class Reopenability(BaseModel):
    """Simple 2-field schema per C-04 — don't over-engineer this."""
    challenge_if_delays_build_by_days: int | None = None
    operator_tempo_hint: str | None = None


class ConstitutionMeta(BaseModel):
    """Required for class=constitutional memos per C45."""
    version: str
    ratified_at: float
    amended_at: float
    incident_ref: str


# Memo class + injection_mode literals (FR-001, FR-006)
MemoClass = Literal[
    "constitutional",
    "behavioral",
    "goal",
    "verbatim-critical",
    "fact",
    "decision-in-progress",
    "episodic",
    "ephemeral-flush",
    "time-scoped",
    "legacy-unattributed",
]

InjectionMode = Literal[
    "forcible-constitutional",
    "forcible-current-focus",
    "on-recall",
    "on-procedure-match",
]


class Memo(BaseModel):
    """The v2 unit of stored knowledge. [001/FR-001]

    Superset of v1 Document with class taxonomy, bi-temporal fields,
    provenance, scope, and class-specific special fields. See
    data-model.md for the full schema + validation rules.
    """
    id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    token_count: int | None = None
    created_at: float
    updated_at: float

    # v2 additions
    class_: MemoClass = Field(alias="class", default="fact")
    injection_mode: InjectionMode = "on-recall"
    scope: list[str] = ["global"]
    provenance: Provenance | None = None
    valid_from: float
    valid_until: float | None = None
    expires_at: float | None = None
    time_scope: TimeScope | None = None
    reopenability: Reopenability | None = None
    derived_from: list[str] = []
    constitution_meta: ConstitutionMeta | None = None

    class Config:
        populate_by_name = True

    @field_validator("valid_until")
    @classmethod
    def _valid_until_after_from(cls, v: float | None, info) -> float | None:
        # valid_from <= valid_until when valid_until is set
        # (data-model.md validation rule)
        if v is not None:
            valid_from = info.data.get("valid_from")
            if valid_from is not None and v < valid_from:
                raise ValueError("valid_until must be >= valid_from")
        return v


# InjectionSet + TransclusionResolution (FR-016..020)
class TransclusionResolution(BaseModel):
    source_file: str
    referenced_uuid: str
    resolved_memo: Memo


class InjectionSet(BaseModel):
    """Computed at hook fire time; returned as additionalContext. [001/FR-016]

    Not stored except in injection_set_cache. See contracts/injection-set.md.
    """
    session_id: str
    agent_family: str | None = None
    project: str | None = None
    current_time: float
    memory_posture: Literal["on", "off"] = "on"
    forcible_constitutional: list[Memo] = []
    forcible_current_focus: list[Memo] = []
    transclusions: list[TransclusionResolution] = []
    spec_kit_constitution_content: str | None = None
    token_budget_used: int = 0
    token_budget_ceiling: int = 5000  # per C-02
    computed_at: float


# Mediator request/response envelopes (FR-010 recall + FR-015a store)
class RecallRequest(BaseModel):
    """Request shape for POST /recall. See contracts/mediator-recall.md."""
    query: str
    session_id: str
    agent_family: str | None = None
    project: str | None = None
    scope_hint: list[str] = []
    as_of: float | None = None
    max_results: int = 8
    budget_tokens: int = 800
    trigger_context: str | None = None


class RecallResponse(BaseModel):
    """Response shape for POST /recall. See contracts/mediator-recall.md."""
    answer: str | None
    citations: list[str] = []
    filter_chain_trace: list[str] = []
    llm_fallback_used: bool = False
    anomalies: list[str] = []
    latency_ms: int
    mediator_version: str = "1.0.0"


class MediatorStoreRequest(BaseModel):
    """Request shape for POST /store (storage mediator). See contracts/mediator-store.md."""
    content: str
    title: str | None = None
    tags: list[str] = []
    class_: MemoClass | None = Field(alias="class", default=None)
    scope: list[str] = ["global"]
    provenance: Provenance | None = None
    time_scope: TimeScope | None = None
    reopenability: Reopenability | None = None
    session_id: str
    operator_directive_ref: dict[str, Any] | None = None
    bypass_mediator: bool = False
    clarification_response: dict[str, Any] | None = None
    clarification_token: str | None = None

    class Config:
        populate_by_name = True


class MediatorStoreResponse(BaseModel):
    """Response shape covering all 6 action outcomes."""
    action: Literal["merge", "write-new", "supersede", "clarify", "reject", "split"]
    memo_id: str | None = None
    memo_ids: list[str] | None = None
    merged_into: list[str] | None = None
    superseded: str | None = None
    supersede_edge_id: int | None = None
    conflicting_memo_id: str | None = None
    prompt: str | None = None
    resolve_via: str | None = None
    clarification_token: str | None = None
    expires_in: int | None = None
    reason: str | None = None
    how_to_authorize: str | None = None
    class_inferred: MemoClass | None = None
    canonical_tags_applied: list[str] | None = None
    provenance_added: bool | None = None
    split_reason: str | None = None
    latency_ms: int
