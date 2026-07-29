# Contract: AgentController Interface (memo → AgentController)

Memo requests operator-level session control operations from the AgentController. The AgentController owns tmux-level execution; memo only issues requests.

## Operations

All are POST requests to `${AGENT_CONTROLLER_URL}/{op}`.

### `POST /spawn`

Spawn a fresh session.

```json
{
  "session_name": "auditor-shadow-quantum-navigator",
  "agent_family": "auditor-shadow",
  "guide_path": ".claude/guides/auditor-shadow.md",
  "atc_zones": ["quantum-navigator"],
  "no_memory": true,
  "additional_args": ["--effort", "low"]
}
```

**Response (201):** `{"session_id": "auditor-shadow-quantum-navigator", "pid": 12345, "started_at": 1785...}`

### `POST /respawn`

Kill current session, spawn fresh with same name.

```json
{
  "session_name": "quantum-data-guardian",
  "preserve_transcript": false,
  "reason": "auditor: transcript exceeded 6MB Bun-segfault threshold"
}
```

### `POST /clear`

Clear session context (same as `/clear` inside Claude Code) without a full respawn.

```json
{"session_name": "..."}
```

### `POST /change-model`

Switch a session's model.

```json
{"session_name": "...", "new_model": "claude-haiku-4-5-20251001"}
```

### `POST /compact`

Trigger `/compact` on a session — memo's auditor calls this per FR-037 / C31.

```json
{
  "session_name": "quantum-navigator",
  "idle_check": true,
  "reason": "auditor: cache-read exceeded 20M tok/day threshold (C-10)"
}
```

**Response includes:** `{"triggered": true, "waited_for_idle_ms": 340}` OR `{"triggered": false, "reason": "session_busy_beyond_wait_window"}`.

### `POST /interrupt`

Interrupt an in-flight turn (ESC-key equivalent).

```json
{"session_name": "...", "reason": "auditor: about-to-take-action would violate memo <id>"}
```

### `POST /inject`

Force-inject a system-reminder-shaped message into a session's next turn.

```json
{
  "session_name": "quantum-navigator",
  "content": "⚠️ Reminder from memo auditor: anti-capture rule (ae52afce) says nav authors seam work — do not delegate this",
  "kind": "system-reminder",
  "urgency": "warning"
}
```

## Null-provider mode (standalone)

When `MEMO_AGENT_CONTROLLER_PROVIDER=null`:
- All operations WARN-log the request + return `{"noop": true}`.
- Auditor's inject / compact / respawn recommendations do NOT execute; they land in the auditor's log for operator review only.

## Invariants

- All operations are best-effort — AgentController may refuse (e.g. `/compact` refuses if session isn't idle-safe per compact-session's own rules).
- Idempotent where possible (spawn with existing name returns the existing pid; respawn always kills-and-restarts).
- AgentController failure NEVER blocks memo hot-path; requests are enqueued + retried.
- Standalone mode is FIRST-CLASS: the auditor's design assumes AgentController may be null, and its own value proposition (write memos, flag anomalies) still holds without it.
