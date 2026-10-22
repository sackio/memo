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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # ⭐ 2026-09-28 — BEN'S CONDITION: A STORED COUNT MUST NAME ITS TOKENIZER.
    # (#atc ts 1790613818, confirmed at source.)
    #
    # ⛔ `token_count` above is cl100k. The embedder enforces max_model_len = 16,384
    # in Qwen3. Measured over the 108 documents with cl100k >= 11,000: cl100k
    # understates Qwen3 by 67-4,279 tokens, mean 1,110, a 64x spread — so there is
    # no correction factor, and the stored column does not even ORDER correctly
    # (the cl100k top-5 and the Qwen3 top-5 nearest the ceiling share ONE member).
    # Six wrong numbers were published across the fleet the day this shipped, every
    # one a quantity read in one unit against a limit enforced in another.
    #
    # ⛔⛔ THESE TWO FIELDS ARE ON THE ENVELOPE AND NOT ONLY IN THE SCHEMA, AND THAT
    # IS THE POINT. The same day, `include_superseded` was found threaded through
    # every layer of db.py and settable by NOBODY, because it was missing from
    # `SearchRequest` — a capability that exists and no caller can reach is
    # indistinguishable from one that was never built. A unit label no reader
    # receives would have repeated that defect in the same commit that fixed it.
    #
    # `token_count_qwen3` is None when not measured — ⛔ NEVER 0, which would read
    # as an empty document with maximum headroom.
    token_count_tokenizer: str | None = "cl100k_base"
    token_count_qwen3: int | None = None

    # ⭐ BEN 2026-10-20 (Slack #memo 19:36): a replaced memo must name its
    # successor ON THE ENVELOPE. Until now a superseded memo answered 200 with
    # `valid_until` set and nothing else, so a caller had no reason to know
    # `/documents/<id>/current` exists — and two independent hosts invented a
    # `/lineage` route that does not exist rather than find it (memo f93eba87).
    #
    # ⛔ THIS HAS TO BE A MODEL FIELD, NOT AN EXTRA DICT KEY. `Document` is
    # strict, so `response_model` SILENTLY STRIPS unknown keys: a dict-only
    # addition would vanish with no error and no log line. Exactly the trap
    # recorded at `_resolve_doc_id_or_404`, which is why the short-id expansion
    # is reported as the `X-Resolved-From` header instead.
    #
    # ⚠️ None means "no successor recorded", NEVER "not checked". A live tip
    # carries `lineage_tip: None` deliberately, so the field never points a
    # reader at the document they are already holding.
    superseded_by: str | None = None
    lineage_tip: str | None = None
    # ⭐ WHICH SEAT WROTE THIS. Ben, #memo 2026-10-22 11:47 (ask a0r, open since
    # 09-20): "I think it should record who wrote each memo".
    # ⛔ NULL ON ~9,900 EXISTING MEMOS AND THAT IS THE HONEST VALUE — the writer
    # was never recorded, and inferring one from transcripts would be a guess
    # stamped as provenance. A NULL can be corrected without first being
    # disbelieved; a wrong name cannot.
    # ⚠️ It is the SEAT, not the human. Every memo here is written by an agent on
    # Ben's behalf, so this answers "whose judgement is this", which is what
    # makes a claim checkable against the seat that made it.
    authored_by: str | None = None
    created_at: float
    updated_at: float

    # ⛔⛔ 2026-10-19 — THE ELEVEN FIELDS BELOW EXIST SO THE TWO DOORS AGREE.
    #
    # Measured 2026-09-15 and again 2026-10-19: the `documents` table has 21
    # columns, `db._row_to_memo` decodes and returns ALL of them, and this model
    # then silently dropped eleven on the way out — so `GET /documents/<id>`
    # answered with 10 fields while MCP `memo_get` answered with 21 FOR THE SAME
    # ROW. Nothing errored; the REST caller simply could not see that a document
    # had a class, a scope, a validity window or a provenance record.
    #
    # ⛔ THAT IS WHY THE FIX IS HERE AND NOT IN db.py. The data was never missing
    # from the store — only from the envelope — and absence in an envelope reads
    # exactly like absence on the object. A seat checking "is this memo
    # superseded?" over REST got `valid_until` = not-present and concluded no.
    # Memo `d7b36ca8`.
    #
    # ⚠️ Most of these are CONSTANT across the corpus and carry no signal:
    # 11,388/11,388 rows have `injection_mode='on-recall'` and `scope=['global']`,
    # and `expires_at` / `time_scope` / `reopenability` / `constitution_meta` are
    # null on every row. They are returned anyway, because a reader cannot tell a
    # field that is universally defaulted from one the envelope omits — and it was
    # that exact ambiguity that produced a false "this memo is force-injected"
    # alarm. A visible default is a fact; an absent field is a question.
    #
    # `class` is a Python keyword, so the attribute is `memo_class` and the alias
    # puts `class` on the wire — the name MCP already uses. FastAPI serialises
    # response models by alias, so the two surfaces now match byte for byte.
    memo_class: str | None = Field(default=None, alias="class")
    injection_mode: str | None = None
    scope: list[str] | None = None
    provenance: dict[str, Any] | None = None
    valid_from: float | None = None
    valid_until: float | None = None
    expires_at: float | None = None
    time_scope: Any | None = None
    reopenability: Any | None = None
    derived_from: list[str] | None = None
    constitution_meta: dict[str, Any] | None = None

    # populate_by_name so the handler can build this from `_row_to_memo`'s dict
    # (which keys on the COLUMN name `class`) and so existing code that sets
    # `memo_class=` keeps working. Without it one of those two paths breaks.
    model_config = ConfigDict(populate_by_name=True)


