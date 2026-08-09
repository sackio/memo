# Feature Specification: Memo Renovation

**Feature Branch**: `001-memo-renovation`

**Created**: 2026-07-29

**Status**: Draft (interview-derived, awaiting operator review)

**Input**: 2026-07-29 seven-round operator interview + six background research
agents (pin/beacon audit, quantum-feed frustration mining, mind/dojo
frustration mining, memo-driven compaction, ecosystem survey, parking-miss
deep-dive, constitutional-practices audit). Interview notes at
`.specify/interview-notes.md` (55 spec constraints C1–C55).

## User Scenarios & Testing

### User Story 1 — Behavioral rules survive compaction and stay in-context (Priority: P1)

An agent running a long-lived campaign (quantum-navigator, mind
evergreen-loop, dojo autonomous loop) receives constitutional rules,
project-wide anti-pattern warnings, and current-focus goals in its context
window at every session start AND at every compaction re-warm — not just
once at spawn. When Ben's operator directives ("emphatic, repeated") land
mid-session, they are captured as `verbatim-critical` memos and forcibly
re-injected on the next compaction cycle. Agents no longer violate
behavioral memos that exist in the corpus, and Ben no longer has to
verbatim-copy-paste load-bearing corrections into rewarm prompts (as
happened with the 2026-07-16 FLAVOR-DUAL correction).

**Why this priority**: This is the load-bearing pain of the entire
renovation. The 14-day audit found 88 quantum-scoped behavioral memos and
**zero** `memo_search` calls surfacing them across all quantum-navigator
sessions. Post-compaction sessions with 11 and 24 compactions made zero
memo calls of any kind. Every other improvement is downstream of getting
this right.

**Independent Test**: Spawn a quantum-navigator session, trigger a
compaction, verify the post-compaction turn's context contains the seven
constitutional principles + the top-N per-family behavioral memos + the
current-focus goal set. Confirm a corpus anti-pattern named in the memo
gets obeyed on a subsequent action that would violate it (either by
inhibition or by mediator warning).

**Acceptance Scenarios**:

1. **Given** a live quantum-navigator session with `ae52afce` (anti-capture
   rule) in the constitutional set, **When** the session compacts and
   resumes, **Then** the anti-capture rule is present verbatim in the
   post-compact system context.
2. **Given** Ben posts an "emphatic, repeated" directive via Slack DM,
   **When** the auditor observes the directive, **Then** it is captured as
   a `verbatim-critical` memo with full-UUID discipline and pinned to the
   session's current-focus set within the settle window.
3. **Given** the FLAVOR-DUAL correction case, **When** a nav session
   respawns, **Then** the correction is present in-context by memo id
   reference (no verbatim copy-paste required in the rewarm prompt).

---

### User Story 2 — Both read and write go through mediators (Priority: P1)

Session agents call mediators, never the memo store directly, on either
the read or write path.

**Retrieval mediator** filters by session-relevance, reconciles
contradicting memos in real time, filters out `valid_until IS NOT NULL`
(superseded) records by default, dedupes migration-cluster duplicates,
and returns a concise ANSWER — potentially <10% of the raw memo bytes —
plus the source memo ids for verification. Every call is logged.

