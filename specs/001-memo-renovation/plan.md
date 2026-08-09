# Implementation Plan: Memo Renovation

**Branch**: `001-memo-renovation` | **Date**: 2026-07-29 | **Spec**: `specs/001-memo-renovation/spec.md`

**Input**: Feature specification + Constitution v1.3.0 + interview notes (71 spec constraints C1–C71) + 6 background research reports.

## Summary

Memo becomes a durable, cross-session memory substrate for an N-agent fleet, adding four things v1 lacks: (1) a class-taxonomy with forcible-injection for behavioral/goal/verbatim-critical memos, (2) bi-temporal supersession so history is preserved in the live store, (3) mediators on both the read and write paths (algorithm-primary + LLM fallback), and (4) integration interfaces to a pluggable Conductor (real-time messaging + bridges + scheduling; ATC today) and pluggable AgentController (session lifecycle control; `agents` supervisor today). Constitution Principle VIII gates that memo must run standalone; ATC + agents are default providers, not required. Delivery is v2 in a separate git worktree + separate MCP + separate sqlite DB; full corpus port precedes any cutover consideration; cutover decision is deferred behind a soak-test operator confidence gate (C-08).

## Technical Context

**Language/Version**: Python 3.11 (matches v1). Hot-path mediator stays in Python; profile in soak-test phase — if p95 misses the ≤500 ms target we revisit selectively (either Cython'd inner loops, a Rust extension for the filter chain, or move sqlite reads to an async pool). No pre-emptive rewrite.

**Primary Dependencies**: FastAPI (keep — v1 already runs on it, MCP + HTTP share one process at `src/memo/main.py:35-43`), sqlite + sqlite-vec (keep — no scale trigger to leave; abstract the bi-temporal edges layer behind a small repository interface so a Postgres migration is possible later without touching call sites), `openai/text-embedding-3-small` via OpenRouter (keep — already deployed), `httpx` (already present via mcp / openai) for outbound Conductor push, Pydantic v2 for schemas.

**Storage**: sqlite + sqlite-vec on the global server4 DB (`/data/memo.db` inside the container, canonical since 2026-06-29). Schema evolves additively (new columns + new tables); v1 columns preserved so a rollback flip does not lose reads. Backups continue writing to `/mnt/backup/memo/` forever per C38.

**Testing**: pytest for unit + integration (contract tests per interface); soak-test phase (C-08 step 3) uses background Claude Code agents driving synthetic + real query workloads against the ported corpus with instrumentation on the mediator's answer-loop-audit log (FR-035).

**Target Platform**: Linux server (server4, Ubuntu on shared NAS-backed image), Docker Compose. Standalone-operation requirement per Principle VIII: v2 MUST run correctly with no Conductor and no AgentController configured.

**Project Type**: web-service (single Python process serving HTTP + MCP over stdio + integration-provider hooks). No frontend.

**Performance Goals**:
- Mediator read (P95 ≤ 1.5 s, median ≤ 500 ms) per SC-006, measured at current corpus size (7339 memos)
- Storage-mediator write with reconcile ≤ 2 s per call (soft; not a spec commit yet)
- InjectionSet computation ≤ 200 ms at session start (blocks nothing else; asynchronous ok if hook allows)
- Standalone operation (no Conductor / AgentController) MUST not regress hot-path latencies

**Constraints**:
- Bi-temporal on the STORE — read-path filters `valid_until IS NULL` by default; point-in-time queries opt-in (C39, Principle VI)
- INDEX LAG invariant: post-write reads use `memo_get(full_uuid)` + settle window (C2 / agents supervisor)
- Provenance as first-class field, not tag (C34, Principle III)
- Facts are authoritative; agents cannot refute (C28, Principle II)
- Ephemeral-flush class MUST auto-reap on TTL (C15) — cron alone is insufficient for per-compact churn
- Layer 2 injection MUST detect `CLAUDE_CODE_DISABLE_AUTO_MEMORY` per session and adapt augment/take-over (C71)
- Guide-path resolver MUST handle 4 conventions (`.claude/guides/<name>.md`, `AGENT_GUIDE.md`, `docs/SESSION_HANDOFF.md`, `.claude/skills/<name>/SKILL.md`) and MUST go through the `agents`-roster `SESSION_GUIDE` table, not string-munge from name (C67, C68)
- Memo MUST NOT write to `.claude/guides/*`, project `CLAUDE.md`, `~/.claude/CLAUDE.md`, or `.env` (C62) — those are agents-roster + operator-owned

**Scale/Scope**: 7339 memos at spec time; assume 3–4× growth headroom on sqlite (~30k memos comfortably; substrate change is out-of-scope for this spec). ~30 mapped agent-family sessions (SESSION_GUIDE map from agents supervisor) plus ad-hoc sessions. Fleet: office + server4 + server5.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Each principle from `.specify/memory/constitution.md` (v1.3.0) evaluated against this plan:

> ⚠️ **This gate was evaluated against v1.3.0; the constitution is now v2.0.0.**
> Principle II was redefined on 2026-07-30 by operator directive — "deletion is
> earned, not forbidden" replaced "only operators can refute facts" — and the
> footer was stamped on 2026-07-31 (T151). **The check below is NOT re-run, and
> does not need to be**, because the change is strictly *more permissive*: it
> removes a constraint rather than adding one, so nothing that passed under the
> stricter rule can fail under the looser one. The row for Principle II now
> understates what is allowed rather than overstating it. A future amendment that
> *tightens* a principle would require re-running this gate; this one does not.

| Principle | Gate check | Status |
|---|---|---|
| **I. Agents Are the Primary Users** | Every interface (mediator, injection-set, hook contracts) shaped for agent-first consumption; human paths (`/recall`, `/memorize`) are pass-throughs to the same mediators, not privileged shortcuts. | ✅ PASS |
| **II. Facts Are Authoritative** | `POST /store` mediator FR-015c rejects fact-refutations without an operator-directive reference. Direct raw-store path requires `bypass_mediator=true` + operator auth (FR-015g). | ✅ PASS |
| **III. Provenance Is First-Class** | `provenance` block is a first-class column on `documents` table (see data-model.md), not a tag. Every FR that creates memos requires provenance. Backfill emits `legacy-unattributed` when absent (C-07 / C50). | ✅ PASS |
| **IV. Behavioral Rules Are Forcibly Injected** | `GET /injection-set` FR-016 returns forcible sets; hook wiring (FR-017, FR-018) invokes it on session-start + post-compact; retrieval is NOT the delivery path for behavioral rules. | ✅ PASS |
| **V. Operator Owns the Constitution** | Constitutional-class memos require operator authority to write/modify. Auditor writes `constitution-proposal` tagged `proposal-pending` only (FR-023, C40). | ✅ PASS |
| **VI. Bi-Temporal Truth in Store, Filtered on Read** | `valid_from` / `valid_until` on every memo (FR-002); `POST /supersede` atomic (FR-003); mediator filters `valid_until IS NULL` by default (FR-011). | ✅ PASS |
| **VII. Every Store and Read Op Goes Through a Mediator** | Both mediators specified (FR-010, FR-015a). Direct raw-store gated behind `bypass_mediator=true` + operator auth. Session agents never call raw endpoints. | ✅ PASS |
| **VIII. Integration-Ready, Not Integration-Bound** | Conductor + AgentController + Claude Code hooks are pluggable (FR-041..046). Memo MUST run standalone (FR-045). Bridge concept extensible (FR-042). | ✅ PASS |

All gates PASS. No violations to justify in the Complexity Tracking section.

## Project Structure

### Documentation (this feature)

```text
specs/001-memo-renovation/
├── plan.md              # This file
├── research.md          # Phase 0 output — decision rationale for the choices above
├── data-model.md        # Phase 1 output — full v2 schema + entities
├── quickstart.md        # Phase 1 output — runnable validation guide (v2 bring-up + soak-test recipe)
├── contracts/
│   ├── mediator-recall.md          # POST /recall (retrieval mediator) contract
│   ├── mediator-store.md           # POST /store (storage mediator) contract
│   ├── injection-set.md            # GET /injection-set contract
│   ├── conductor-push.md           # Outbound event schema (memo → Conductor)
│   ├── conductor-pull.md           # Inbound event schema (Conductor → memo)
│   ├── agent-controller.md         # Session-control interface (memo → AgentController)
│   ├── claude-code-hooks.md        # SessionStart / PostCompact / InstructionsLoaded / SessionEnd contracts
│   └── migration-cli.md            # memo-migrate-backfill script contract + audit-log format
└── tasks.md                # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (v2 worktree layout)

v2 lives in a git worktree at `../memo-v2` off the main `memo` repo, so the two coexist for the soak-test phase without cross-contamination.

```text
memo-v2/
├── src/memo/
│   ├── main.py                    # FastAPI app (HTTP + MCP), unchanged shape from v1
│   ├── config.py                  # Settings (Pydantic); adds v2-specific flags
│   ├── db.py                      # Connection pool; bi-temporal helpers
│   ├── models.py                  # NEW — Pydantic v2 models for Memo, Provenance, InjectionSet, MediatorRequest/Response
│   ├── mediators/                 # NEW
│   │   ├── recall.py              # Retrieval mediator (in-process filter chain + LLM fallback)
│   │   ├── store.py               # Storage mediator (reconcile-before-write + clarify + canonical tag/class inference)
│   │   └── filters.py             # Dedup, bi-temporal, recency-boost, tag-class-boost, scope filter
│   ├── injection/                 # NEW
│   │   ├── set.py                 # GET /injection-set resolver (Layer 2 gap-fill computer)
│   │   ├── transclude.py          # memo:<uuid> transclusion resolver
│   │   ├── posture.py             # Per-session CLAUDE_CODE_DISABLE_AUTO_MEMORY detection
│   │   └── guides.py              # SESSION_GUIDE resolver (via agents DM or roster parse; 4 path conventions)
│   ├── providers/                 # NEW — pluggable-provider adapters per Principle VIII
│   │   ├── conductor/
│   │   │   ├── base.py            # Abstract Conductor interface (push/pull, scheduled/event triggers, bridge event schema)
│   │   │   └── atc.py             # Concrete ATC adapter (default provider)
│   │   ├── agent_controller/
│   │   │   ├── base.py            # Abstract AgentController interface (spawn/respawn/clear/change-model/compact/interrupt/inject)
│   │   │   └── agents_supervisor.py  # Concrete `agents`-supervisor adapter (default provider)
│   │   └── null.py                # Null adapters for standalone mode (Principle VIII / FR-045)
│   ├── auditor/                   # NEW
│   │   ├── shadow.py              # Per-session shadow auditor (long-running, Conductor-subscribed)
│   │   ├── global_sweep.py        # Global auditor (daily cron) — policing + cross-session synthesis + supersession-chain coalescing (edge from spec.md)
│   │   ├── liveness.py            # Content-based liveness monitor (mirrors stale-guide-detector; C70)
│   │   └── proposals.py           # constitution-proposal generator (never writes constitutional memos directly)
│   ├── hooks/                     # NEW — Claude Code hook endpoints
│   │   ├── session_start.py       # SessionStart hook payload handler + additionalContext response
│   │   ├── instructions_loaded.py # InstructionsLoaded hook (post-CLAUDE.md layer)
│   │   ├── post_compact.py        # PostCompact re-injection (replaces the atc-precompact-beacon.py subagent dance)
│   │   └── session_end.py         # SessionEnd — flush + auditor final sweep
│   ├── log_queries.py             # NEW — intelligent Claude Code log query tool (FR-033)
│   ├── clarify.py                 # NEW — storage mediator synchronous clarification round-trip
│   └── auto_store.py              # Kept from v1; refactored to route through storage mediator
├── migrations/
│   ├── 001_v2_schema.sql          # NEW columns on documents; new tables for provenance + audit-log + injection-cache + supersede-chain
│   ├── 002_bi_temporal_indexes.sql
│   └── 003_seed_canonical_tags.sql # Retire hard-rule/ben-hard-rule/behavioral-rule fragmentation (C44)
├── scripts/
│   ├── memo-migrate-backfill      # V1 corpus → V2 (classify/retag/provenance-link/split/merge/redirect/dedupe)
│   ├── memo-migrate-verify        # Post-backfill parity + integrity check
│   ├── memo-soak-test             # Kick-tires background test workload driver (C-08 step 3)
│   └── memo-mcp-flip              # MCP config flip helper (v1↔v2), used at cutover
├── docker-compose.yml             # v2 stack: separate container name, separate port, separate volume
├── pyproject.toml
└── tests/
    ├── contract/                  # One per contracts/*.md file
    ├── integration/               # End-to-end mediator + auditor + hook flows
    ├── unit/                      # Filter chain, transclusion, guide resolver, provenance
    └── soak/                      # Synthetic + replayed real workloads for the soak phase
```

**Structure Decision**: Single-project web-service layout, matching v1's shape. Modularized further under `src/memo/` into `mediators/`, `injection/`, `providers/`, `auditor/`, `hooks/` — new capability groups introduced by v2. No monorepo split, no frontend, no separate CLI package (existing `memo-migrate-*` scripts stay under `scripts/`).

## Phased Build Order (respects C-08 sequencing)

Estimates are for an AI agent developing autonomously — hours, not
weeks. Total dev time ~8-12 hours across Phases A-F; then Phase G is
runtime (as long as the soak workload is configured to run), and
Phase H is deferred behind the operator confidence gate.

1. **Phase A — Schema + core CRUD** (~1 hr)
   - Migrations 001-003; models.py; db.py bi-temporal helpers; auto_store.py refactored to storage-mediator route.
   - Deliverable: v2 accepts writes with class/provenance/valid_from/valid_until fields; raw storage works.

2. **Phase B — Mediators** (~2-3 hr)
   - Retrieval mediator: filter chain + LLM fallback; observability log; `POST /recall`.
   - Storage mediator: reconcile-before-write + canonical-tag inference + clarify round-trip; `POST /store` refactor.
   - Deliverable: FRs 010–015g pass their contract tests.

3. **Phase C — Injection + Layer 2 hooks** (~1-2 hr)
   - `GET /injection-set`; transclusion resolver; posture detection; SESSION_GUIDE resolver.
   - Claude Code hook endpoints wired via `~/.claude/settings.json` on server4 only initially.
   - Deliverable: fresh session on v2 sees the constitutional layer + auto-loaded spec-kit constitution.md.

4. **Phase D — Provider abstractions + adapters** (~1 hr)
   - Base + ATC + agents-supervisor + null adapters. Standalone mode verified (FR-045).
   - Deliverable: FRs 041–046 pass; null-mode round-trip works.

5. **Phase E — Auditor** (~2-3 hr)
   - Shadow (long-running, per-session, Conductor-subscribed).
   - Global (cron; polices shadows; supersession-chain coalescing).
   - Liveness monitor (content-based, mirrors stale-guide-detector).
   - Proposal generator (`constitution-proposal` tagged writes).
   - Deliverable: FRs 021–026 pass; auditor visible on ATC posting findings.

6. **Phase F — Migration script** (~1-2 hr)
   - `memo-migrate-backfill` walks v1 corpus into v2; audit-log produced.
   - `memo-migrate-verify` post-check.
   - Deliverable: 7339 v1 memos ported into v2 with ≥95% classified into a real class (SC-009).

7. **Phase G — Soak test** (runtime, not dev time — as long as operator wants the workload to run; the C-08 confidence gate)
   - `memo-soak-test` drives synthetic + real query workloads against v2 with the ported corpus.
   - Background test agents (spawned via the AgentController adapter) kick tires on mediator, auditor, injection hooks, reconciliation, recall-corrections loop.
   - Instrumentation report: mediator latencies (SC-006), classification coverage (SC-009), false-positive compactions (C-10 tuning), dupe-cluster collapse (SC-005).
   - Deliverable: operator (Ben) reviews soak report and decides IF cutover proceeds + what shape (waves default assumption).

8. **Phase H — Cutover** (only after C-08 confidence gate; timing operator-controlled)
   - Shape decided at gate; default assumption = waves (quantum first, assistant next, everything else third).
   - `memo-mcp-flip` per-host per-session per-wave with v1→v2 one-way write replication during the transition window.
   - Deliverable: fleet on v2, v1 quiesced, backups retained forever per C38.

**Bug-recovery model**: issues found during soak-test → back to the
relevant phase for another chunk of dev time (still hours), then
re-soak. Multiple soak-cycles are cheap; committing to a bad cutover
is expensive.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

**None.** All 8 constitutional principles pass. No architectural workarounds needed.

## Post-Design Constitution Re-Check

Deferred to after Phase 1 artifacts are complete (data-model.md, contracts/, quickstart.md). Any design choice that violates a principle triggers a fix + re-plan; will not proceed to Phase 2 (`/speckit-tasks`) with a violation on the board.
