# Data Model: Memo Renovation v2

Full v2 sqlite schema + Pydantic model definitions for the entities
introduced by the renovation. Additive to the v1 schema — no destructive
changes; v1 columns preserved so rollback flip does not lose reads.

Source of truth for FR-001 through FR-046 field definitions.

## documents (v1 columns + v2 additions)

The `documents` table gains 10 columns; v1 columns stay unchanged.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| **v1 columns (unchanged)** | | | | |
| `id` | TEXT | NO | — | UUIDv4. Primary key. Immutable. |
| `content` | TEXT | NO | — | Memo body. |
| `title` | TEXT | YES | NULL | Optional short label. |
| `tags` | TEXT (JSON) | YES | `'[]'` | Array of tag strings. |
| `metadata` | TEXT (JSON) | YES | `'{}'` | Free-form. |
| `token_count` | INT | YES | NULL | For budgeting. |
| `created_at` | REAL | NO | current-time | Epoch seconds. |
| `updated_at` | REAL | NO | current-time | Epoch seconds. |
| **v2 additions** | | | | |
| `class` | TEXT | NO | `'fact'` | One of: `constitutional`, `behavioral`, `goal`, `verbatim-critical`, `fact`, `decision-in-progress`, `episodic`, `ephemeral-flush`, `time-scoped`, `legacy-unattributed`. Migration sets legacy rows to `legacy-unattributed` where class cannot be inferred. |
| `injection_mode` | TEXT | NO | `'on-recall'` | One of: `forcible-constitutional`, `forcible-current-focus`, `on-recall`, `on-procedure-match`. |
| `scope` | TEXT (JSON) | NO | `'["global"]'` | Array. Elements: `"global"`, `"project:<slug>"`, `"session:<subscriber-id>"`, `"agent-family:<name>"`. |
| `provenance` | TEXT (JSON) | YES | NULL | See Provenance entity below. Nullable only for legacy-unattributed; new writes MUST provide it. |
| `valid_from` | REAL | NO | current-time | Bi-temporal start. |
| `valid_until` | REAL | YES | NULL | Bi-temporal end. NULL = currently true. |
| `expires_at` | REAL | YES | NULL | TTL for `ephemeral-flush` class primarily. Reaper sweeps every 5 min. |
| `time_scope` | TEXT (JSON) | YES | NULL | `{start, end, trip_id?, calendar_event_id?}` for `time-scoped` class. |
| `reopenability` | TEXT (JSON) | YES | NULL | `{challenge_if_delays_build_by_days: int, operator_tempo_hint: str}` for `decision-in-progress` class only. |
| `derived_from` | TEXT (JSON) | YES | NULL | Array of parent memo IDs (for meta-rules built on other memos). |
| `constitution_meta` | TEXT (JSON) | YES | NULL | `{version, ratified_at, amended_at, incident_ref}` — required on `class = constitutional`, else NULL. (C45 / C46) |

**Indexes added:**
- `documents_current_idx` on `(valid_until IS NULL, id)` — powers the default read-path filter.
- `documents_class_scope_idx` on `(class, scope)` — powers InjectionSet computation.
- `documents_expires_idx` on `(expires_at)` where `expires_at IS NOT NULL` — powers TTL reaper.
- `documents_time_scope_idx` on `(time_scope)` where `time_scope IS NOT NULL` — powers time-scoped auto-pin lookup.

## document_embeddings (v1, unchanged)

`document_embeddings USING vec0(doc_id TEXT, embedding FLOAT[1536] cosine)` — no schema change.

## supersede_edges (NEW)

Transition log for bi-temporal supersessions.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-increment. |
| `old_id` | TEXT NOT NULL | Foreign-key-shaped to `documents.id`, no FK constraint (audit-log semantics — old_id may be reaped in extreme cases). |
| `new_id` | TEXT NOT NULL | Same. |
| `superseded_at` | REAL NOT NULL | Epoch seconds; equals `old.valid_until` and `new.valid_from`. |
| `actor` | TEXT NOT NULL | `operator:<name>` or `auditor:<session-id>` or `mediator:auto`. |
| `reason` | TEXT | Optional freetext. |
| `operator_directive_ref` | TEXT | JSON ref to operator DM (session-id + timestamp) when actor is auditor acting on operator authority (per FR-015c). |

**Index**: `supersede_edges_old_idx` on `(old_id)`, `supersede_edges_new_idx` on `(new_id)`.

## mediator_audit_log (NEW)

