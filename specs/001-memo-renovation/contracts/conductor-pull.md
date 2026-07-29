# Contract: Conductor Pull Interface (Conductor → memo)

Memo accepts out-of-band events from the Conductor. Transport (default ATC): the Conductor delivers as inbound HTTP `POST /events` to memo's endpoint, OR memo subscribes to a WebSocket / SSE stream depending on Conductor implementation.

## Event kinds memo consumes

| Event kind | Payload | Handler |
|---|---|---|
| `operator.directive` | `{from, content, at, thread}` | → auditor for classification (fact-update? behavioral-rule? etc.) |
| `beacon.acked` | `{beacon_id, acked_by, at}` | Update injection-set-cache staleness |
| `calendar.event` | `{event_id, title, start, end, description}` | → time-scope handler; may auto-pin a memo for the event window |
| `infra.change` | `{resource_type, old_value, new_value, at, source}` | → reconciliation; may auto-supersede infra-tagged memos |
| `bridge.delivery` | `{bridge, external_id, from, content, at, thread}` | Generic — routes to auditor for potential fact-capture (parking, PNRs, codes patterns per Agent E's capture-miss detection) |
| `scheduled.fire` | `{trigger_name, fired_at}` | Named trigger memo registered — runs the registered handler (e.g. reap ephemeral-flush, sweep for stale infra) |
| `session.started` | `{session_id, agent_family, project, pid, at}` | Precompute injection-set-cache for the session |
| `session.ended` | `{session_id, at, transcript_path}` | Trigger auditor final-sweep for that session |

## Endpoint

`POST /events` — HTTP.

**Request:**

```json
{
  "event_id": "uuid-v4",
  "event_kind": "operator.directive",
  "event_time": 1785231234.5,
  "source": "atc@v1.2",
  "payload": {
    "from": "slack:U0NGEHS2J",
    "content": "the K8s IP moved to 192.168.1.244",
    "at": 1785231234.5,
    "thread": "channel:memo"
  }
}
```

**Response (202 Accepted):**

```json
{
  "handler": "auditor",
  "acknowledged": true,
  "trace_id": "trc-abc123"
}
```

## Scheduled + event-triggered fires (FR-042a)

Memo may register named triggers with the Conductor:

**Registration (memo → Conductor):**

```json
POST ${CONDUCTOR_URL}/triggers
{
  "trigger_name": "memo.reap-ephemeral-flush",
  "trigger_kind": "scheduled",
  "cron": "*/5 * * * *",
  "callback_url": "http://server4:8001/events",
  "event_kind_on_fire": "scheduled.fire"
}
```

**Or event-triggered:**

```json
POST ${CONDUCTOR_URL}/triggers
{
  "trigger_name": "memo.reconcile-on-infra-change",
  "trigger_kind": "event",
  "watch_kind": "infra.change",
  "callback_url": "http://server4:8001/events",
  "event_kind_on_fire": "infra.change"
}
```

**Fire:**

Conductor `POST`s the `scheduled.fire` or `infra.change` event back to memo when the trigger fires. Memo runs the registered handler.

## Standalone mode

When `MEMO_CONDUCTOR_PROVIDER=null`, memo runs its OWN 5-minute background scheduler in-process for `reap-ephemeral-flush` (only). All other triggers are skipped with a WARN log; the auditor is degraded but memo core still works.

## Invariants

- Handlers idempotent (Conductor may re-deliver an event on retry).
- Unknown `event_kind` — 200 with `handler: "no-op"` (forward-compat with Conductor evolution).
- Bridge events (FR-042 generic bridges) — memo processes any `bridge.delivery` regardless of which bridge (Slack, phony, Gmail, or something new); the `bridge` field in the payload identifies the source but memo doesn't hardcode a whitelist.
