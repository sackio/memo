-- 001/FR-023 — constitution proposal workflow (auditor proposes, operator ratifies).

CREATE TABLE IF NOT EXISTS constitution_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at REAL NOT NULL,
    proposed_by TEXT NOT NULL,
    layer TEXT NOT NULL,                    -- 'constitutional' | 'behavioral' | 'goal' | 'verbatim-critical'
    scope TEXT NOT NULL,                    -- JSON
    proposed_content TEXT NOT NULL,
    proposed_tags TEXT NOT NULL,            -- JSON array
    proposed_class TEXT NOT NULL,
    evidence TEXT,                          -- JSON — session UUIDs, event refs, frustration signals
    urgency TEXT NOT NULL DEFAULT 'medium', -- 'low' | 'medium' | 'high'
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'accepted' | 'rejected'
    resolved_at REAL,
    resolution_note TEXT,
    resulting_memo_id TEXT
);

CREATE INDEX IF NOT EXISTS constitution_proposals_status_idx ON constitution_proposals(status, proposed_at);