class StoreRequest(PermissiveRequest):
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    db_path: str | None = None
    # The writing seat. Named `actor` to match /supersede-link, which has
    # required one since the tombstone work and writes it to the edge — one
    # word for one concept beats a second spelling of the same idea.
    # ⚠️ OPTIONAL, UNLIKE /supersede-link's. Making it required would 422 every
    # existing client on the first deploy, and a write refused for want of a
    # label loses the memo, which is strictly worse than an unlabelled memo.
    actor: str | None = None
    # Mirror-only fields — see db._sync_store for why they exist. A mirror that
    # mints fresh ids passes every count and diff, then 404s every cited id the
    # moment it is promoted to serve :8000. Supplying an id that already exists
    # is a 409, never a silent overwrite.
    id: str | None = None
    created_at: float | None = None
    updated_at: float | None = None


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
    # ⭐ 2026-09-28 — MAKE THE EXISTING OPT-IN REACHABLE. [FR-115 follow-up]
    #
    # `include_superseded` has been threaded through every layer of db.py since
    # 2026-08-03 and was settable by NOBODY: it was absent from this model, so the
    # only caller that ever set it True was the recall mediator, and only under
    # `as_of`. So the FR-115 exclusion was UNCONDITIONAL for every ordinary
    # `/search` caller, with no way to ask past it.
    #
    # ⛔ THE DEFAULT DOES NOT CHANGE, AND MUST NOT. FR-115 exists because a
    # superseded memo was measured being served at rank 3 on the query after its
    # supersession, and 655 self-declared-stale memos could all have been correctly
    # superseded and still returned. Excluding by default is the correct behaviour
    # and a de-rank was explicitly rejected — a memo the corpus has been told is no
    # longer true is not "less relevant", and returning it ranked lower still
    # returns it.
    #
    # ⇒ What this adds is only the ESCAPE HATCH the design already assumed existed:
    # a caller tracing a supersession chain, or auditing what was retired, can ask
    # for it explicitly instead of being unable to see it from this surface at all.
    # Found 2026-09-28 by `atc` checking the deployed OpenAPI against a claim of
    # mine that the opt-in was already exposed. It was not.
    include_superseded: bool = False


class SearchResult(BaseModel):
    document: Document
    score: float


