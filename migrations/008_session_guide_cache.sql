-- 001/FR-016 — cached snapshot of the `agents`-roster SESSION_GUIDE table
-- for offline resolution when ATC is unavailable. Refreshed daily.

CREATE TABLE IF NOT EXISTS SESSION_GUIDE_cache (
    session_name TEXT PRIMARY KEY,
    guide_path TEXT NOT NULL,
    guide_convention TEXT NOT NULL,        -- 'standard' | 'agent-guide-md' | 'session-handoff-doc' | 'skill-based'
    fetched_at REAL NOT NULL
);
