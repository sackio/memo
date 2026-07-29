# Contract: POST /recall (Retrieval Mediator)

Session agents call this instead of the raw `POST /search` / `POST /context` on the recall path.

## Endpoint

`POST /recall` — HTTP + MCP tool `memo_recall`.

## Request

```json
{
  "query": "where did I park at Logan?",
  "session_id": "assistant",
  "agent_family": "assistant",
  "project": null,
  "scope_hint": ["global", "session:assistant"],
  "as_of": null,
  "max_results": 8,
  "budget_tokens": 800,
  "trigger_context": "operator DM"
}
```

**Fields:**
- `query` (required, string): natural-language query.
- `session_id` (required, string): calling session's ATC subscriber id.
- `agent_family` (optional, string): resolved via SESSION_GUIDE if omitted.
- `project` (optional, string): auto-detected from cwd if omitted.
- `scope_hint` (optional, list[string]): scope tags to prefer.
- `as_of` (optional, float): epoch timestamp for point-in-time query. Omit for current truth.
- `max_results` (optional, int): default 8.
- `budget_tokens` (optional, int): default 800; the mediator returns an answer + citations within this budget.
- `trigger_context` (optional, string): what triggered the recall (helps the mediator prioritize).

## Response — SUCCESS (200)

```json
{
  "answer": "Central Garage Level 6, Row R. Parked 2026-07-22 at BOS for Mexico trip 7/22-7/25.",
  "citations": ["58aff069-7d2f-4cf8-88b7-83ebcba356a4"],
  "filter_chain_trace": ["dedup: 3→1", "bi_temporal: 1→1", "recency_boost: rank 4→1", "tag_class_boost: parking+logistics"],
  "llm_fallback_used": false,
  "anomalies": [],
  "latency_ms": 87,
  "mediator_version": "1.0.0"
}
```

## Response — NO RESULTS (200, empty answer)

```json
{
  "answer": null,
  "citations": [],
  "filter_chain_trace": ["semantic_top_k: 0"],
  "llm_fallback_used": false,
  "anomalies": ["gap: no memo covers this query"],
  "latency_ms": 32,
  "mediator_version": "1.0.0"
}
```

Explicit `null` answer signals "not found" — distinct from a wrong-answer surface.

## Response — ANOMALY / CONFLICT (200 with anomalies)

```json
{
  "answer": "candidates conflict — cluster A says X, cluster B says Y",
  "citations": ["<id_A>", "<id_B>"],
  "filter_chain_trace": ["semantic_top_k: 8", "bi_temporal: 8→6", "reconcile: 2 clusters"],
  "llm_fallback_used": true,
  "anomalies": ["conflict: two memos with disjoint claims, neither superseded"],
  "latency_ms": 1450,
  "mediator_version": "1.0.0"
}
```

Anomalies are also emitted to the auditor via ATC event (FR-015).

## Error responses

- `400` — invalid request schema.
- `429` — rate-limited (fleet-wide cap on LLM-fallback calls).
- `503` — memo DB unavailable.

## Invariants

- Default filter: `valid_until IS NULL` — agents never see superseded memos.
- Migration-duplicate clusters collapse to one canonical citation (FR-012).
- Recency + tag-class boost applied for operator-logistics tag families
  (`logistics`, `parking`, `access-code`, `booking`, `appointment`,
  `receipt`, `travel`) — formula tunable, default per FR-013.
- Every call logged to `mediator_audit_log` per FR-014 (retention ≥30 days).
- LLM fallback fires when: `>N` candidates after dedup+bi-temporal, OR
  top candidates conflict (semantic sim ≥ threshold on disjoint content).
