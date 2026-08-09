-- 001/FR-002 001/FR-003 — bi-temporal supersession edge log.

CREATE TABLE IF NOT EXISTS supersede_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_id TEXT NOT NULL,
    new_id TEXT NOT NULL,
    superseded_at REAL NOT NULL,
    actor TEXT NOT NULL,          -- operator:<name> | auditor:<session-id> | mediator:auto
    reason TEXT,
    operator_directive_ref TEXT   -- JSON: {kind, from, at, thread} when actor=auditor acting on operator authority
);

CREATE INDEX IF NOT EXISTS supersede_edges_old_idx ON supersede_edges(old_id);
CREATE INDEX IF NOT EXISTS supersede_edges_new_idx ON supersede_edges(new_id);