**Storage mediator** intercepts every `memo_store` and (a) reconciles the
incoming memo against the current corpus — merge into an existing memo
if it is the same fact, supersede if it is a refutation from an operator
directive, split if it is compound, reject if the write would refute a
`class = fact` memo without operator authority (per Principle II), (b)
may synchronously go back to the calling agent for a clarification when
the write is ambiguous (e.g. "is this new IP a supersession of the
existing one or a second interface?"), and (c) applies canonical tagging
+ class inference before persisting. Database curation happens on the
write path, not just on the weekly cron.

**Why this priority**: The 7/26 `/recall parking` failure demonstrated
the retrieval failure mode (duplicates + no reconciliation) and the 7/22
capture went to a state JSON without ever reaching memo — the direct
`memo_store` path made both errors invisible and irrecoverable. Mediated
read + mediated write together turn memo into a self-cleaning substrate
that agents can safely fire-and-forget into. This is the enabler for the
compaction / context-reduction goal (any story that says "flush to memo
and drop from transcript" depends on the store being clean).

**Independent Test**: Simulate the 7/26 parking recall with the retrieval
mediator in place (July BOS memo ranks above May SF, duplicates
collapsed, returned payload is a one-line answer + memo id). Separately,
attempt a `memo_store` for a fact that duplicates an existing memo AND a
fact that would refute a `class = fact` memo — verify the storage
mediator merges the first and rejects the second.

**Acceptance Scenarios**:

1. **Given** three duplicate memos of the same fact and one authoritative
   memo, **When** the retrieval mediator responds to a matching query,
   **Then** the duplicates are collapsed and only the authoritative memo
   is surfaced (plus its id).
2. **Given** memo A superseded by memo B (`A.valid_until = t; B`
   current), **When** the retrieval mediator responds to a query that
   would have matched both, **Then** only B is returned to the agent.
3. **Given** a session belongs to project P, **When** the retrieval
   mediator responds, **Then** cross-project memos are downweighted or
   filtered unless their `scope` explicitly includes P or `global`.
4. **Given** an agent attempts to `memo_store` a fact semantically
   identical to an existing memo, **When** the storage mediator runs,
   **Then** it merges the new content into the existing memo (updated
   `updated_at`, provenance appended) and returns the existing memo id
   to the caller — no duplicate is created.
5. **Given** an agent attempts to `memo_store` a fact that contradicts
   an existing `class = fact` memo without operator authority, **When**
   the storage mediator runs, **Then** the write is rejected with a
   clarification prompt: "this contradicts memo `<id>`; who is
   authorizing the refutation?" — the write only proceeds if the
   response references an operator directive.
6. **Given** an agent attempts to `memo_store` a memo missing a class or
   provenance, **When** the storage mediator runs, **Then** it either
   infers the missing fields from content + calling-context OR asks the
   agent to clarify before persisting.

---

### User Story 3 — Trip-scoped / time-scoped memos auto-pin when relevant (Priority: P1)

Operator-logistics memos (parking spots, PNRs, access codes, appointment
details, delivery windows) can carry a `time_scope` block (`{start, end}`
or `{trip_id, calendar_event_id, start, end}`) that automatically pins the
memo into the operator's relevant sessions during its active window and
depins it after. Anchored on Google Calendar event ids where applicable.
This solves the 7/22 Logan parking case: the datum is pinned at trip-start,
surfaces proactively on landing, and depins after trip-end.

**Why this priority**: Ben explicitly asked for this via the parking-miss
example and it is the concrete demonstration of the "short-term memory
that survives compaction" goal that maps most cleanly to the operator's
lived experience.

**Independent Test**: Create a memo with `time_scope: {calendar_event_id:
<real cal event>, start: T0, end: T1}` before T0. Verify it appears in
`assistant` session context between T0 and T1 without a `memo_search`
call. Verify it depins from that context after T1.

**Acceptance Scenarios**:

1. **Given** a memo with `time_scope: {start: 2026-07-22, end: 2026-07-25}`
   about Logan parking, **When** the assistant session is active on
   2026-07-22 to 2026-07-25, **Then** the memo is in the session's
   forcible-injection set.
2. **Given** the same memo, **When** the current time passes `end`,
   **Then** it depins automatically on the next session start.
3. **Given** a memo linked to a gcal event id, **When** the calendar event
   is edited or deleted, **Then** the memo's `time_scope` updates to
   match (via an ATC calendar-event listener).

---

### User Story 4 — Every memo links to its source material (Priority: P2)

Every new memo carries a structured `provenance` block referencing the
artifact that produced it: Claude Code jsonl path + line-range, git
commit SHA + file:line, Gmail msg-id, phony record-id, ATC event-id, URL,
or `derived_from: [memo_ids]`. The retrieval mediator and auditors can
walk provenance chains. Backfill will retro-populate provenance for
existing memos where discoverable; memos where provenance is unrecoverable
are tagged `legacy-unattributed`.

**Why this priority**: Ben explicitly ranked provenance as first-class
data structure in Q6a. Enables audit ("where did this claim come from?"),
reconciliation (auditor knows the source it's reconciling against), and
temporal reasoning ("was this before or after the switch?"). Not P1
because forcible injection (US1) and mediator (US2) unblock more pain
faster.

**Independent Test**: Create a memo from an assistant session with an
inbound Gmail msg. Verify the memo's `provenance.gmail_msg_id` matches
the source msg. Query `mediator.explain(memo_id)` returns the provenance
chain.

**Acceptance Scenarios**:

1. **Given** a memo derived from a Claude Code session turn, **When** it
   is stored, **Then** its provenance contains the session UUID + line
   range.
2. **Given** a memo derived from a Gmail msg, **When** it is stored,
   **Then** provenance contains the thread + message id.
3. **Given** a meta-rule memo built from two other memos, **When** it is
   stored, **Then** `derived_from` contains both parent memo ids and the
   parents' `derived_by` back-references point to the meta-rule.

---

### User Story 5 — Bi-temporal supersession preserves audit trail (Priority: P2)

When a new fact contradicts an older fact, the older fact's `valid_until`
is set to the transition moment and the new fact is stored with
`valid_from` = same moment. The read path (mediator) filters
`valid_until IS NULL` by default so agents never see refuted facts.
Auditors and explicit point-in-time queries (`memo.as_of(t)`) see the full
history. Delete + replace is prohibited for any memo class other than
`ephemeral-flush`.

**Why this priority**: Without this the auditor cannot reason about
change ("when did the K8s IP change? what changed?") and provenance
chains break at supersession. Ben approved it after the pushback exchange.
P2 because it does not unblock pain by itself — it unblocks the auditor's
correctness.

**Independent Test**: Create fact A, then supersede with fact B via the
supersede API. Verify a normal read returns B only. Verify
`memo.as_of(t_between_A_and_B)` returns A. Verify auditor can list both.

**Acceptance Scenarios**:

1. **Given** memo A about the K8s cluster IP, **When** memo B with the
   new IP is created via `supersede(A)`, **Then** A's `valid_until` = B's
   `valid_from` and both remain in the store.
2. **Given** the same state, **When** an agent queries via the mediator,
   **Then** only B is returned.
3. **Given** the same state, **When** the auditor queries with
   `include_history=true`, **Then** both A and B are returned with their
   temporal edges.

---

### User Story 6 — Auditor watches every session and can flush / compact / re-inject (Priority: P2)

Each running session has a shadow auditor (per-session) that observes
transcript growth, memo query patterns, frustration signals, and
behavioral-memo adherence. The auditor writes new memos (proposed
meta-rules, new observed anti-patterns), never modifies constitutional
memos (operator-only), can trigger compaction on its observed session
when bloat threshold is breached, and can re-inject verbatim-critical
memos mid-session via ATC beacon or targeted system-reminder DM. Global
auditors (fleet-wide, cron-driven) police the per-session auditors and
handle cross-session pattern synthesis.

**Why this priority**: Ben confirmed the two-tier auditor architecture
(Q5) and its full autonomy. The auditor is the mechanism that makes many
of the other user stories self-healing. P2 because US1/US2/US3 can start
without a full auditor if the constitutional layer and mediator are
seeded manually.

**Independent Test**: Spawn a session, feed it a transcript that violates
a `hard-rule` memo. Verify the shadow auditor detects the violation
within N turns, DMs the session with a system-reminder-shaped nudge, and
writes a "violation observed" memo tagged with the session UUID and the
violated memo id.

**Acceptance Scenarios**:

1. **Given** a session with a `hard-rule` memo present, **When** the
   agent takes an action that would violate the rule, **Then** the shadow
   auditor injects a mid-session reminder via ATC beacon.
2. **Given** a session's transcript exceeds the operator-configured bloat
   threshold, **When** the pane is idle-safe, **Then** the shadow auditor
   triggers `/compact` via the existing `compact-session --self` path.
3. **Given** Ben posts a frustration signal in a DM, **When** the auditor
   observes it, **Then** it either (a) injects a course-correction into
   the session or (b) DMs Ben proposing the correction if uncertain.

---

### User Story 7 — Corpus backfill: retrofit 7339 legacy memos into the new taxonomy (Priority: P3)

The existing corpus is systematically walked and each memo is (a)
classified into the new class taxonomy (constitutional / behavioral /
goal / verbatim-critical / fact / decision-in-progress / episodic /
ephemeral-flush / time-scoped), (b) retagged against the canonical
vocabulary (retire `hard-rule`/`ben-hard-rule`/`behavioral-rule`
fragmentation to a single tag per class), (c) provenance-linked where
discoverable, (d) split into multiple memos where a single memo carries
compound content, (e) merged where duplicates exist (Matt-Sack cluster
`0c55a9a3/c664f4a1/98efbda5` collapses to one canonical), (f) marked
`legacy-unattributed` where provenance is unrecoverable, (g) redirected
where a memo id is superseded by a canonical memo. Migration is
non-destructive: originals are archived to a `pre-renovation-2026-07-29`
snapshot and can be flipped back to.

**Why this priority**: Ben confirmed migration is a "big part of the
project" but comes AFTER the new taxonomy + primitives are proven in v2.
P3 because it can be phased over weeks/months while v2 is live; the
system works with a mixed corpus (new memos in new schema, legacy in
`legacy-unattributed` bucket handled by the mediator).

**Independent Test**: Pick 50 memos from the current corpus, run them
through the backfill classifier, verify every one is either classified
into a class or marked `legacy-unattributed`, no memo is dropped
silently, and the mediator returns correct results for a canonical set
of test queries against the migrated set.

**Acceptance Scenarios**:

1. **Given** the current 7339 memos, **When** backfill completes, **Then**
   every memo has a class assignment and canonical tags OR is in the
   `legacy-unattributed` bucket, with the operator-visible reason.
2. **Given** the Matt-Sack duplicate cluster, **When** backfill completes,
   **Then** the cluster collapses to one canonical memo and the other
   ids resolve as redirects.
3. **Given** the mind `5d2cd356` "quarterly cap" decision memo, **When**
   backfill completes, **Then** it is classified as
   `decision-in-progress` with a `reopenability` flag set based on
   auditor review of subsequent events.

---

### User Story 8 — v2 built in a separate worktree; SOAK-TEST FIRST, then MCP-flip when confident (Priority: P3)

Memo v2 (implementing the above stories) is built in a separate git
worktree with an entirely separate MCP server binding and a separate
sqlite dataset. **Before any cutover consideration**, the full v1
corpus (7339 memos) is ported into v2 via the migration script, and
v2 is soak-tested by background test agents that exercise the mediator,
auditor, injection hooks, reconciliation, and recall-corrections loop
against the ported corpus. Only once the operator (Ben) reviews soak-
test results and gives explicit confidence approval does the cutover
proceed. The cutover shape (big-bang / waves / session-by-session) is
decided at the confidence gate, not committed in this spec. Rollback
throughout: v1 remains untouched; the MCP-flip is the only production
change; flip back = same operation reversed.

**Why this priority**: Ben's operational safety requirement — plus the
explicit sequencing rule that we don't pick a cutover strategy against
an unbuilt system. P3 because it is the *shape* of the delivery, not a
user-visible feature by itself — but the shape gates everything else.
Soak-test phase gates the cutover phase; cutover shape decision comes
last, at the confidence gate.

**Independent Test**: Bring up v2 on a scratch port with a scratch DB,
port the v1 corpus into v2, run the soak-test workload (kick-tires
agents), review the soak-test report, decide whether to cut over.
Independently: flip a single non-production session to the v2 MCP
config, verify all memo operations work end-to-end, flip back to v1,
verify the session resumes on v1 with no corruption.

**Acceptance Scenarios**:

1. **Given** v2 running on a non-conflicting port with its own DB,
   **When** a session is flipped to v2 MCP, **Then** all memo tools
   (store, get, list, search, context, recall) work identically to v1
   plus the new mediator surface.
2. **Given** the same session, **When** it is flipped back to v1,
   **Then** it operates against v1's DB with no cross-contamination.
3. **Given** v2 with the full v1 corpus ported, **When** the soak-test
   workload runs, **Then** all mediator queries return correct results
   against a canonical test-query set, all auditor hooks fire, all
   injection paths deliver the expected InjectionSet, and no data-loss
   or crash events are logged.
4. **Given** a passing soak-test report, **When** the operator gives
   explicit confidence approval, **Then** the cutover strategy (shape
   TBD at approval time) proceeds without lost writes (verified
   against a pre-flip corpus snapshot).

---

### Edge Cases

- **INDEX LAG** — a memo banked <1 min ago is not `memo_search`-findable
  yet (sqlite-vec indexing latency). Post-write reads MUST use
  `memo_get(full_uuid)` + a settle window. Prefix-uuid lookups return
  null by design (documented in `agents` supervisor DM 2026-07-29).
- **Auditor false-positive on frustration signals** — Ben's phrasing
  ("recall", "emphatic") can appear in benign contexts. Mid-session
  injection must have a high-confidence bar or escalate to global auditor
  before firing.
- **Session-scoped memos going undiscoverable post-respawn** — a fresh
  agent won't know to search for its predecessor's tag. Every
  session-scoped memo MUST be paired with either a beacon pointer or a
  guide-queried deterministic tag (never rely on agent initiative).
- **Ephemeral-flush TTL** — session flush memos must auto-reap on their
  TTL, not wait for the weekly memo-minder sweep.
- **Compaction interrupting the auditor's flush** — pre-compact flush
  must complete before compaction fires (synchronous), or the flush's
  own memos are lost.
- **Bi-temporal on a facts-with-many-updates memo** — a K8s cluster IP
  that changes weekly should not accrue 52 valid_until edges per year;
  reconciler should coalesce a chain into
  `{current, previous, other-superseded (link)}` for performance.
- **Global auditor down** — per-session auditors that escalate to a
  down-global must not block; they log the escalation and proceed with
  their default action.
- **Mediator cache poisoning** — if the mediator caches recent answers,
  a supersession event MUST invalidate every cached answer that touched
  the superseded memo.
- **v2 rollback with partial v2 writes** — memos written to v2 during
  the cutover window MUST be reconcilable back to v1 on rollback (either
  replicated forward on cutover or migrated on rollback).

## Requirements

### Functional Requirements — storage + taxonomy

- **FR-001**: memo MUST support a `class` field on every memo, with values
  drawn from: `constitutional`, `behavioral`, `goal`, `verbatim-critical`,
  `fact`, `decision-in-progress`, `episodic`, `ephemeral-flush`,
  `time-scoped`, `legacy-unattributed`.
- **FR-002** *(withdrawn 2026-07-30, **REINSTATED 2026-07-31** by operator
  directive)*: Bi-temporal versioning **stays**. `valid_from` / `valid_until`,
  `get_as_of`, `GET /documents/{id}/as-of` and supersede-chain resolution are all
  retained, live and tested.

  **History, kept deliberately.** On 2026-07-30 this requirement was withdrawn:
  the argument was that under the amended Principle II superseded *state* is
  deleted rather than retained, so an as-of query would have nothing to find, and
  point-in-time reconstruction should come from backups instead. **The withdrawal
  was never executed** — no task carried it out, the surface stayed live across 23
  files, and because the code still carried `001/FR-002` anchors the trace gate
  rated a withdrawn requirement **FULL**. The 2026-07-30 audit caught that and
  raised **T033a** to force the choice.

  **The choice went the other way (2026-07-31).** Asked to either execute the
  withdrawal or amend the spec, the operator kept as-of. So the spec is corrected
  to match the code rather than the code destroyed to match the spec — which also
  means nothing has to be deleted, and the 23 files stand as they are.

  Two things this leaves true and worth stating, since the withdrawal rationale
  was not wrong, merely outweighed:
  - Superseded state under Principle II is deleted rather than retained, so an
    as-of query answers from what still exists — it is not a general time machine.
  - **FR-028a**'s deletion log with content snapshots remains the recovery path
    for anything actually removed. The two are complementary, not redundant.

- **FR-003** *(amended 2026-07-30)*: `POST /supersede` survives as the
  **replace-and-record** primitive: write the new memo, delete the old, and log
  the old one's content to the deletion log in one transaction. It no longer
  maintains a version chain — `supersede_edges` is reduced to that audit record.

- **FR-004**: memo MUST store a structured `provenance` block per memo
  supporting the fields: `claude_log_ref`, `git_ref`, `gmail_msg_id`,
  `phony_ref`, `atc_ref`, `url`, `derived_from`.
- **FR-005**: memo MUST support a `time_scope` block on memos with values
  `{start, end}` and optional `calendar_event_id`, `trip_id`.
- **FR-006**: memo MUST support an `injection_mode` field per memo:
  `forcible-constitutional`, `forcible-current-focus`, `on-recall`,
  `on-procedure-match`.
- **FR-007**: memo MUST support an `expires_at` field (TTL) for classes
  where auto-reap is required (primarily `ephemeral-flush`).
- **FR-008**: memo MUST support a `scope` field: `global`, `project:<slug>`,
  `session:<subscriber-id>`, `agent-family:<name>`, or list-combinations.
- **FR-009**: memo MUST support `reopenability` metadata on
  `decision-in-progress` memos: `challenge_if_delays_build_by_days`,
  `challenge_if_operator_tempo_shifts`, freetext operator-hints.

### Functional Requirements — retrieval mediator

- **FR-010**: memo MUST expose a `POST /recall` endpoint (also as an MCP
  tool) that implements the retrieval mediator: takes a query + calling
  session context, returns a filtered/reconciled ANSWER + citation memo
  ids.
- **FR-011**: The retrieval mediator MUST filter `valid_until IS NULL`
  by default; point-in-time queries require explicit `as_of=<timestamp>`.
- **FR-012**: The retrieval mediator MUST dedupe migration-duplicate
  clusters (identical embedding + near-identical content) to a canonical
  single result.
- **FR-013**: The retrieval mediator MUST apply recency + tag-class
  boost for operator-logistics tag families (`logistics`, `parking`,
  `access-code`, `booking`, `appointment`, `receipt`, `travel`).
  Recommended formula: `score = semantic × 0.5 +
  recency_decay(created_at) × 0.3 + tag_class_match × 0.2` — tuneable.
- **FR-014**: The retrieval mediator MUST log every call: `{query,
  filters, results, latency_ms, calling_session_id, calling_role,
  timestamp}`, retention ≥30 days for auditor use.
- **FR-015**: The retrieval mediator MUST return anomalies (contradicting
  memos, stale memos, gaps in expected coverage) to the auditor via ATC
  event.

### Functional Requirements — storage mediator

- **FR-015a**: memo MUST expose a `POST /store` endpoint (also as an MCP
  tool `memo_store` — the existing name preserved for backward
  compatibility) that routes every write through the storage mediator.
- **FR-015b**: The storage mediator MUST run a reconcile pass against
  the current corpus before persisting: semantic-similarity search + tag
  overlap + entity match. On match, the mediator chooses one of `merge`,
  `supersede`, `split`, `reject`, `write-new` and returns the chosen
  action + resulting memo id(s) to the caller.
- **FR-015c**: **WITHDRAWN** 2026-07-30, amended under Principle II. The
  operator-directive requirement for refuting a `class = fact` memo, and its
  409-then-403 protocol, are removed. Superseding a wrong fact is ordinary
  corpus maintenance, not a privileged act. Replaced by FR-028's
  supersede-never-delete rule. The `operator_directive_ref` field remains
  OPTIONAL on `supersede_edges` for the cases where an operator *did* direct
  the change and it is worth recording — it is no longer a gate
  section of contracts/mediator-store.md — that contract previously specified
  403 for this same case, and the two are reconciled as 409-then-403 rather
  than either alone.
- **FR-015d**: The storage mediator MUST synchronously issue a
  clarification prompt to the calling agent when the incoming memo is
  ambiguous (missing class, missing provenance, appears to be compound,
  contradicts multiple existing memos). The caller may respond
  in-session; the mediator either proceeds with the clarified write or
  rejects.
- **FR-015e**: The storage mediator MUST apply canonical tagging + class
  inference before persistence — if the caller omits a class, the
  mediator infers from content + calling role; if a canonical-vocabulary
  violation is present (e.g. `hard-rule` when the canonical is
  `constitutional`), it rewrites the tag.
- **FR-015f**: The storage mediator MUST log every call: `{incoming
  memo, chosen action, resulting memo id(s), clarification-round-trips,
  reconcile-hit ids, latency_ms, calling_session_id, calling_role,
  timestamp}`, retention ≥30 days for auditor use.
- **FR-015g**: The direct raw-store path (`POST /documents` in v1
  terms) MUST remain available for auditors, migrations, and admin
  tooling, but MUST require an explicit `bypass_mediator=true` flag +
  operator authentication. Default calls route through the mediator.

### Functional Requirements — forcible injection

- **FR-016**: memo MUST expose a `GET /injection-set` endpoint that
  returns the current set of memos to be forcibly injected for a given
  `{session_id, agent_family, project, current_time}`.
- **FR-017**: The Claude Code SessionStart hook (`atc-session-start.sh`)
  MUST call `/injection-set` and include the returned memos in the fresh
  session's context.
- **FR-018**: The Claude Code SessionStart:compact hook MUST call
  `/injection-set` and include the returned memos in the post-compact
  session's context.
- **FR-019**: The `injection_mode = forcible-constitutional` set MUST
  include: all `class = constitutional` memos + all `class = behavioral`
  memos with `scope` matching the session's agent-family + all
  `verbatim-critical` memos with matching `scope`.
- **FR-020**: The `injection_mode = forcible-current-focus` set MUST
  include: all `class = goal` memos with matching `scope` + all
  `time-scoped` memos whose current time is inside `{start, end}` + all
  `verbatim-critical` memos posted within the session's rolling window.

### Functional Requirements — auditor

- **FR-021**: Each running Claude Code session MUST have a shadow
  auditor process that observes the session (transcript growth, tool
  calls, incoming DMs, memo query patterns).
- **FR-022**: The shadow auditor MUST have autonomy to WRITE new memos,
  MODIFY non-constitutional memos, trigger `/compact` on its observed
  session (idle-gated), and re-inject `verbatim-critical` memos via ATC
  beacon or targeted system-reminder DM.
- **FR-023**: The shadow auditor MUST NOT modify constitutional memos —
  it may only write `constitution-proposal` memos tagged
  `proposal-pending` for operator review.
- **FR-024**: A global auditor MUST run on a scheduled cadence (initial:
  daily via cron, tunable) and (a) police the shadow auditors, (b)
  synthesize cross-session patterns, (c) reap `ephemeral-flush` memos
  past TTL, (d) reconcile long supersession chains for storage
  efficiency.
- **FR-025**: The auditor MUST log every write, modify, injection, and
  compaction trigger with rationale for after-the-fact operator review.
- **FR-026**: The operator MUST be able to override any auditor decision
  via explicit DM (e.g. "auditor, undo that reinjection"), with the
  override captured as a `decision-in-progress` memo for future auditor
  calibration.

### Functional Requirements — reconciliation

- **FR-027**: An agent MAY create new facts via `memo_store`.
- **FR-028** *(amended 2026-07-30)*: An agent MAY supersede AND MAY DELETE.
  Deletion is restricted by WHAT qualifies, not by who asks:
  **deletable** — byte-identical duplicates (keep one), superseded versions past
  retention, TTL-expired and `ephemeral-flush`, empty stubs;
  **supersede-then-delete** — superseded operational STATE (the old router, the
  previous IP, last month's config). Nobody queries what router we had in 2025,
  and keeping it competes for retrieval against the current fact. A "we used to
  have X" rewrite keeps the cost and adds none of the value;
  **keep** — the REASONING behind a change (decisions, postmortems, measured
  findings). Those are not superseded by the state changing; they explain it.
  The line is: facts about what IS get replaced, findings about what HAPPENED
  get kept. Split a memo containing both;
  **operator-only** — `class = constitutional`.
  Content changes MUST route through supersede rather than in-place mutation, so
  the prior version stays answerable via `get_as_of`. Every supersede edge MUST
  record `actor` and `reason`.
- **FR-028a** *(added 2026-07-30)*: Every deletion MUST be logged with a content
  snapshot sufficient to reconstruct the memo. That log — not a prohibition — is
  what makes aggressive pruning safe, and it is what lets a wrong call be undone
  rather than merely regretted.
- **FR-029**: **WITHDRAWN** 2026-07-30, amended. Refutation no longer
  requires an operator directive. The measured failure mode is staleness (44
  duplicate groups / 150 excess copies, 2026-07-30), which an operator gate
  makes worse by making the operator the bottleneck on every correction.
- **FR-030** *(amended 2026-07-30)*: Real-time reconciliation MUST fire on the
  write path for `class = fact` memos: a new fact that contradicts a current
  one MUST be reconciled — superseded, merged, or both retained with the
  conflict recorded — rather than written silently alongside it. It is NO
  LONGER queued for operator review; the reconciling agent decides and the
  audit log records what it decided.
- **FR-031**: Event-triggered reconciliation MUST fire on ATC
  infra-change events (existing L3a listener extended) for
  infrastructure-tagged memos.
- **FR-032**: Cron-based reconciliation (existing memo-minder) MUST
  continue for daily corpus sweep, dedup, and legacy backfill.

### Functional Requirements — new tooling

- **FR-033**: memo MUST expose an intelligent Claude Code log query tool
  (grep-based; embedding-optional). Given `(host, project, session_uuid,
  optional query)`, returns matched line-ranges with structured metadata
  for provenance linking.
- **FR-034**: **WITHDRAWN** 2026-07-30, operator directive. Was: memo MUST expose a
  `POST /flush` endpoint upserting a slot-set of ephemeral-flush memos keyed on
  `(session_id, flush_generation)`. Session working state belongs to ATC, and
  memo's copy was a *redundant store* rather than redundant delivery — two
  mechanisms holding the same state can silently diverge with nothing
  reconciling them. Deleting it did not create a gap; it revealed one that ATC
  already had, which is the point. Module, tests, endpoint and the pre-compact
  path removed in `707e714`; T061/T069 are withdrawn with it.
- **FR-035**: memo MUST expose `GET /answer-loop-audit` returning the
  mediator's query→answer→user-next-turn log for auditor consumption.

### Functional Requirements — compaction integration

- **FR-036**: The `~/.claude/hooks/atc-precompact.sh` NO-OP MUST be
  repurposed to invoke `POST /flush` synchronously before compaction
  proceeds, so ephemeral flush completes before context is dropped.
- **FR-037**: The auditor MAY trigger `/compact` on its observed session
  via `compact-session --self` when a per-session token/turn threshold
  is breached and the session is idle.

### Functional Requirements — migration

- **FR-038**: memo v2 MUST be deployable in a separate git worktree with
  a distinct MCP port, distinct data directory, and no shared state
  with v1.
- **FR-039**: A `memo-migrate-backfill` script MUST walk every v1 memo
  and (classify, retag, provenance-link, split, merge, redirect) it into
  v2 with a full audit log.
- **FR-040**: Migration MUST be reversible: v1 remains untouched during
  cutover; the MCP-flip is the only production change.

### Functional Requirements — integration interfaces (Principle VIII)

- **FR-041**: **Conductor push interface** — memo MUST be able to emit
  events to the Conductor when specific triggers fire: memo store,
  memo supersession, mediator anomaly detection, auditor recommendation,
  injection-set change, time-scope enter/exit. The event schema is
  defined by memo; the transport is provider-supplied. Default provider
  = ATC (`atc_post`, `atc_send`, `atc_post_beacon`).
- **FR-042**: **Conductor pull interface** — memo MUST accept out-of-
  band events from the Conductor via a well-defined event schema. These
  include: operator directive DMs (routed to auditor), ATC beacon acks,
  calendar-event triggers (routed to time-scope handler), infra-change
  events (routed to reconciliation), **generic bridge-deliveries**
  (Slack, phony/Twilio SMS/voice, Gmail today; any additional bridges
  the Conductor adds later that speak the standard event schema —
  memo MUST NOT hard-code the enumerated bridge set), and time-based
  watchdog triggers (fire-every-N or fire-on-event scheduling).
  Default provider = ATC.
- **FR-042a**: **Scheduled + event-triggered fires via the Conductor** —
  memo MAY register named triggers with the Conductor (e.g. "reap
  ephemeral-flush memos every 60 min", "fire reconcile when infra-change
  event lands"), and the Conductor delivers the fire back to memo via
  the pull interface. Memo does not run its own scheduler for
  Conductor-eligible triggers.
- **FR-043**: **AgentController interface** — memo MUST be able
  to request agent-level operations from the AgentController via
  a well-defined interface: spawn new agent, respawn existing, start-
  fresh (drop transcript), clear session, change model, trigger
  compact, interrupt in-flight turn, force-inject a prompt. The provider
  owns tmux-level control and knows how to safely execute each op;
  memo only issues the request with parameters. Default provider =
  `agents` supervisor session.
- **FR-044**: **Claude Code hook interface** — memo MUST expose HTTP
  endpoints (or MCP tools) that the Claude Code hook chain
  (SessionStart, SessionStart:compact, PreCompact, SessionStop,
  SessionEnd) can invoke, with a documented contract for each hook's
  expected inputs, outputs, and side effects. Hook wiring lives in
  `~/.claude/settings.json`, not in memo's release artifact.
- **FR-045**: **Standalone operation** — memo MUST function correctly
  (all CRUD, both mediators, auditor logging, the direct MCP tool
  surface) when NO Conductor and NO AgentController is
  configured. Integration features (mid-session injection, external
  event fanout, auditor-triggered compaction/respawn, time-based reap)
  MUST degrade gracefully with a WARN log describing what would have
  fired.
- **FR-046**: **Provider-versioning independence** — memo MUST NOT
  break when the Conductor or AgentController updates its own
  schema/version, PROVIDED the provider maintains its side of the
  interface contract. Breaking provider changes require an explicit
  memo-side adapter update, not a memo-release rebuild.

### Key Entities

- **Memo** — the unit of stored knowledge. Fields: `id (uuid)`, `content`,
  `title`, `tags`, `class`, `injection_mode`, `scope`, `time_scope?`,
  `reopenability?`, `provenance`, `valid_from`, `valid_until?`,
  `expires_at?`, `created_at`, `updated_at`, `derived_from`,
  `derived_by`. Immutable id; append-only + temporal supersession.
- **Provenance** — structured block per memo referencing the source
  artifact(s) that produced it. Nested fields per source-type.
- **RetrievalMediator** — a service (skill or subagent) that sits between
  session agents and the memo store on the recall path. Owns filtering,
  reconciliation, deduping, ranking, answer-shaping, and observability.
- **StorageMediator** — a service (skill or subagent) that sits between
  session agents and the memo store on the write path. Owns
  reconcile-before-write (merge / supersede / split / reject / write-new),
  canonical-tagging + class inference, clarification round-trips with
  the calling agent, and observability.
- **Conductor** — the real-time coordination + messaging layer memo
  composes with. Handles: (a) push/pull messaging between memo, sessions,
  agents, and the operator; (b) an **extensible bridges concept** —
  the current bridge set (Slack, phony/Twilio for SMS + voice +
  voicemail, Gmail) is the starting point, not the ceiling; the
  Conductor may add additional bridges over its own release cycle,
  and memo consumes each via the same generic bridge-event interface
  (no per-bridge memo change required for new bridges that speak the
  standard event schema); (c) board/zone posting with TTL/expiry;
  (d) time-based scheduled triggers (fire-every-N-minutes) and event-
  driven watchdog triggers (fire-on-event). ATC is the default
  Conductor today; memo treats it as a pluggable provider. Memo
  defines the interface; the Conductor supplies the transport,
  bridges, and scheduling.
- **AgentController** — an external orchestrator that automates
  operator-level control of Claude Code agents + the tmux sessions they
  run within. Capabilities include anything the Claude Code CLI or SDK
  can do: spawn new, respawn existing, start-fresh (drop transcript),
  clear, change model, trigger compact, interrupt an in-flight turn,
  force-inject a prompt. `agents` supervisor is the default provider
  today; memo treats it as pluggable. Memo defines the interface; the
  provider owns tmux-level execution.
- **ShadowAuditor** — per-session background process that observes
  the session and can act on it (write memos, trigger compaction,
  re-inject).
- **GlobalAuditor** — fleet-wide scheduled auditor that polices shadow
  auditors, synthesizes cross-session patterns, and reconciles the
  corpus.
- **InjectionSet** — the set of memos returned to a session for forcible
  in-context injection at session start / post-compaction, resolved
  from `{class, injection_mode, scope, time_scope}`.
- **BackfillJob** — the workstream that retrofits the 7339 v1 memos
  into v2. Comprises classifier, retagger, deduper, splitter, merger,
  redirector, audit log.

## Success Criteria

### Measurable Outcomes

- **SC-001**: In a 14-day post-cutover window across the quantum fleet,
  the count of Ben's "emphatic, repeated" frustration signals drops by
  ≥60% vs. the 2026-07-15→2026-07-29 baseline (nine documented events).
- **SC-002**: In the same window, the count of behavioral-memo violations
  (agent takes an action a forcible-injection memo named) drops to zero
  measurable events, or is caught mid-session by the auditor in ≥90%
  of cases when it does occur.
- **SC-003**: Zero verbatim copy-paste of load-bearing operator
  corrections into rewarm prompts. All such corrections live as
  `verbatim-critical` memos referenced by id.
- **SC-004**: For operator-logistics recalls (parking, PNRs, access
  codes, booking, appointments), the correct memo ranks #1 in mediator
  results ≥95% of the time when it exists in the corpus, and the
  mediator returns "not found" (not a wrong-answer) ≥95% of the time
  when it doesn't.
- **SC-005**: Migration-duplicate clusters in the corpus drop to zero
  after backfill (audited via `memo-migrate-verify`).
- **SC-006**: Median mediator round-trip latency ≤ 500 ms at the current
  corpus size (7,339 memos), P95 ≤ 1.5 s.
- **SC-007**: For the always-on loop-agent fleet (minders, heartbeats,
  watchers), per-turn cache-read replay drops by ≥30% via auditor-
  triggered compaction + memo flush-and-forget.
- **SC-008**: The 26 orphan constitution files (worktree duplicates)
  collapse to a single-source model — no drift between them at 30 days
  post-cutover.
- **SC-009**: Backfill classifies ≥95% of v1 memos into a real class
  (≤5% land in `legacy-unattributed`).
  **AMENDED 2026-07-30 (operator decision)**: measured against the amended
  C-07 (see data-model.md). An unattributed fact now stays a `fact` tagged
  `provenance-pending`, so `legacy-unattributed` means "no usable signal"
  rather than "unattributed". Measured on the real corpus: **0.0%**
  legacy-unattributed (was 86.8% under the original rule).
  **Provenance coverage (6.4%) is now a tracked HEALTH metric, explicitly
  NOT a gate** — it should rise over time as memos are re-attributed, and
  gating on it would block a migration on record-keeping debt that
  predates the requirement.
- **SC-010**: Operator (Ben) can flip a single session's MCP between v1
  and v2 in under 60 seconds, with no data loss on rollback.

## Assumptions

- The existing MCP infrastructure (`~/.claude.json` per-session and
  per-project blocks) remains the delivery channel — no protocol change.
- ATC (server4:3030) remains available as the coordination bus for
  beacons, DMs, and auditor↔session communication.
- Claude Code's SessionStart and SessionStart:compact hooks continue to
  fire deterministically and support `additionalContext` on the
  SessionStart path (`atc-precompact-beacon.py` already relies on
  this).
- The current sqlite + sqlite-vec substrate scales to 3–4× the current
  corpus size without a substrate change. If not, a Postgres migration
  is a separate ticket, out of this spec's scope.
- OpenRouter / `openai/text-embedding-3-small` remains the embedding
  provider for RAG (already deployed).
- Operator (Ben) is available for constitutional-proposal review at a
  weekly cadence minimum during the cutover phase; auditor writes queue
  until reviewed.
- The 7,339-memo v1 corpus does not grow >30% before v2 cutover; if it
  does, backfill sizing is re-estimated.
- The `agents` supervisor session remains available as the fleet-wide
  compaction coordinator + agent-lifecycle manager; per-session
  auditors compose with (not replace) it. However, per Principle VIII,
  memo MUST NOT depend on `agents` as a required component — it is the
  default AgentController but is pluggable.
- ATC remains available as the default Conductor; per Principle VIII,
  memo MUST NOT depend on ATC as a required component.
- ATC is being independently revised on its own cycle (in-flight as of
  2026-07-29). Memo's interfaces to the Conductor must remain stable
  across ATC's own version transitions, PROVIDED ATC (as the current
  Conductor implementation) maintains its side of the contract.
- Backups (`/mnt/backup/memo/`) are kept forever for both v1 corpus and
  v2 corpus, both source-material archives and memo snapshots — this
  is the forever-cold-storage safety net.

## Clarifications

Answers to open questions from the operator, walked interactively via
Slack DM (2026-07-29). Answered clarifications are struck through in
the Open Questions section below and their resolutions land here.

- **C-03** (2026-07-29 14:58 EDT) — **Auditor implementation is a
  HYBRID**: a lean long-running background process subscribed to the
  Conductor (watches ATC events for its assigned scope) + hook-based
  triggers on Claude Code SessionStart / SessionStart:compact /
  PreCompact / SessionStop / SessionEnd. Both mechanisms coexist. Bias
  toward creating both up front and tuning nosiness/intrusiveness
  downward rather than starting minimal and expanding. Keep per-agent-
  family shadow auditors **lean** (small model, narrow prompt, event-
  driven not poll-driven).

- **C-09** (2026-07-29 15:03 EDT) — **Retrieval mediator is HYBRID
  (algorithm-primary + LLM fallback)**: option (c) — in-process filter
  chain in the memo server (dedup + bi-temporal filter + recency-boost
  + tag-class-boost + scope filter + answer-shaping) handles the hot
  path with a smart deterministic algorithm; LLM call is invoked
  selectively when (a) the filter returns a large number of candidate
  memos and needs to synthesize, or (b) the returned candidates conflict
  and reconciliation requires judgment. Same hybrid pattern applies to
  the storage mediator. Framing: this is a working module refined over
  time; component boundaries designed for tunability.

- **C-04** (2026-07-29 15:05 EDT) — **Reopenability schema kept
  DELIBERATELY SIMPLE**: two fields on `decision-in-progress` memos —
  a structured `challenge_if_delays_build_by_days: <int>` trigger plus
  an optional freetext `operator_tempo_hint: <string>`. No temperature
  knob, no elaborate trigger struct. The `decision-in-progress` class
  is expected to be a **small edge-case tier, not first-class**, because
  memo's core purpose per the operator is to store **actual guiding
  principles, truths, and ground truth** — things not yet firm truth
  mostly should not be in memo in the first place; they live in specs,
  in-flight code, and session context. Auditor uses the trigger field
  deterministically; falls back to LLM-reading the freetext hint only
  when the trigger doesn't fit. Don't over-engineer this section.

- **C-02** (2026-07-29 15:11 EDT — default accepted) — **Injection-set
  token budget = 5k tokens soft ceiling per session, tunable per-agent-
  family.** Tunable at runtime, not baked in.

- **C-06** (2026-07-29 15:11 EDT — default accepted) — **Duplicate
  detection = cosine ≥0.90 + title 4-gram overlap ≥60% + LLM escape on
  borderline candidates.** Tunable.

- **C-07** (2026-07-29 15:11 EDT — default accepted) — **Legacy-orphan
  provenance = mark `legacy-unattributed`, do NOT LLM-synthesize a
  best-guess provenance.** Preserves the audit-trail invariant that
  provenance means something real. Human-review path only.

- **C-10** (2026-07-29 15:11 EDT — TIGHTENED from proposed default) —
  **Auditor-triggered compaction composite threshold, tightened**:
  fires when *any* of {transcript > **2.5 MB**, cache-read > **20 M
  tokens/day**, > **120 turns** since last compact}. Down from
  originally-proposed {4 MB / 30 M / 200} to catch bloat earlier. Per-
  agent-class tunable; start aggressive and back off if false-positive
  compactions become disruptive. All parameter defaults across C-02/06/
  07/10 must remain runtime-tunable, not baked into code.

- **C-01** (2026-07-29 15:23 EDT) — **Constitutional composition is a
  THREE-LAYER STACK; memo owns only Layer 2 (gap-fill), never Layer
  0-1**:

  **Layer 0 — Claude Code native (auto-loaded every session, PRIMARY,
  memo-untouchable)**: user global `~/.claude/CLAUDE.md` + project
  `./CLAUDE.md` (walked up dir tree) + `.claude/rules/*.md` +
  `memory/MEMORY.md` (unless `--no-memory`).

  **Layer 1 — `c` wrapper adds via `--append-system-prompt-file`
  (PRIMARY, agents-roster-owned, memo-untouchable)**: per-session
  GUIDE file — always-on every turn, survives compaction, resolved
  once at exec. Path lookup goes through the `agents`-roster
  `SESSION_GUIDE` table, NOT string-munged from name. Guide-path
  conventions vary across 4 shapes (`.claude/guides/<name>.md`,
  `AGENT_GUIDE.md`, `docs/SESSION_HANDOFF.md`, `.claude/skills/<name>/
  SKILL.md`) — memo's resolver must handle all four extensibly.

  **Layer 2 — MEMO GAP-FILL (the entire memo constitutional role)**:
    a) **Inject `.specify/memory/constitution.md`** at SessionStart /
       PostCompact via hook — Claude Code does NOT auto-load spec-kit
       constitutions (Agent G confirmed), memo fills the gap.
    b) **Resolve `memo:<uuid>` transclusion references** — memo scans
       auto-loaded CLAUDE.md + guide + rules for memo references, auto-
       retrieves + inlines resolved content as `additionalContext`.
       Turns on-disk files into addressable pointer files. Eliminates
       redundant duplication of the same rule text across 6+ project
       CLAUDE.md files (Agent F finding).
    c) **Store canonical-pointer memos** ("the auth rule lives at
       `~/.claude/guides/nav.md:42-56`") so agents can locate on-disk
       constitutional artifacts.
    d) **Own the fleet-wide behavioral memo class** — rules that apply
       across projects + agent-families without a natural CLAUDE.md /
       guide home.

  **Composition per agent family**: fleet-baseline (Layer 0
  `~/.claude/CLAUDE.md`) + per-agent-family (Layer 1 guide via
  SESSION_GUIDE) + per-project (Layer 0 project CLAUDE.md) + memo-gap-
  fill (Layer 2). This is basically the fleet's current shape today,
  with Layer 2 newly formalized.

  **Ownership**: operator curates Layer 0 and Layer 1; auditor proposes
  Layer 2 additions/changes; operator ratifies (Principle V). Auditor
  never writes to Layer 0/1 files.

  **Liveness monitoring**: memo's auditor mirrors the exemplar
  `stale-guide-detector` pattern for Layer 2 — a **content-based**
  check ("is the injected constitutional-memo set the CURRENT one for
  this session's scope?"), NOT timestamp-based. The discriminator is
  addressed-to-running vs. addressed-to-successor, per the agents-
  supervisor's `stale-guide-detector` design.

  **Ad-hoc `c --name X --guide Y`** launches carry guides not in the
  roster; cmdline is ground truth for those (what stale-guide-detector
  reads). Memo's Layer-2 hook must ingest the actual `--guide` cmdline
  path, not assume roster lookup.

  **Opt-out**: if a session sets `MEMO_DISABLE_INJECTION=1` (mirroring
  Claude Code's `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`), Layer 2 gap-fill
  does not fire. Layer 0/1 remain unaffected.

  **Per-session MEMORY.md posture detection**: memo MUST detect
  whether the running session has `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
  (via roster lookup or `/proc/<pid>/environ`) and adapt Layer 2:
    - **memory-on session** (default): memo AUGMENTS native MEMORY.md
    - **memory-off session** (quantum-* + guardians opt in via
      roster-configured `--no-memory`): memo's Layer 2 is the ONLY
      memory layer the session receives — role expanded, injection
      set larger, monitoring more strict.
  Failure to detect breaks the quantum guardians (they'd get zero
  memory context).

- **C-05** (2026-07-29 15:28 EDT) — **Answer-loop learning is HYBRID
  (immediate finding + threshold-gated promotion)**: when an operator
  corrects a bad mediator response, the correction is written
  IMMEDIATELY as an `answer-loop-finding` memo (mediator observability
  log, per FR-014) and a fresh finding surfaces for the auditor. The
  ranking-hint update itself is NOT applied immediately; it requires
  either (i) the auditor approves it within the current cycle
  (per-session shadow auditor may auto-approve for high-confidence
  cases), or (ii) N corroborating corrections in the same tag family
  within a short window (default: 3 corrections in 24h auto-promote
  the hint). Prevents single-shot thrashing while respecting
  operator-observable urgency ("Ben corrected the same class of thing
  3 times today — obviously update"). Tuneable thresholds.

- **C-08** (2026-07-29 15:33 EDT) — **Cutover strategy DEFERRED;
  SOAK-TEST FIRST is the load-bearing gate.** The renovation is
  sequenced:
    1. **Build v2** in a separate worktree with distinct MCP + DB
       (FR-038).
    2. **Full-backfill port** of the entire 7339-memo v1 corpus into
       v2 with the migration script (FR-039) — including all
       classification, retag, provenance-link, split, merge, redirect
       operations.
    3. **Soak-test v2** with synthetic + real query workloads driven
       by background test agents that "kick the tires" — exercise the
       mediator, the auditor, the injection hooks, the reconciliation
       path, the flush cycle, and the recall corrections loop against
       the ported corpus. Instrument + measure. Fix issues found.
    4. **Confidence gate**: operator (Ben) reviews soak-test results
       and only then decides IF and HOW to cut over. The cutover
       strategy (big-bang vs. waves vs. session-by-session) is a
       decision made at THIS point, not now.
    5. **Cutover in waves** is the default assumption if it proceeds
       (quantum-first, then assistant, then everything else) with
       one-way v1→v2 replication during the transition window — but
       nothing about the wave shape is committed until step 4.
  Rationale: don't pick a cutover strategy against an unbuilt system;
  the right strategy will be obvious once v2 is proven and any real
  behavior gaps are known.

## Open Questions (for `/speckit.clarify`)

The following were surfaced by the interview + research but not
finalized. Recommended to resolve via `/speckit.clarify` before
`/speckit.plan`. Anchored with `C-nn` ids so code and tests can cite
`001/C-nn` later.

- ~~**C-01**: Exact composition of the "constitutional" class for each
  agent family (nav, mind, dojo, assistant, memo, minders, quantum-*).
  Requires operator + per-family shadow-auditor input.~~ →
  **RESOLVED**: 3-layer stack (Claude Code native + `c` wrapper GUIDE
  + memo gap-fill); memo owns only Layer 2; content-based liveness
  monitoring mirroring stale-guide-detector; operator curates 0-1,
  auditor proposes Layer 2. See Clarifications.
- ~~**C-02**: Injection-set token budget per session — how big before
  it defeats the compaction goal? Ben said "fine with the cost" but
  hard cap TBD.~~ → **RESOLVED**: 5k soft ceiling, tunable per family.
- ~~**C-03**: Auditor implementation: skill-based (invoked from hook)
  vs. long-running background process (agent-service). Latter is more
  capable, former is cheaper. Agent-family scope may argue for a mix.~~
  → **RESOLVED**: hybrid (long-running watcher + hook triggers). See
  Clarifications above.
- ~~**C-04**: Reopenability semantics for `decision-in-progress` memos —
  is this a boolean, a temperature 0-1, a set of freetext triggers, or
  a structured JSON policy? Mind dividend-matrix event motivates but
  doesn't specify.~~ → **RESOLVED**: two-field simple schema
  (`challenge_if_delays_build_by_days` + freetext `operator_tempo_hint`);
  class expected to be small/edge-case; don't over-engineer. See
  Clarifications.
- ~~**C-05**: Answer-loop audit action — when Ben corrects a bad
  recall, does the mediator update its ranking immediately (online
  learning) or wait for the global auditor to promote the correction?~~
  → **RESOLVED**: hybrid — immediate finding-log, ranking-hint
  promotion gated on auditor approval OR 3 corroborating corrections
  in 24h. See Clarifications.
- ~~**C-06**: Migration duplicate detection threshold — cosine
  similarity + title-substring, or LLM-judgment, or both?~~ →
  **RESOLVED**: cosine ≥0.90 + title 4-gram overlap ≥60% + LLM escape
  on borderline.
- ~~**C-07**: Backfill provenance for legacy memos without a
  recoverable source — synthesize a best-guess (auditor infers from
  content) or leave `legacy-unattributed` for operator review?~~ →
  **RESOLVED**: mark `legacy-unattributed`, do NOT LLM-synthesize.
- ~~**C-08**: MCP-flip strategy at fleet scale — big-bang (single
  moment across all hosts) vs. session-by-session as they respawn. Ben
  suggested MCP config flip but rollout timing not specified.~~ →
  **RESOLVED**: cutover strategy DEFERRED; soak-test first (build →
  port → kick-tires → operator confidence gate). Cutover shape decided
  at gate, not now. See Clarifications.
- ~~**C-09**: Retrieval-mediator implementation shape — Python skill
  invoked by CLI, subagent dispatched on every call, or an in-process
  filter chain in the memo server itself. Latency + correctness
  trade-off.~~ → **RESOLVED**: hybrid (in-process filter chain primary
  + LLM fallback on large-result-set or conflict). See Clarifications.
- ~~**C-10**: Auditor-triggered compaction bloat-threshold policy —
  token count, turn count, cache-read tokens/day, or a composite. Agents
  supervisor recommends per-agent-class tuning.~~ → **RESOLVED**:
  composite {transcript >2.5MB, cache-read >20M/day, >120 turns},
  tunable per class. Start aggressive.
