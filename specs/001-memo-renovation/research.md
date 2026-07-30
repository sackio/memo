# Research: Memo Renovation

Decision rationale for the technical choices in `plan.md`. Every choice
is a Decision + Rationale + Alternatives-considered block, per the
speckit-plan protocol. Sourced from the 7-round Ben interview, 6
background research agents, and the resolved clarifications C-01..C-10.

## R-01 — Language + runtime

**Decision**: Python 3.11, keep the v1 stack.

**Rationale**:
- v1 already runs Python + FastAPI + sqlite-vec at
  `src/memo/main.py:35-43`; no forcing function to change.
- MCP server + HTTP share a single FastAPI process — no protocol
  gymnastics required to extend both simultaneously.
- Mediator hot-path is dominated by sqlite reads + numpy cosine, not
  Python overhead. Preliminary math: 7,339 memos × 1536-dim float =
  ~45 MB working set; well within cache.
- If soak-test misses the SC-006 latency budget (P95 ≤ 1.5 s), targeted
  Cython or a Rust extension for the filter-chain inner loop is a
  spike, not a rewrite.

**Alternatives considered**:
- Rust for the whole server: rejected — rewrite cost outweighs any
  hot-path win at current scale + memo's operator-experience is Python.
- Go: same reasoning; also loses OpenAI SDK ergonomics.
- Rust only for the mediator filter chain: **deferred**, revisit at
  Phase G soak-test if measurements say so.

## R-02 — Web framework

**Decision**: Keep FastAPI.

**Rationale**: MCP + HTTP dual-serve is already load-bearing on it; the
mediator + injection endpoints slot in cleanly; async-native for the
outbound Conductor push calls; Pydantic v2 gives us the model layer for
free.

**Alternatives considered**: Litestar (smaller ecosystem, no
justification); Flask + BackgroundTasks (loses async story for
Conductor push).

## R-03 — Storage substrate

**Decision**: Keep sqlite + sqlite-vec for now. Abstract the
bi-temporal + supersession-edge layer behind a small repository
interface so a Postgres migration becomes a single-file swap when scale
justifies it.

**Rationale**:
- 7,339 memos + 1536-dim embeddings fit comfortably; sqlite handles the
  read pattern (memo_get by UUID + vector similarity + tag filter).
- The Explore agent's original sqlite-vec chunk investigation is
  irrelevant at v1 scale.
- Bi-temporal adds two columns (`valid_from`, `valid_until`) + a
  supersede-chain table — all trivially SQLite-compatible.
- Repository abstraction (~200 LOC) means the Rust/Postgres migration
  path stays open without upfront cost.

**Alternatives considered**:
- Postgres + pgvector from day one: rejected — scale doesn't justify
  the operations burden yet; the migration path from sqlite is
  well-worn if needed.
- Redis for hot cache: rejected — sqlite's page cache already provides
  the same benefit at zero operational cost.
- Graphiti (bi-temporal graph DB): **inspiration** for the temporal
  edges model (stolen per Agent D report), but not adopted as the
  substrate — too heavy for the fleet + operationally unfamiliar.

## R-04 — Bi-temporal implementation in sqlite

**Decision**: Two columns on `documents` (`valid_from REAL`,
`valid_until REAL` — nullable) + separate `supersede_edges` table for
the "A → B superseded" transitions.

**Rationale**:
- `valid_from = created_at` at insert. `valid_until` stays NULL until a
  supersede fires.
- Index on `(valid_until IS NULL, id)` for the default-current read
  path — cheap, hot.
- `supersede_edges (old_id TEXT, new_id TEXT, superseded_at REAL,
  actor TEXT, reason TEXT)` gives the auditor a queryable transition
  log without walking the documents table.
- Point-in-time query = `WHERE valid_from <= t AND (valid_until IS NULL
  OR valid_until > t)` — sqlite optimizer handles it.
