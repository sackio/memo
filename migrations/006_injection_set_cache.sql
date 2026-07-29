-- 001/FR-016 — optional cache for InjectionSet computation.

CREATE TABLE IF NOT EXISTS injection_set_cache (
    cache_key TEXT PRIMARY KEY,            -- hash of (session_id, agent_family, project, time-bucket-5min, MEMORY_posture_flag)
    injection_set TEXT NOT NULL,           -- JSON — serialized memo IDs + resolved content
    computed_at REAL NOT NULL,
    expires_at REAL NOT NULL               -- cache TTL, default 5 min
);

CREATE INDEX IF NOT EXISTS injection_set_cache_expires_idx ON injection_set_cache(expires_at);
