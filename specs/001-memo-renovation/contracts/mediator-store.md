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

## Response — REJECT (403)

Mediator refused the write.

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

## Invariants

- Reconcile pass MUST run before persist for every non-`bypass_mediator` call.
- `class = fact` refutation MUST require `operator_directive_ref` or return 403.
- Canonical tag vocabulary applied per C44 (retire `hard-rule` / `ben-hard-rule` / `behavioral-rule` fragmentation).
- Every call logged to `mediator_audit_log` per FR-015f.
- INDEX LAG invariant: caller should NOT immediately `memo_search` for the new memo; use `memo_get(returned_memo_id)` + settle window.
