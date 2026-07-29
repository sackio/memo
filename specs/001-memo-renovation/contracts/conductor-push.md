# Contract: Conductor Push Interface (memo → Conductor)

Memo emits events to the Conductor when specific triggers fire. Provider-supplied transport (ATC HTTP `POST /messages` or `POST /beacons` today; extensible per FR-046).

## Trigger → Event mapping

| Trigger | Event kind | Payload | Default provider action |
|---|---|---|---|
| `memo_store` succeeded | `memo.stored` | `{memo_id, class, scope, provenance, at}` | Optionally beacon target session(s) if the class is `verbatim-critical` |
| `memo_supersede` completed | `memo.superseded` | `{old_id, new_id, actor, reason, at}` | Board post to `sessions` zone if scope includes global |
| Mediator anomaly | `mediator.anomaly` | `{kind, mediator_kind, memo_ids, calling_session, at}` | DM to auditor session for that scope |
| Auditor recommendation | `auditor.recommendation` | `{proposal_id, scope, urgency, at}` | DM to Ben (operator) if `urgency=high` |
| Injection-set change | `injection.updated` | `{session_id, added, removed, at}` | Beacon target session to re-fetch |
| Time-scope enter | `time_scope.enter` | `{memo_id, session_id, at}` | Beacon target session with the new pinned content |
| Time-scope exit | `time_scope.exit` | `{memo_id, session_id, at}` | Beacon target session with depin notice |

## Event schema (generic wrapper)

```json
{
  "event_id": "uuid-v4",
  "event_kind": "memo.stored",
  "event_time": 1785231234.5,
  "source": "memo",
  "payload": { ... event-specific ... },
  "delivery_hints": {
    "target": "session_id or zone or 'auto'",
    "priority": "info | warning | critical",
    "delivery_mode": "message | beacon | board-post"
  }
}
```

## Transport (default ATC)

- HTTP POST to `${CONDUCTOR_URL}/messages` or `/beacons` (memo picks based on `delivery_mode`).
- Auth via HMAC signature per-session (memo has its own ATC subscriber id).
- Non-blocking: memo enqueues the event on an async queue and returns to the hot-path caller immediately; a background worker delivers with retry.
- Failure isolation: Conductor unreachable → event enqueues to a disk-backed dead-letter queue; retried on Conductor recovery; NEVER blocks a mediator response.

## Null-provider mode (standalone)

When `MEMO_CONDUCTOR_PROVIDER=null` (Principle VIII / FR-045):
- All events are WARN-logged to `logs/conductor-null.log` and dropped.
- No retry, no dead-letter queue.
- Mediator + auditor still function; only cross-session coordination + external bridges degrade.

## Invariants

- Every event is idempotent (deliver-once ok; duplicates handled by receiver).
- Event schema is versioned in the `source` field (`"memo@v2.0"`, `"memo@v2.1"`, etc.) so Conductor evolutions don't break memo.
- Bridge event routing is decoupled: memo doesn't know Slack from SMS; the Conductor's bridge layer handles that (FR-042).
