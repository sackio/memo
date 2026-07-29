# Memo Constitution

Memo is the durable, cross-session memory substrate for a fleet of agents.
The rules below apply to every version, every worktree, every deployment,
and every client. A rule belongs here only if violating it makes memo wrong
or unsafe **regardless of how it is implemented**. Everything else — schema,
transport, endpoints, tooling — belongs in `specs/`.

## Core Principles

### I. Agents Are the Primary Users (NON-NEGOTIABLE)

Memo is built for agents first, humans second. Agents both READ (auto-recall
during work, initial-context load, post-compaction re-warm) and WRITE
(auto-curate as they discover facts). Human writes (`/memorize`, `/recall`)
are supported but must never be the design center — any decision that makes
the human path smoother at the cost of the agent path is prohibited.

*Ratified after:* the 2026-07-29 renovation interview, where Ben confirmed
*"in practice agents have mostly done all of this and I want agents to be
responsible for this."*

### II. Facts Are Authoritative — Only Operators Can Refute Them (NON-NEGOTIABLE)

Any agent may WRITE a new fact when one comes to light. No agent may REFUTE
or overwrite an existing fact. Refutation requires operator input, either
directly (Ben contradicting a memo in DM) or via an auditor invoked at the
operator's request. This asymmetry is what makes memo trustworthy as a
fleet-shared substrate — an agent's session context cannot poison the
shared truth.

*Ratified after:* the 2026-07-20 operator halt to nuke "stale fog" — the
demonstration that agents left to reconcile facts amongst themselves accrue
contradictions faster than they resolve them.

### III. Provenance Is First-Class (NON-NEGOTIABLE)

Every memo must be linkable to the source material that produced it —
Claude Code log line-range, git commit SHA + file:line, Gmail msg-id,
phony record-id, ATC event-id, URL, or derived-from(other memo). Provenance
is a structured field on every memo, never a free-text tag afterthought.
A memo without provenance can exist only as a temporary migration artifact,
and must be reconciled during the backfill process.

*Ratified after:* the FLAVOR-DUAL correction of 2026-07-16 — a load-bearing
operator directive that had to be verbatim-copy-pasted into four successor
sessions because the memo system could not carry the reference.

### IV. Behavioral Rules Are Forcibly Injected, Not Retrieved

Some memo classes MUST ride in every session's context every turn regardless
of whether the agent thinks to search for them. The forcible-injection tiers
are constitutional (fleet-wide + agent-family-wide, always in prompt) and
current-focus (session-scoped, always in prompt, re-injected at compaction).
Retrieval-based recall (`memo_search`, `memo_context`) is the delivery path
for facts and episodic memory only — never for behavioral rules, hard-rule
constraints, active goals, or operator directives that carry the
`verbatim-critical` badge.

*Ratified after:* the past 14-day audit showing 88 quantum-scoped behavioral
memos and zero `memo_search` calls surfacing them across all quantum-navigator
sessions — retrieval-only injection is proven insufficient.

### V. Operator Owns the Constitution

The constitution and other constitutional-class memos may only be modified
by the operator (Ben) or by an auditor acting on an explicit operator
instruction. Any agent may PROPOSE changes; the bar is high, the acceptance
path is operator-approved. Agents may not create, edit, supersede, or reap
constitutional-class memos on their own initiative — even if they believe
the rule is wrong.

*Ratified after:* Ben's 2026-07-29 answer to Q7 — *"generally I do not want
to allow agents to modify the constitution — that's the whole point."*

### VI. Bi-Temporal Truth in the Store, Filtered on Read

Every memo carries `valid_from` and `valid_until` timestamps. Supersession
is a temporal edge — the old memo is not deleted; its `valid_until` is set
to the moment its successor became true. The audit trail is preserved
forever in the live store, not just backups.

On the READ path, the retrieval mediator filters `valid_until IS NULL` by
default so agents never see refuted facts. Operators, auditors, and
explicit point-in-time queries see the full history. Backups remain the
forever-cold-storage safety net.

*Ratified after:* the 2026-07-29 pushback-and-accept exchange where Ben
approved separating store semantics (append-only + temporal) from read
semantics (current-truth filtered).

### VII. Every Store and Read Operation Goes Through a Mediator

Session agents do not query or write the memo store directly on the RAG
path. Both sides are mediated:

- **Retrieval mediator** — every recall goes through it. Filters by
  session-relevance, reconciles contradicting memos in real time, returns
  the ANSWER (potentially much smaller than the raw memos) not a dump of
  hits, logs the query + result + latency for observability, and reports
  anomalies to the auditor.