- The `agents` supervisor's edge-coalescing pattern (from spec.md edge
  cases) applies: an IP that changes weekly doesn't accrue 52 rows;
  the reconciler coalesces older edges into a `superseded_summary`
  memo linked from the current one.

**Alternatives considered**:
- Append-only log table + materialized-view current: rejected — read
  path stays simple with the column approach.
- History mirror table (copy-on-supersede): rejected — duplicates
  storage; column approach is cheaper.

## R-05 — Embedding provider

**Decision**: Keep `openai/text-embedding-3-small` via OpenRouter.

**Rationale**: v1 is already indexed with this dim (1536); switching
requires re-embedding all 7,339 memos + a schema change on the
sqlite-vec virtual table. No accuracy complaint on file; no cost
concern (backfill re-embed ≈ $0.20 per Agent D report).

**Alternatives considered**: `text-embedding-3-large`, Cohere embed-v3
— rejected on no-signal-to-change-what-works grounds.

## R-06 — Mediator hot-path implementation shape

**Decision**: In-process filter chain in the FastAPI process (option
(c) from C-09), pure Python + numpy for the ranking math, with an LLM
fallback triggered when (a) the deterministic filter returns > N
candidates and needs synthesis, or (b) top candidates conflict.

**Rationale**:
- Locked at C-09 by operator.
- Same shape applies to the storage mediator's reconcile-before-write
  path.
- Framing: "working module refined over time; component boundaries
  designed for tunability" — filter chain is a composable pipeline of
  strategy classes.

**Alternatives considered**:
- Subprocess-per-call: rejected on latency + no-shared-cache grounds.
- Subagent per call: rejected on cost + latency grounds; only used as
  fallback path.

## R-07 — Auditor implementation shape

**Decision**: Hybrid per C-03. Long-running per-agent-family shadow
process (lean model, event-driven, subscribed to Conductor for its
assigned scope) + Claude Code hook triggers (SessionStart,
SessionStart:compact, PreCompact, PostCompact, SessionStop, SessionEnd)
that also fire the auditor.

**Rationale**:
- Both routes give distinct signals: long-running catches things
  in-transcript; hook triggers catch things at lifecycle boundaries.
- Lean = small model (Haiku-tier for the shadow watchers is enough for
  the classify/route work); reserve heavier models for the global
  auditor's daily synthesis.
- Start intrusive per Ben's direction, tune down.

**Alternatives considered**: skill-only (misses in-transcript
signals); long-running-only (misses hook signals + heavier operations
cost).

## R-08 — Provider abstractions (Principle VIII)

**Decision**: Abstract `Conductor` and `AgentController` base classes;
concrete `ATCConductor` and `AgentsSupervisor` adapters; `NullConductor`
+ `NullAgentController` for standalone mode. Each interface defined by
a Pydantic event/request schema, transport-agnostic.

**Rationale**:
- Locked at C-64.
- FR-045 (standalone) requires the null adapters or memo can't be
  brought up without ATC.
- ATC is being independently revised (per Ben) — memo's contract must
  outlive ATC's schema evolution (FR-046).
- Bridge concept is generic (per Ben's 14:49 amendment); the pull
  interface accepts any bridge event that matches the standard schema
  without memo-side hardcoding.

**Alternatives considered**:
- Direct ATC coupling (no abstraction): rejected — violates VIII.
- Plugin-registration DSL: over-engineered for two providers; base
  class inheritance is the right weight.

## R-09 — Layer 2 injection mechanism

**Decision**: A hook-driven Layer 2 gap-fill computer that produces an
`additionalContext` string at SessionStart / PostCompact /
InstructionsLoaded (whichever hook is most appropriate for each
substream).

Layer 2 content assembly order:
1. `.specify/memory/constitution.md` at cwd or walked-up tree (if
   present; auto-detected).
2. `memo:<uuid>` transclusion resolutions from all auto-loaded files
   (CLAUDE.md, guide, `.claude/rules/*.md`).
3. Session-scoped `constitutional`, `behavioral`, `goal`, and
   `verbatim-critical` memos matching the session's `scope` (via
   guide-resolved agent-family + project).
