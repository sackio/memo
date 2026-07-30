# Contract: POST /store (Storage Mediator)

Session agents call this instead of raw `POST /documents`. Existing v1 MCP tool `memo_store` is preserved but routed through this.

## Endpoint

`POST /store` — HTTP + MCP tool `memo_store` (backward-compat name).

## Request

```json
{
  "content": "K8s barn cluster control-plane IP is 192.168.1.243",
  "title": "Barn K8s control-plane IP",
  "tags": ["k8s", "barn-cluster", "infrastructure"],
  "class": "fact",
  "scope": ["global"],
  "provenance": {
    "claude_log_ref": {
      "host": "server4",
      "project_dir": "-home-ben",
      "session_uuid": "abc123...",
      "line_range_start": 1247,
      "line_range_end": 1268
    }
  },
  "session_id": "cluster",
  "operator_directive_ref": null,
  "bypass_mediator": false
}
```

**Fields:**
- `content` (required, string).
- `title` (optional).
- `tags` (optional, list). Storage mediator may rewrite for canonical vocabulary.
- `class` (optional). If omitted, mediator infers from content + calling role.
- `scope` (optional). Defaults to `["global"]`.
- `provenance` (required for new writes — see data-model.md).
- `session_id` (required, string): caller identity for audit log.
- `operator_directive_ref` (required when contradicting a `class=fact` memo — session-id + timestamp of Ben's authorizing DM).
- `bypass_mediator` (optional, default false). When true, requires operator auth; skips reconcile/clarify.

## Response — MERGE (200)

Mediator merged the incoming memo into an existing one.

```json
{
  "action": "merge",
  "memo_id": "<existing-uuid>",
  "merged_into": ["<existing-uuid>"],
  "provenance_added": true,
  "latency_ms": 145
}
```

## Response — WRITE NEW (201)

```json
{
  "action": "write-new",
  "memo_id": "<new-uuid>",
  "class_inferred": "fact",
  "canonical_tags_applied": ["k8s", "barn-cluster", "infrastructure"],
  "latency_ms": 210
}
```

## Response — SUPERSEDE (200)

Mediator detected the incoming memo supersedes an existing one; operator-authority required.

```json
{
  "action": "supersede",
  "memo_id": "<new-uuid>",
  "superseded": "<old-uuid>",
  "supersede_edge_id": 4271,
  "latency_ms": 320
}
```

## Response — CLARIFY (409)

Mediator needs the calling agent to disambiguate before persisting.

```json
{
  "action": "clarify",
  "conflicting_memo_id": "<existing-uuid>",
  "prompt": "This contradicts memo <id> ('barn control-plane IP is 192.168.1.242'). Is this a supersession, a second interface, or a correction?",
  "resolve_via": "POST /store with `clarification_response` field populated",
  "clarification_token": "clr-abc123",
  "expires_in": 300
}
```

Caller retries the store call including `clarification_response` and the token. Mediator then proceeds with the disambiguated action.

## Refutation flow — 409 THEN 403 (resolved 2026-07-29)

`spec.md` FR-015c and this contract disagreed about what a fact-refutation
without operator authority returns: FR-015c said **409 + `{action:"clarify"}`
("who is authorizing the refutation?")**, while this document's REJECT section
and invariant said **403**. Same case, two answers — and materially different
for callers, since 409 is recoverable in-band and 403 is a hard stop.

**Operator ruling (2026-07-29): both, in sequence.**

1. **First attempt → 409 CLARIFY.** The mediator asks who is authorizing and
   issues a single-use `clarification_token` (TTL 300s). The agent can recover
   in-band by retrying with `operator_directive_ref` set.
2. **Retry still lacking authority (or an explicit decline) → 403 REJECT**,
   with `how_to_authorize`.

So FR-015c's 409 is the OPENING response and this contract's 403 is the
TERMINAL state. An agent that never had authority still ends at 403; an agent
that simply forgot to attach it gets a chance to comply rather than losing the
write.

## Response — REJECT (403)

Mediator refused the write. Per the flow above this is the TERMINAL response —
reached on a retry that still lacks authority, not on first contact.

```json
{
  "action": "reject",
  "reason": "would refute fact memo <id> without operator authority",
  "conflicting_memo_id": "<existing-uuid>",
  "how_to_authorize": "obtain operator directive (Ben DM), then retry with operator_directive_ref set"
}
```

## Response — SPLIT (200)

Compound memo written as multiple entries.

```json
{
  "action": "split",
  "memo_ids": ["<uuid-1>", "<uuid-2>", "<uuid-3>"],
  "split_reason": "compound content — 3 distinct facts detected",
  "latency_ms": 480
}
```

## Error responses

- `400` — schema invalid or provenance missing.
- `429` — rate-limited.
- `503` — memo DB unavailable.

## Latency + LLM availability (amended 2026-07-29, per R-17)

The `latency_ms` values above assumed an in-process inference API. Per **R-17**
the LLM is an interactive Claude Code session reached over ATC. The reconcile
pass is search-first, so only the genuinely ambiguous cases (merge / split /
clarify judgement) reach the LLM:

| Path | Expected |
|---|---|
| Reconcile resolves by search alone (most writes) | **~100-500 ms**, as the examples show |
| Reconcile needs LLM judgement | **~1-10 s** (session round-trip) |
| LLM unavailable | soft-timeout at **10 s**, then degrade |

**Degrade, never block.** If the `memo-llm` session is unavailable, the
storage mediator MUST NOT return 403/503 for want of an LLM. It writes the memo
(`action: "write-new"`) and flags it for the auditor to reconcile later. Losing
an agent's memo is strictly worse than deferring a merge decision.

Note this interacts with the `class = fact` refutation invariant below: that
403 is only correct when a refutation has actually been DETECTED. An
undetectable-because-degraded case must fall through to write-new + auditor
flag, never to a spurious 403.

The provider separately DMs the `agents` supervisor to respawn the session,
rate-limited to one notify per outage.

## Invariants

- Reconcile pass MUST run before persist for every non-`bypass_mediator` call.
- `class = fact` refutation MUST require `operator_directive_ref`. Per the
  "Refutation flow" section above, first contact returns **409 CLARIFY** and
  only a retry still lacking authority returns **403 REJECT** — 403 remains
  the mandatory terminal state, it is just not the first response.
- Canonical tag vocabulary applied per C44 (retire `hard-rule` / `ben-hard-rule` / `behavioral-rule` fragmentation).
- Every call logged to `mediator_audit_log` per FR-015f.
- INDEX LAG invariant: caller should NOT immediately `memo_search` for the new memo; use `memo_get(returned_memo_id)` + settle window.