class UpdateRequest(PermissiveRequest):
    # The seat making THIS edit. ⚠️ It does not overwrite the original
    # `authored_by` — an editor is not the author, and collapsing the two would
    # silently reassign authorship of every memo the audit touches.
    actor: str | None = None
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    # Appends VERBATIM to existing content — no separator injected. Mutually
    # exclusive with `content`; sending both is refused, not merged. [v0.4.0]
    append: str | None = None
    # PREPENDS VERBATIM to the FRONT of existing content — same contract as
    # `append`, opposite end. Mutually exclusive with both `content` and
    # `append`. [v0.5.0]
    #
    # ⛔ WHY IT EXISTS, and it is not symmetry for its own sake. `append` lands at
    # the END, so it structurally CANNOT carry a correction that must be read
    # first — and a top-of-document banner is how this corpus retires a claim
    # without deleting the record that explains it. Every such banner was
    # therefore a hand-rolled GET → concat → PATCH, with the guarantee that
    # nothing was lost living in the caller's discipline rather than in the API.
    # The memo seat applied 17 in a single cycle on 2026-08-27, each needing its
    # own `new.endswith(old)` assertion. One omitted assertion overwrites a memo
    # and there is no undo.
    # ⇒ This moves that guarantee into the server, where it cannot be forgotten.
    prepend: str | None = None
    # COMPARE-AND-SET GUARD, honoured on this path since 2026-09-06. [v0.5.1]
    #
    # ⛔ IT USED TO BE A PHANTOM FIELD HERE AND REAL ON THE MCP TOOL, WHICH IS
    # THE WORST OF THE THREE AVAILABLE BEHAVIOURS. `memo_update`'s documentation
    # tells callers that `expect_content` is the guard for a destructive
    # `content=` replace — the operation with no undo and no version table. A
    # caller who read that and then wrote over HTTP had the field ACCEPTED (this
    # model tolerates unknown fields by design, see PermissiveRequest), silently
    # DISCARDED, and got back 200 with a fresh `updated_at`: byte-identical to a
    # successful guarded write. Measured 2026-09-06 by `quantum-library` with a
    # deliberately wrong value and a no-op body; reported by Ben, "tell memo to
    # fix". ⭐ Silently accepting a guard you do not apply converts an explicit
    # safety request into a false one — worse than never offering it, because the
    # caller stops writing their own check.
    #
    # ⚠️ It guards ANY update, not only a content write: passing it with a
    # title-only PATCH asserts the body did not move while you retitled. The
    # comparison lives in `_sync_update` (db.py) and is unconditional there.
    # ⇒ Mismatch is 409, never a silent overwrite. Same contract as `memo_update`.
    expect_content: str | None = None
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
    # Byte-identical (whitespace-normalised) memos collapsed before packing.
    # Defaulted so existing callers and stored responses stay valid. [002/FR-119]
    duplicates_dropped: int = 0
    # Hits packed as a window around the matched passage rather than whole.
    spans_windowed: int = 0
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

    @model_validator(mode="after")
    def _class_special_field_requirements(self) -> "Memo":
        """Enforce the per-class field requirements from data-model.md.

        These are cross-field rules, so they need ``mode="after"`` — a
        field_validator cannot see ``class_`` and the special field together.

        Deliberately NOT enforced here:
          * ``class = fact`` requires provenance. The storage mediator owns that
            one, because it has the caller context needed to decide between
            rejecting the write and reclassifying to legacy-unattributed
            (data-model.md says "else migration -> legacy-unattributed"), and a
            model-level raise would make legacy backfill impossible.
          * ``derived_from`` ids must exist. Requires a DB round-trip; done at
            write time per data-model.md, not in a pure model.
        """
        if self.class_ == "constitutional":
            if self.constitution_meta is None:
                raise ValueError("class=constitutional requires constitution_meta (C45)")
            if self.injection_mode != "forcible-constitutional":
                raise ValueError(
                    "class=constitutional requires injection_mode="
                    "forcible-constitutional, got " + self.injection_mode
                )
        elif self.constitution_meta is not None:
            raise ValueError("constitution_meta is only valid on class=constitutional")

        if self.class_ == "time-scoped" and self.time_scope is None:
            raise ValueError("class=time-scoped requires time_scope")

        if self.class_ == "ephemeral-flush" and self.expires_at is None:
            raise ValueError("class=ephemeral-flush requires expires_at")

        # decision-in-progress MAY have reopenability — nullable per C-04.
        return self


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


# Supersede envelopes (FR-003)
class SupersedeLinkRequest(BaseModel):
    """Link TWO EXISTING documents: `new_id` supersedes `old_id`.

    ⭐ The gap this closes, measured 2026-09-28: `SupersedeRequest` below requires
    `content`, so the only supersession path MINTS a new document. A caller holding
    two documents that already exist could not record the relation at all — which is
    why 137 titles in this corpus assert a redirect in PROSE while only 117 edges
    exist. Prose was the only option available.

    ⛔ This retires `old_id` from default search (FR-115). Use it for a genuine
    replacement, NOT for a partial correction — a title reading "CORRECTION to X"
    usually narrows one claim in X, and retiring X would hide a mostly-correct
    document.
    """
    old_id: str
    new_id: str
    actor: str
    reason: str | None = None
    operator_directive_ref: dict[str, Any] | None = None
    db_path: str | None = None


class SupersedeRequest(BaseModel):
    """Request shape for POST /supersede. See FR-003.

    ``content`` and the v2 fields describe the REPLACEMENT memo; ``old_id`` is
    the version being closed out. The new memo's ``valid_from`` is assigned
    server-side to the same instant as the old memo's ``valid_until``, so it is
    deliberately not a caller-supplied field.
    """
    old_id: str
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    class_: MemoClass = Field(alias="class", default="fact")
    injection_mode: InjectionMode = "on-recall"
    scope: list[str] = ["global"]
    provenance: Provenance | None = None
    expires_at: float | None = None
    time_scope: TimeScope | None = None
    reopenability: Reopenability | None = None
    derived_from: list[str] = []
    constitution_meta: ConstitutionMeta | None = None

    # Audit fields for the supersede_edges row.
    actor: str                       # operator:<name> | auditor:<session-id> | mediator:auto
    reason: str | None = None
    operator_directive_ref: dict[str, Any] | None = None

    class Config:
        populate_by_name = True


class SupersedeResponse(BaseModel):
    """Response shape for POST /supersede."""
    old_id: str
    new_id: str
    superseded_at: float
    edge_id: int


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
    # Free-form caller metadata, persisted verbatim. Carries the code-provenance
    # convention (`repo` / `source_files` / `git_sha` / `observed_at`) that
    # `memo-code-staleness` reads. Added 2026-08-11: the field did not exist, so the
    # mediator wrote `metadata={}` and every value a caller sent was discarded.
    metadata: dict[str, Any] | None = None
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
