-- 001/FR-014 001/FR-015f 001/FR-035 — retrieval + storage mediator audit log.

CREATE TABLE IF NOT EXISTS mediator_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mediator_kind TEXT NOT NULL,           -- 'retrieval' | 'storage'
    at REAL NOT NULL,
    calling_session_id TEXT,
    calling_role TEXT,
    query TEXT,                            -- JSON — recall query params, or storage incoming memo
    filters TEXT,                          -- JSON — filter set applied
    results TEXT,                          -- JSON — memo IDs + ranking scores
    chosen_action TEXT,                    -- storage only: merge/supersede/split/reject/write-new
    clarification_rounds INT NOT NULL DEFAULT 0,
    latency_ms INT NOT NULL DEFAULT 0,
    anomaly_flags TEXT                     -- JSON — conflicts, stale-memo, gaps (feeds auditor)
);

CREATE INDEX IF NOT EXISTS mediator_audit_at_idx ON mediator_audit_log(at);
CREATE INDEX IF NOT EXISTS mediator_audit_session_idx ON mediator_audit_log(calling_session_id, at);