4. Current-focus `time-scoped` memos whose `[start, end]` covers now.
5. `ephemeral-flush` memos from the previous session with
   `flush-generation:<N-1>` (session continuity).

**Rationale**: Aligns with C-01's 3-layer stack; content is generated
deterministically from the memo store; the only LLM in the loop is the
inference calls the mediator may make when computing the resolved
transclusion set (rare).

**Alternatives considered**: Subagent-computed (rejected — latency
per session start too high); static system-prompt (rejected — can't
be session-scoped).

## R-10 — Cutover strategy

**Decision**: Deferred behind soak-test operator confidence gate per
C-08. Default assumption post-gate is waves (quantum → assistant →
rest) with one-way v1→v2 replication during transition.

**Rationale**: Locked at C-08.

## R-11 — MEMORY.md posture detection

**Decision**: At each Layer 2 injection, memo reads the calling
session's environment for `CLAUDE_CODE_DISABLE_AUTO_MEMORY`. Preferred
source: `/proc/<pid>/environ` when the hook payload includes PID
(SessionStart does). Fallback: SESSION_GUIDE roster lookup by session
name; further fallback: assume MEMORY.md is on.

**Rationale**: C-71. The quantum guardians rely on memo carrying the
entire memory layer they receive; failure to detect breaks them.

**Alternatives considered**: Query the AgentController for the flag
— rejected because AgentController is a pluggable interface + might
not be present; PID-based lookup is provider-agnostic.

## R-12 — Guide-path resolver

**Decision**: A `GuideResolver` class that:
1. Takes a session name.
2. Queries the `agents`-roster `SESSION_GUIDE` table (via ATC DM or
   by parsing `~/scripts/agents` — the roster script).
3. Returns the guide path from the roster if present.
4. If session is ad-hoc, reads `/proc/<pid>/cmdline` for the `--guide`
   flag value.
5. Falls back to `None` (memo skips guide-derived injection).

**Rationale**: C67 + C68 + C69. Handles all 4 conventions
(`.claude/guides/<name>.md`, `AGENT_GUIDE.md`,
`docs/SESSION_HANDOFF.md`, `.claude/skills/<name>/SKILL.md`) via the
roster table which encodes them directly.

**Alternatives considered**: String-munge `name → .claude/guides/name.md`
— rejected per Agent H caveat #1.

## R-13 — Migration duplicate detection

**Decision**: Cosine similarity ≥ 0.90 + title 4-gram overlap ≥ 60% +
LLM escape on borderline candidates (cosine ∈ [0.85, 0.90]).

**Rationale**: Locked at C-06.

## R-14 — Ephemeral-flush TTL enforcement

**Decision**: Add `expires_at REAL` column; a lightweight background
task in the FastAPI process sweeps expired rows every 5 minutes. NOT
memo-minder cron (per C15 — too slow for per-compact churn).

**Rationale**: C15. The alternative (sqlite-triggered auto-delete) is
not portable. The 5-min sweep is cheap and self-heals if it misses a
tick.

## R-15 — Testing strategy

**Decision**:
- **Unit** (pytest): filter chain strategies, guide resolver, provenance
  parsing, transclusion regex, posture detection, bi-temporal edge cases.
- **Contract** (pytest + httpx): one test file per `contracts/*.md`, asserts
  request/response schema conformance against Pydantic models.
- **Integration** (pytest + real memo container): mediator → store →
  supersede → mediator round-trips; hook payload → injection-set →
  session context; end-to-end migration on a synthetic 1000-memo corpus.
- **Soak** (Phase G; script-driven, not pytest): background Claude Code
  agents driving real + synthetic workloads against the ported corpus.

**Rationale**: Standard pyramid; soak is separate because it's not a
regression gate — it's the operator confidence gate for cutover per
C-08.

## R-16 — Backup + rollback

