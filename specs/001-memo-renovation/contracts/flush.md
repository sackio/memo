# Contract: POST /flush (Session Ephemeral Flush)

Backs FR-034. Called by the PreCompact hook (repurposing atc-precompact.sh per FR-036 / C58) and by the auditor's SessionStop handler. Upserts a slot-set of `ephemeral-flush` memos for the session in one call.

## Endpoint

`POST /flush` — HTTP + MCP tool `memo_flush`.

## Request

```json
{
  "session_id": "quantum-navigator",
  "flush_generation": 47,
  "slots": {
    "active-threads": "Working on T094 seam authorship; blocked on library read-model dedup",
    "in-flight-work": "background bash job pid 12345 running memo-migrate-verify (started 15:22)",
    "pending-dms": "owe reply to atc / no owed replies from ben",
    "open-tasks": "1. finish T094 seam. 2. respawn if transcript >5MB",
    "key-decisions": "SC-018 union scope reaffirmed by ben 07-20 15:16",
    "follow-ups-owed": "report parking spot before Sat 7/25 landing"
  },
  "expires_at": null,
  "provenance": {
    "atc_ref": {
      "kind": "message",
      "id": "beacon-...",
      "from": "memo-hook",
      "zones": []
    }
  }
}
```

**Fields:**
- `session_id` (required): the session whose state is being flushed.
- `flush_generation` (required, int): monotonic counter for this session's flushes; used by the post-compact hook to fetch the correct generation's memos.
- `slots` (required, dict): map of slot name → distilled content. Standard slot names: `active-threads`, `in-flight-work`, `pending-dms`, `open-tasks`, `key-decisions`, `follow-ups-owed`. Custom slot names allowed.
- `expires_at` (optional, epoch): defaults to `now + 24h` if omitted; TTL enforced by the 5-min sweeper (R-14).
- `provenance` (required): ATC event ref if invoked from hook; auditor session id if invoked from SessionStop handler.

## Response (200)

```json
{
  "flush_generation": 47,
  "memo_ids": {
    "active-threads": "flush-abc-...",
    "in-flight-work": "flush-def-...",
    "pending-dms": "flush-ghi-...",
    "open-tasks": "flush-jkl-...",
    "key-decisions": "flush-mno-...",
    "follow-ups-owed": "flush-pqr-..."
  },
  "previous_generation_reaped": 46,
  "latency_ms": 145
}
```

- All memos written with `class = ephemeral-flush`, `scope = ["session:<session_id>"]`, `tags = ["ephemeral-flush", "session:<session_id>", "flush-generation:<N>", "slot:<slot-name>"]`.
- Previous generation memos for the same session are auto-reaped on successful new flush (keeps only latest + one prior for post-compact re-injection).

## Retrieval

Post-compact hook calls `GET /injection-set?flush_generation=<N-1>` to include the previous generation's memos in the new session's Layer 2 context (contract in `injection-set.md`).

## Invariants

- Flush is SYNCHRONOUS relative to the hook that called it — PreCompact returns only after all slots are persisted (else the memos are lost when compaction fires).
- Storage-mediator bypass is REQUIRED for the flush call (`bypass_mediator=true`, operator-directive-ref = hook context), because slot memos are session-scoped and should not trigger reconcile-against-corpus.
- `flush_generation` MUST be monotonic per session; two flushes with the same generation return 409.
- TTL sweeper (R-14) reaps expired flush memos every 5 minutes; the auto-reap-on-new-flush is an additional layer for hot paths.
