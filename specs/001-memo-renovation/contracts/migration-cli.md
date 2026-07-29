# Contract: memo-migrate-backfill CLI

Walks the v1 corpus and produces the v2 corpus. Non-destructive on v1.

## Invocation

```bash
memo-migrate-backfill \
  --v1-url http://server4:8000 \
  --v2-url http://server4:8001 \
  --audit-log /mnt/backup/memo/migration-2026-XX-XX/audit.jsonl \
  --dry-run   # optional: rehearsal, produces the audit-log without writing to v2
```

## Per-memo pipeline

For each memo in v1:

1. **Fetch** the memo + its embedding (v1 `GET /documents/<id>` + embedding row).
2. **Classify** into one of the 10 v2 classes:
   - Tag-based heuristic first (e.g. tags containing `constitution` → `constitutional`, `anti-pattern`/`behavioral-rule`/`hard-rule`/`ben-hard-rule` → `behavioral` etc — per C44 canonical mapping).
   - Fall back to LLM classifier for ambiguous cases (opt-in via `--llm-classify`; default off to keep migration deterministic).
   - Fall back to `legacy-unattributed` if nothing fits.
3. **Retag** — apply canonical vocabulary (retire fragmentation per C44).
4. **Provenance-link** — attempt to reconstruct provenance from tags:
   - `gmail-sourced` → try to extract msg-id from content or metadata.
   - `session-sourced` → try to extract session UUID.
   - Otherwise → mark provenance as `null` and set class to `legacy-unattributed` per C-07.
5. **Split** — if content contains compound facts (heuristic: multiple distinct entity claims + LLM verdict), emit N memos with `derived_from` pointing at the original v1 id.
6. **Merge** — dedup-check against already-migrated v2 memos (cosine ≥ 0.90 + title 4-gram ≥ 60% per C-06). If duplicate, add provenance to existing v2 memo instead of writing a new one.
7. **Redirect** — for the OTHER v1 ids in a duplicate cluster (Matt-Sack case `0c55a9a3/c664f4a1/98efbda5`), write a redirect record: `v1_id → canonical_v2_id`. The v2 store answers `memo_get(v1_id)` with the canonical memo + a redirect notice header.
8. **Set bi-temporal**: `valid_from = v1.created_at`, `valid_until = NULL` (all v1 memos are current at migration time).
9. **Write** to v2 via `POST /store` with `bypass_mediator=true` (migration is an operator-authority context — mediator would reject some fact-updates as unauthorized).
10. **Log** the entire operation to the audit log.

## Audit log format (JSONL)

One line per memo:

```json
{
  "v1_id": "abc-uuid",
  "action": "write-new" | "merge" | "split" | "redirect" | "skip",
  "v2_ids": ["def-uuid"],
  "class_assigned": "fact",
  "class_source": "tag-heuristic" | "llm" | "legacy-unattributed",
  "canonical_tags": ["k8s", "barn-cluster"],
  "provenance_reconstructed": true,
  "provenance_source": "gmail-sourced-tag-inference" | "session-sourced-tag-inference" | null,
  "split_children": [],
  "merged_into": null,
  "redirect_from": null,
  "at": 1785231234.5
}
```

## Per-class backfill rules

Each v2 class has explicit backfill semantics:

| Target class | Detection heuristic | Special-field handling |
|---|---|---|
| `constitutional` | Tag ∈ {`constitution`, `constitutional`}, OR tag ∈ {`hard-rule`, `ben-hard-rule`} where content matches operator-authority pattern | `constitution_meta = {version: "0.0.0-legacy", ratified_at: v1.created_at, amended_at: v1.updated_at, incident_ref: "backfill-2026-XX-XX"}`; injection_mode = `forcible-constitutional` |
| `behavioral` | Tag ∈ {`behavioral-rule`, `operator-coaching`, `anti-pattern`} OR content matches "don't X" / "avoid X" pattern | injection_mode = `forcible-constitutional` if scope=global; `forcible-current-focus` if project-scoped |
| `goal` | Tag ∈ {`goal`, `mission`, `done-line`} OR content matches "we want" / "target" | injection_mode = `forcible-current-focus` |
| `verbatim-critical` | Tag ∈ {`verbatim-critical`, `pinned`} OR content contains full 36-char UUIDs + hard-constraint language | injection_mode = `forcible-constitutional`; DO NOT summarize during compact |
| `fact` | Default for infrastructure / config / reference tags | injection_mode = `on-recall`; provenance strongly preferred |
| `decision-in-progress` | Tag ∈ {`decision`, `wip`, `in-flight`} + content dated within last 30 days | `reopenability` fields left null unless content clearly matches a challenge trigger; injection_mode = `on-recall` |
| `episodic` | Tag ∈ {`session-log`, `incident`, `event`} OR `session-sourced` tag | injection_mode = `on-recall`; reference material only |
| `ephemeral-flush` | Not created during backfill — this class is session-scoped and v1 has no equivalent | Skip (no backfill target) |
| `time-scoped` | Tag ∈ {`parking`, `travel`, `appointment`, `booking`, `trip-*`} — LLM optionally infers `{start, end}` from content when `--llm-classify` is enabled | `time_scope` populated when start/end extractable; else fall back to `fact` |
| `legacy-unattributed` | Fallback when no other class fits AND provenance cannot be reconstructed | No injection; provenance stays null; awaits human review |

**Constitutional-class special case**: v1 has no `constitution_meta` fields. Backfill synthesizes them with `version: "0.0.0-legacy"` and `incident_ref: "backfill-YYYY-MM-DD"`. The operator (Ben) may later ratify individual memos by re-issuing them via the `constitution-proposals` workflow (see `constitution-proposals.md`), which produces proper versioned metadata.

**Time-scoped retrofit**: for v1 memos tagged `parking`/`travel`/`appointment` etc. where the trip window is discoverable from adjacent memos or calendar-event tags, the LLM classifier (opt-in) infers `time_scope = {start, end}`. Where extraction fails, memo stays as `fact` — operator can promote later.

## Post-migration verification

Run `memo-migrate-verify` — separate script:

- Every v1 memo id resolves to a v2 memo (directly or via redirect).
- No memos in v2 without a class assignment.
- `≤ 5%` in `legacy-unattributed` class (SC-009).
- Zero migration-duplicate clusters in v2 (SC-005).
- Sample of 50 canonical queries return the same or better mediator results in v2 vs. v1 raw search.

Exit non-zero on any check failure. Audit log line printed for each failed check.

## Rollback

`memo-migrate-backfill --rollback --v2-url http://server4:8001` — truncates the v2 documents table. v1 untouched throughout. Re-run migration from clean state.
