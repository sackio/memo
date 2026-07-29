# Contract: Claude Code Hook Endpoints

Memo exposes HTTP endpoints that Claude Code hooks call from `~/.claude/settings.json`. Hook wiring is per-host operator configuration, NOT in memo's release artifact (FR-044 / C-01 Layer boundaries).

## SessionStart

Called at every session start (fresh, resume, and post-compact via a separate hook — see below).

**Hook wiring** (`~/.claude/settings.json`):

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "curl -sf -m 3 -X POST http://server4:8001/hooks/session-start --data-binary @-"
      }]
    }]
  }
}
```

**Payload (stdin from Claude Code):**

```json
{
  "hook_event_name": "SessionStart",
  "source": "startup",
  "session_id": "abc-uuid",
  "cwd": "/mnt/nas/data/code/memo",
  "permission_mode": "auto",
  "pid": 12345
}
```

**Response (stdout, JSON):**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "[MEMO Layer 2 injection]\n\n## Constitution ...\n\n## Forcible constitutional\n..."
  }
}
```

The `additionalContext` is the serialized InjectionSet (see `injection-set.md`).

## PostCompact

Called after compaction completes — this REPLACES the `atc-precompact-beacon.py` subagent dance from v1 (per C58).

**Hook wiring:**

```json
{
  "hooks": {
    "PostCompact": [{
      "hooks": [{
        "type": "command",
        "command": "curl -sf -m 3 -X POST http://server4:8001/hooks/post-compact --data-binary @-"
      }]
    }]
  }
}
```

**Payload:** same as SessionStart plus `"source": "compact"` and a compaction summary blob (informational only).

**Response:** same shape as SessionStart — the InjectionSet re-fetched with `flush_generation:<N-1>` populated so previous-session ephemeral-flush memos ride into the fresh post-compact context.

## InstructionsLoaded

Called AFTER Claude Code loads the CLAUDE.md chain (per Agent G finding — this hook fires post-CLAUDE.md-load). Memo uses this to scan the loaded files for `memo:<uuid>` transclusion references and inject resolved content.

**Hook wiring:**

```json
{
  "hooks": {
    "InstructionsLoaded": [{
      "hooks": [{
        "type": "command",
        "command": "curl -sf -m 3 -X POST http://server4:8001/hooks/instructions-loaded --data-binary @-"
      }]
    }]
  }
}
```

**Payload:**

```json
{
  "hook_event_name": "InstructionsLoaded",
  "session_id": "...",
  "instruction_files": [
    {"path": "~/.claude/CLAUDE.md", "content": "..."},
    {"path": "/mnt/nas/data/code/memo/CLAUDE.md", "content": "..."}
  ]
}
```

**Response:**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "InstructionsLoaded",
    "additionalContext": "## Transclusions resolved from your CLAUDE.md files\n\n[memo:5d43c4a0] no-resting rule: ..."
  }
}
```

## SessionEnd

Called at session termination — memo triggers auditor final-sweep for the session's transcript.

**Hook wiring:**

```json
{
  "hooks": {
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "curl -sf -m 3 -X POST http://server4:8001/hooks/session-end --data-binary @-"
      }]
    }]
  }
}
```

**Payload:** `{"session_id": "...", "transcript_path": "/home/ben/.claude/projects/.../abc.jsonl"}`

**Response:** `{}` (no additionalContext needed; work is async — the auditor processes the transcript in background).

## Error tolerance

- Timeout after 3s (`-m 3`) — Claude Code proceeds without additionalContext if memo is unreachable.
- HTTP 500 from memo — Claude Code logs and proceeds without additionalContext.
- Malformed response — same as 500.
- The memo hook endpoints MUST fail-safe: any exception in the endpoint returns `{"hookSpecificOutput": {"hookEventName": "...", "additionalContext": ""}}` rather than 500, so sessions never wedge on memo hook failure.

## Standalone mode

If the operator hasn't wired the hooks (or memo isn't running), Claude Code proceeds with only Layer 0 + Layer 1 context. Sessions still function; Layer 2 gap-fill is simply absent. No error, no wedge. This is the fallback per C-01.
