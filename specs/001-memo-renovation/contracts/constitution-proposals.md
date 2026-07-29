# Contract: Constitution Proposal Workflow

Backs Principle V (Operator Owns the Constitution) + FR-023. Auditors propose additions/changes to constitutional-class memos; operator (Ben) accepts or rejects; memo executes on the accepted proposals.

## Overview

**Auditors** call `POST /constitution/propose` to file a proposal.
**Operator** (Ben) accepts/rejects via `POST /constitution/resolve` (usually driven from the speckit-review UI, but also callable directly from an operator DM handler).
**Memo** creates the underlying constitutional memo automatically on acceptance.

## POST /constitution/propose (auditor → memo)

**Request:**

```json
{
  "proposed_by": "quantum-navigator-shadow-auditor",
  "layer": "constitutional",
  "scope": ["global"],
  "proposed_content": "Never author a rewarm-pin without a full 36-char UUID reference — prefix lookups return null by design and quietly break re-warm continuity.",
  "proposed_tags": ["constitutional", "rewarm-pin", "full-uuid-discipline"],
  "proposed_class": "constitutional",
  "evidence": {
    "session_uuids": ["abc-..."],
    "atc_event_ids": ["beacon-..."],
    "frustration_signals": ["operator DM 2026-07-29 15:24: 'agent should have full UUID'"],
    "recurrence_count": 3
  },
  "urgency": "medium"
}
```

**Response (201):**

```json
{
  "proposal_id": 4271,
  "status": "pending",
  "notified": ["slack:U0NGEHS2J"],
  "expected_resolution_by": null
}
```

Optionally memo emits a `auditor.recommendation` event to the Conductor which the default provider routes to Ben's Slack for review.

## GET /constitution/proposals?status=pending

**Response (200):**

```json
{
  "proposals": [
    {
      "proposal_id": 4271,
      "proposed_at": 1785231234.5,
      "proposed_by": "quantum-navigator-shadow-auditor",
      "layer": "constitutional",
      "scope": ["global"],
      "proposed_content": "...",
      "proposed_tags": [...],
      "proposed_class": "constitutional",
      "evidence": {...},
      "urgency": "medium",
      "age_hours": 12.5
    }
  ],
  "total_pending": 1
}
```

Used by the speckit-review UI to list open proposals.

## POST /constitution/resolve (operator → memo)

**Request — ACCEPT:**

```json
{
  "proposal_id": 4271,
  "resolution": "accepted",
  "resolution_note": "Yes, seen this three times now, promote to constitutional-class.",
  "operator_directive_ref": {
    "kind": "slack_dm",
    "from": "slack:U0NGEHS2J",
    "at": 1785231500.0,
    "thread": "channel:memo"
  },
  "amendments": {
    "content_override": null,
    "tags_override": null,
    "class_override": null,
    "constitution_meta": {
      "version": "1.4.0",
      "ratified_at": 1785231500.0,
      "incident_ref": "session-abc-... + operator DM 15:24 2026-07-29"
    }
  }
}
```

**Response (200):**

```json
{
  "proposal_id": 4271,
  "resolution": "accepted",
  "resulting_memo_id": "<uuid>",
  "class": "constitutional",
  "constitution_version_bump_recommended": "1.3.0 → 1.4.0"
}
```

If accepted for a `class = constitutional` proposal, memo:
1. Creates the memo with `injection_mode = forcible-constitutional` and the provided `constitution_meta`.
2. Sets scope per the proposal.
3. Invalidates injection-set caches for any session whose scope matches.
4. Emits `memo.stored` event (kind: constitutional-add) to the Conductor for fleet visibility.
5. Adds a change-log entry to `.specify/memory/constitution.md` (optional; skipped if the memo is per-agent-family not fleet-wide).

**Request — REJECT:**

```json
{
  "proposal_id": 4271,
  "resolution": "rejected",
  "resolution_note": "Not general enough — this is a full-UUID discipline rule that lives better in the rewarm-pin skill, not the fleet constitution.",
  "operator_directive_ref": { ... }
}
```

**Response (200):**

```json
{
  "proposal_id": 4271,
  "resolution": "rejected",
  "archived": true
}
```

Rejected proposals stay in `constitution_proposals` (archived) so the auditor can see prior rejections and avoid re-proposing the same rule.

## Invariants

- ONLY the operator (via `operator_directive_ref`) can resolve. Auditors cannot self-approve. HTTP 403 if the caller isn't authenticated as operator.
- Accepted proposals produce ONE memo write; no partial state (transactional).
- Rejected proposals never delete — always archived, so the auditor's ML/heuristic can learn from rejections.
- Constitution version-bump is RECOMMENDED not automatic; operator manually updates `.specify/memory/constitution.md` if the amendment is fleet-wide. Per-agent-family additions don't bump the constitution version.
- Proposal age > 30 days without resolution → auditor may DM Ben a "stale proposal" reminder; the proposal itself stays pending indefinitely.