- **Storage mediator** — every write goes through it. Before persisting,
  it (a) reconciles the incoming memo against the current corpus — merge
  into an existing memo if it is the same fact, supersede if it is a
  refutation, split if it is compound, reject if it violates
  fact-refutation rules (Principle II), (b) may synchronously go back to
  the calling agent for clarification when the write is ambiguous, and
  (c) applies canonical tagging + class inference before the memo lands.
  The database gets curated on the write path, not only by after-the-fact
  sweeps.

The direct store API (`memo_get`, `memo_list`, raw `memo_store`) remains
available for auditors, migrations, and admin tooling, but agents in
session must go through the mediators for both reads and writes.

*Ratified after:* Ben's 2026-07-29 answers to Q6c (retrieval mediator)
and his 2026-07-29 14:22 EDT amendment (*"storage should be mediated as
well by something that before writing makes sure it has reconciled with
other memos and potentially gone back to the agent to clarify"*).

### VIII. Integration-Ready, Not Integration-Bound

Memo composes with three external orchestration surfaces: a **Conductor**
— the real-time messaging + bridging + event-triggering + external-comms
layer (as ATC provides today, spanning channel↔channel messaging, an
extensible bridge concept currently covering Slack + phony/Twilio +
Gmail but open to further bridges added over the Conductor's own release
cycle, temporal board posts with TTL, and scheduled + event-triggered
fires) — an **AgentController** — the operator-level toolkit that
automates anything the Claude Code CLI or SDK can do to a session
(spawn, respawn, start-fresh, clear, change model, compact, interrupt,
force-inject), as the `agents` supervisor provides today — and the
Claude Code hook chain
(SessionStart, SessionStart:compact, PreCompact, SessionStop,
SessionEnd). Memo defines the interfaces to each of these but treats
each as a *pluggable provider* — the concrete ATC and agents
implementations are wired at deployment, not compiled into memo.

Memo MUST run standalone with no provider attached: all CRUD, both
mediators, and the auditor's own record-keeping still work. Integration
features (mid-session injection, ATC event fanout, auditor-triggered
compaction) degrade gracefully when a provider is absent, with clear
logging so an operator can see what would have fired.

The purpose of this asymmetry is durability: ATC and agents are being
independently revised on their own cycles. Memo cannot version-lock its
own release cadence to theirs, and vice versa. Interfaces are stable;
implementations behind them are free to evolve.

*Ratified after:* Ben's 2026-07-29 14:30 EDT amendment — *"we should
build memo in such a way that it anticipates integration with the toolkit
that allows for those types of things but is not directly forcibly tied
to agents … we will plug in agents and ATC to the finished memo."*

## Governance

### Enforce, Don't Document

A principle above is in force only if a live gate enforces it. Prose in this
file with no test, no runtime check, and no observable audit failure is
aspirational, not constitutional. New principles ship with the gate that
enforces them; principles whose gates go stale are marked `UNENFORCED` at
the next audit until re-instrumented.

### Amendment Process

Proposals may come from any agent, human, or auditor via a
`constitution-proposal` memo tagged `proposal-pending`. The operator (Ben)
reviews. Accepted proposals produce (a) an edit to this file, (b) a bump to
the `Version` field, (c) an update to `Last Amended`, and (d) an
incident-anchor line in the ratified principle. Rejected proposals are
kept as memos tagged `proposal-rejected` with the rejection reason.

### Incident-Anchoring Requirement

No constitutional principle may be added without a named ratifying event —
a session UUID + timestamp, a git commit SHA, an ATC event id, or an
operator directive with a timestamp. The purpose is falsifiability: every
rule points to the failure it was ratified against, so future readers can
judge whether the failure is still real.

### Single-Source Discipline

The constitution lives in exactly one place: this file, referenced by every
worktree by relative path, never by copy. Constitutional-class memos live
in exactly one memo id, referenced by every session by id, never by
verbatim copy-paste into rewarm prompts. Copy-drift is the failure mode
this rule prevents.

### Precedence

This constitution supersedes every project-level `CLAUDE.md`, every
per-agent SKILL.md, and every ATC pin. When they conflict, the constitution
wins; the conflicting document is corrected in the same amendment cycle.

**Version**: 1.3.0 | **Ratified**: 2026-07-29 | **Last Amended**: 2026-07-29
<!-- 1.0.0 → 1.1.0: expanded Principle VII to cover storage-side mediator per Ben 14:22 EDT amendment. -->
<!-- 1.1.0 → 1.2.0: added Principle VIII (integration-ready, not integration-bound) per Ben 14:30 EDT amendment. -->
<!-- 1.2.0 → 1.3.0: renamed abstractions to Conductor + AgentController + expanded their scope per Ben 14:38-14:49 EDT amendments. -->

