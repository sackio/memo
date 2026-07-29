# Contract: GET /injection-set

Returns the Layer 2 gap-fill content for a given session — the memos memo must inject at SessionStart / PostCompact / InstructionsLoaded to fill the gap Claude Code's auto-load leaves.

## Endpoint

`GET /injection-set` — HTTP + MCP tool `memo_injection_set`.

Usually called from a Claude Code hook script, not by an agent directly.

## Request (query params)

- `session_id` (required): ATC subscriber id, or auto-detected from cwd's session-registry lookup.
- `agent_family` (optional): resolved via SESSION_GUIDE if omitted.
- `project` (optional): auto-detected from cwd.
- `pid` (optional): calling session's PID — memo uses `/proc/<pid>/environ` to read `CLAUDE_CODE_DISABLE_AUTO_MEMORY` posture (C71).
- `current_time` (optional, epoch): defaults to now.
- `flush_generation` (optional, int): pass N-1 to auto-include previous session's ephemeral-flush memos for continuity.

## Response (200)

```json
{
  "session_id": "quantum-navigator",
  "agent_family": "quantum-navigator",
  "project": "quantum-feed",
  "memory_posture": "on",
  "spec_kit_constitution_content": "# Memo Constitution\n...(full text of .specify/memory/constitution.md if found at cwd tree)...",
  "forcible_constitutional": [
    {"id": "ae52afce-...", "content": "...", "title": "Anti-capture rule"},
    {"id": "5d43c4a0-...", "content": "...", "title": "No resting with work pending"}
  ],
  "forcible_current_focus": [
    {"id": "goal-abc...", "content": "current mission: ship T094 seam", "title": "Current focus"},
    {"id": "time-scope-xyz...", "content": "Logan parking Cape Cod L6 R", "title": "Travel"}
  ],
  "transclusions": [
    {"source_file": "~/.claude/CLAUDE.md", "referenced_uuid": "5d43c4a0-...", "resolved_content": "..."}
  ],
  "token_budget_used": 3120,
  "token_budget_ceiling": 5000,
  "computed_at": 1785231234.5
}
```

**Rendering for `additionalContext`:**

The hook script serializes this into a single `additionalContext` string that Claude Code injects into the session's prompt at the fire point. Structure:

```
[MEMO Layer 2 injection — this is your persistent memory]

## Constitution (spec-kit)
{spec_kit_constitution_content}

## Forcible constitutional
- [ae52afce] Anti-capture rule: ...
- [5d43c4a0] No resting with work pending: ...

## Current focus
- Current mission: ship T094 seam
- Travel: Logan parking Cape Cod L6 R (valid 2026-07-22 → 2026-07-25)

## Transclusions from CLAUDE.md
[5d43c4a0 from ~/.claude/CLAUDE.md] ...

Memory posture: on (memo augments; native MEMORY.md also loads)
Injection budget: 3120 / 5000 tokens
```

## Response — OPTED OUT (200 empty)

If `MEMO_DISABLE_INJECTION=1` in the calling session's environ:

```json
{
  "session_id": "...",
  "opt_out": true,
  "reason": "MEMO_DISABLE_INJECTION=1"
}
```

Hook produces no `additionalContext`; session runs "clean" of Layer 2.

## Error responses

- `400` — session_id malformed / not resolvable.
- `500` — memo DB error or SESSION_GUIDE unreachable (falls back to empty forcible set with a WARN log).

## Invariants

- Total serialized `additionalContext` MUST fit within `token_budget_ceiling` (default 5k per C-02).
  Budget exceeded → mediator drops lowest-priority items (current-focus first, then transclusions, never constitutional).
- `spec_kit_constitution_content` populated only when `.specify/memory/constitution.md` is found in cwd or walked-up tree (per FR-050 in refined spec — Agent G finding that Claude Code does NOT auto-load this).
- `memory_posture = "off"` mode: same response shape, but the auditor treats it as a "role expanded" case (C71) — Layer 2 IS the memory layer.
- Cached in `injection_set_cache` for 5 minutes to avoid recomputation on hot repeat-call paths.