Answer-loop-audit + storage-mediator audit log (FR-014 + FR-015f + FR-035).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | |
| `mediator_kind` | TEXT | `'retrieval'` or `'storage'`. |
| `at` | REAL NOT NULL | Epoch seconds. |
| `calling_session_id` | TEXT | ATC subscriber id of caller. |
| `calling_role` | TEXT | Agent-family name resolved from session, or `human` for operator direct calls. |
| `query` | TEXT | JSON — retrieval query params, or the incoming memo body/tags for storage. |
| `filters` | TEXT | JSON — the filter set applied. |
| `results` | TEXT | JSON — memo IDs surfaced + ranking scores. |
| `chosen_action` | TEXT | Storage-mediator only: `merge`/`supersede`/`split`/`reject`/`write-new`. |
| `clarification_rounds` | INT | Number of round-trips with caller before persist. |
| `latency_ms` | INT | Total latency. |
| `anomaly_flags` | TEXT | JSON — conflicts, stale-memo hits, gaps. Feeds auditor. |

**Retention**: ≥30 days per FR-014 / FR-015f. Weekly cron sweeps older rows.

**Index**: `mediator_audit_at_idx` on `(at)`, `mediator_audit_session_idx` on `(calling_session_id, at)`.

## injection_set_cache (NEW)

Optional cache; not required for correctness but reduces recomputation on hot repeat-injection paths.

| Column | Type | Notes |
|---|---|---|
| `cache_key` | TEXT PRIMARY KEY | Hash of `(session_id, agent_family, project, time-bucket-5min, MEMORY_posture_flag)`. |
| `injection_set` | TEXT (JSON) | Serialized set of memo IDs + resolved content. |
| `computed_at` | REAL | Epoch seconds. |
| `expires_at` | REAL | Cache TTL, default 5 min. |

Invalidated by any store/supersede that touches a memo in the cached set.

## constitution_proposals (NEW)

Auditor writes here; operator reviews. Never flows into `documents` until operator ratifies.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | |
| `proposed_at` | REAL | |
| `proposed_by` | TEXT | Auditor session id. |
| `layer` | TEXT | `'constitutional'` or `'behavioral'` or `'goal'` or `'verbatim-critical'`. |
| `scope` | TEXT | JSON scope target. |
| `proposed_content` | TEXT | Draft memo body. |
| `proposed_tags` | TEXT | JSON. |
| `evidence` | TEXT | JSON — session UUIDs / event refs the auditor observed. |
| `status` | TEXT | `'pending'`, `'accepted'`, `'rejected'`. |
| `resolved_at` | REAL | Nullable. |
| `resolution_note` | TEXT | Nullable. |
| `resulting_memo_id` | TEXT | Nullable — set when accepted + a memo is created. |

## SESSION_GUIDE_cache (NEW, optional)

Cached snapshot of the `agents`-roster `SESSION_GUIDE` table for offline
resolution when ATC is unavailable.

| Column | Type | Notes |
|---|---|---|
| `session_name` | TEXT PRIMARY KEY | e.g. `quantum-navigator`, `dojo`, `assistant`. |
| `guide_path` | TEXT | Relative or absolute path. |
| `guide_convention` | TEXT | One of `standard`, `agent-guide-md`, `session-handoff-doc`, `skill-based`. |
| `fetched_at` | REAL | Epoch seconds. |

Refreshed daily; a `--refresh-roster` script trigger forces immediate.

## Pydantic Models (Python)

### Memo

```python
class Memo(BaseModel):
    id: UUID
    content: str
    title: str | None = None
    tags: list[str] = []
    metadata: dict = {}
    token_count: int | None = None
    created_at: float
    updated_at: float

    # v2 additions
    class_: Literal[
        "constitutional", "behavioral", "goal", "verbatim-critical",
        "fact", "decision-in-progress", "episodic",
        "ephemeral-flush", "time-scoped", "legacy-unattributed",
    ] = Field(alias="class", default="fact")
    injection_mode: Literal[
        "forcible-constitutional", "forcible-current-focus",
        "on-recall", "on-procedure-match",
    ] = "on-recall"
    scope: list[str] = ["global"]
    provenance: Provenance | None = None
    valid_from: float
    valid_until: float | None = None
    expires_at: float | None = None
    time_scope: TimeScope | None = None
    reopenability: Reopenability | None = None
    derived_from: list[UUID] = []
    constitution_meta: ConstitutionMeta | None = None
```

### Provenance (first-class, per Principle III + C34)