**Decision**: v1 sqlite DB untouched throughout v2 development, soak,
and cutover. Backups continue to `/mnt/backup/memo/` daily +
per-cutover-checkpoint. Rollback = MCP-flip reversal (v2 → v1 in
`~/.claude.json`).

**Rationale**: C-08 sequencing + operator's operational-safety
requirement + C38 forever-backup posture.

## R-17 — LLM inference provider for the mediators

**Decision**: The mediators' generative LLM calls are served by an
**interactive Claude Code session**, reached through an `LLMProvider`
adapter (`src/memo/providers/llm/`), NOT by a per-token inference API.

Specifics:

- **A dedicated session, `memo-llm`** — deliberately NOT the standing
  `memo` session, despite that being the obvious candidate. Three
  reasons: (a) **reentrancy** — the `memo` session has memo hooks, so a
  mediator call into it can trigger its own `memo_store`/`memo_recall`
  and loop; (b) **availability** — `memo` compacts at 06:10 and runs
  memo-minder at 06:17 daily, so it is unavailable in that window;
  (c) **blast radius** — if `memo` wedges, every mediated write on the
  fleet would wedge with it.
- **`claude -p` is PROHIBITED.** Passing a prompt programmatically to
  `claude` is billed as API usage; an interactive session rides the Max
  subscription already being paid for. This is the same reason
  memo-minder was moved off its `claude -p` host cron to an interactive
  session cron on 2026-07-15. Any future adapter that shells out to
  `claude -p` is a regression, not an optimization.
- **Degrade, never block.** If the session is busy, compacting, or down,
  the mediator does NOT fail the caller: recall returns its search-only
  answer with an `anomalies` entry, and store writes-new and flags the
  auditor. A 10s soft timeout bounds the wait.
- **Escalate to the supervisor on outage.** On unavailability the
  provider DMs the `agents` supervisor session over ATC to respawn
  `memo-llm`, so a dead session self-heals rather than silently
  degrading recall quality indefinitely. **Rate-limited to one notify
  per outage**, not per failed call — a dead session plus fleet-wide
  memo traffic would otherwise blast the supervisor with hundreds of
  DMs, which is precisely the thundering-herd failure the operator's
  CLAUDE.md §5 warns about.
- **Embeddings are out of scope for this decision** and stay on
  OpenRouter `text-embedding-3-small` per R-05 — there is no Claude
  embedding endpoint. R-17 governs generative calls only.
- **`auto_store`'s `openai/gpt-4o-mini`** is left as-is until T036 folds
  auto_store into the storage-mediator path, at which point it inherits
  this provider.

**Rationale**: Operator directive 2026-07-29 ("use an interactive
Claude Code session for the LLM ... I don't want to use the -p flag ...
that's programmatic usage of Claude which can cause extra [cost]").
Cost is the driver; the subscription is already paid for. The spec had
never actually named a provider for the mediator inference calls — it
only ever said "LLM fallback" / "LLM escape" — so this fills an
unspecified slot rather than replacing a prior decision.

Fits the existing Principle VIII provider pattern from R-08: an
abstract base + a concrete adapter + a `null` adapter, selected by a
`MEMO_LLM_PROVIDER` env var.

**Sequencing**: Phase 3 builds the mediators against the `LLMProvider`
interface with a deterministic **null adapter** (search-only, no
inference), so the mediators land complete and fully testable without
the transport existing. The concrete `claude_session` adapter lands in
Phase 5 alongside the other provider adapters.

**Alternatives considered**:
- *OpenRouter / direct Anthropic API* — rejected by the operator on
  cost; duplicates spend we already have via the Max subscription.
- *`claude -p` per call* — rejected, see above. It is the thing that
  makes this billable.
- *Serve from the `memo` session itself* — rejected on reentrancy,
  availability, and blast radius (above).

**Consequence for the contracts**: the latencies quoted in
`contracts/mediator-recall.md` (87ms happy path, ~1450ms with fallback)
assumed an in-process API call and are not achievable across a session
round-trip. Amended there. The happy path is unaffected — it makes no
LLM call at all.