```python
class Provenance(BaseModel):
    claude_log_ref: ClaudeLogRef | None = None
    git_ref: GitRef | None = None
    gmail_msg_id: str | None = None
    phony_ref: PhonyRef | None = None
    atc_ref: ATCRef | None = None
    url: str | None = None
    derived_from: list[UUID] = []
    # At least one of the above MUST be set on new writes.
    # Legacy-unattributed memos are the only exception.

class ClaudeLogRef(BaseModel):
    host: str
    project_dir: str
    session_uuid: UUID
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
```

### TimeScope, Reopenability, ConstitutionMeta

```python
class TimeScope(BaseModel):
    start: float
    end: float
    trip_id: str | None = None
    calendar_event_id: str | None = None

class Reopenability(BaseModel):
    challenge_if_delays_build_by_days: int | None = None
    operator_tempo_hint: str | None = None

class ConstitutionMeta(BaseModel):
    version: str  # semver, e.g. "1.3.0"
    ratified_at: float
    amended_at: float
    incident_ref: str  # session UUID + timestamp, git SHA, or ATC event id
```

### InjectionSet (computed, not stored except in cache)

```python
class InjectionSet(BaseModel):
    session_id: str
    agent_family: str | None
    project: str | None
    current_time: float
    memory_posture: Literal["on", "off"]
    forcible_constitutional: list[Memo]
    forcible_current_focus: list[Memo]
    transclusions: list[TransclusionResolution]
    spec_kit_constitution_content: str | None = None
    token_budget_used: int
    token_budget_ceiling: int  # default 5000 per C-02
    # Rendered as `additionalContext` for the hook payload.

class TransclusionResolution(BaseModel):
    source_file: str        # Which CLAUDE.md / guide / rule referenced this
    referenced_uuid: UUID
    resolved_memo: Memo
```

## Relationships

```
documents 1 ─── * document_embeddings   (doc_id FK)
documents 1 ─── * supersede_edges       (old_id FK, new_id FK)
documents * ──> * documents             (derived_from — a memo can cite N parents)
documents 0..1 ─ 1 constitution_proposals (resulting_memo_id when accepted)
mediator_audit_log — no FKs (pure event log)
injection_set_cache — no FKs (materialized view)
SESSION_GUIDE_cache — no FKs (external-system snapshot)
```

## State Transitions

**Memo lifecycle:**
```
[created] --create()--> current (valid_from = now, valid_until = NULL)
   │
   ├─ [ephemeral-flush class] --expires_at reached--> [reaped/deleted]
   ├─ [time-scoped class] --outside [start,end]--> [depinned from injection set, kept in store]
   ├─ [any] --supersede(new)--> superseded (valid_until = now); new: current
   └─ [operator-directed delete] --delete()--> [hard-deleted, edge preserved]
```

**Constitution-proposal lifecycle:**
```
[created by auditor] --status=pending--> awaiting operator
   ├─ operator accepts --status=accepted, resulting_memo_id set--> memo created
   └─ operator rejects --status=rejected, resolution_note captured--> archived
```

**Supersede-chain coalescing (edge case in spec):**
```
[N-length chain of A→B→C→...→Z] --coalescer runs weekly-->
   [Z current; A..Y summary memo linked from Z with content:"previous values: A@t1, B@t2, ..."]
   Edges retained; only memo bodies for A..Y compact into the summary.
```

## Validation Rules

- `class = constitutional` REQUIRES `constitution_meta` and `injection_mode = forcible-constitutional`.
- `class = fact` SHOULD have `provenance`. **AMENDED 2026-07-30 (operator
  decision)**: a fact WITHOUT provenance stays `class = fact` with
  `provenance: null` and is tagged **`provenance-pending`**. It is NOT demoted
  to `legacy-unattributed`.
  *Why*: the original rule caught **86.8%** of the real v1 corpus, which
  records an origin KIND (`assistant-sourced`, `git-sourced`) but almost never
  a locator. Operator: *"we should not be heavily penalizing the vast bulk of
  our corpus which is actually good facts but don't have a readily known
  provenance because we haven't done the record keeping of it yet."*
  The tag is load-bearing — the plan is to re-attribute these as they are
  proven out, and without a marker they are unfindable.
  `legacy-unattributed` is now reserved for memos with NO usable signal.
- `class = time-scoped` REQUIRES `time_scope`.
- `class = decision-in-progress` MAY have `reopenability` (nullable per spec — C04 kept simple).
- `class = ephemeral-flush` REQUIRES `expires_at`.
- `valid_from <= valid_until` when `valid_until IS NOT NULL`.
- `derived_from` UUIDs MUST exist in `documents` (validated at write time via a fast SELECT; NOT a FK because bulk-migration can't easily maintain declarative FKs during backfill).
