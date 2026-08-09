-- 001/FR-044 — memo's INJECTION LOG.
--
-- Originally drafted as a compaction ledger, where memo would count
-- compactions and reconcile against ATC's delivery record. The operator
-- corrected that 2026-07-30, and the correction is right:
--
--   memo's ledger only records IF MEMO'S HOOK FIRES. When the hook does not
--   fire, memo reports zero compactions — which is indistinguishable from no
--   compactions having happened. That is the exact self-report-absence problem
--   the ledger existed to solve, reintroduced one level up.
--
-- The AGENT COORDINATOR is the right counter: it TRIGGERS the compaction, so it
-- knows one happened without depending on any hook, and it is a genuine third
-- party to the memo<->ATC exchange.
--
-- So this table narrows to the half memo can honestly report: WHEN MEMO'S HOOK
-- DID FIRE, what did memo deliver? The coordinator counts compactions and asks
-- memo and ATC what each did; a compaction with no matching row here is memo's
-- hook silently not firing, which only the coordinator can see.
--
-- memo reports its own PRESENCE-AND-OUTCOME. It cannot report its own absence,
-- and no longer pretends to.

CREATE TABLE IF NOT EXISTS injection_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    observed_at REAL NOT NULL,
    fire_point  TEXT NOT NULL,          -- post-compact | session-start
    agent_family TEXT,
    project     TEXT,

    -- The half memo can honestly report: it was present, and this is what
    -- happened. Absence is the coordinator's to detect.
    injected_ok BOOLEAN NOT NULL DEFAULT 1,
    injected_tokens INTEGER
);

CREATE INDEX IF NOT EXISTS injection_log_session_idx ON injection_log(session_id);
CREATE INDEX IF NOT EXISTS injection_log_when_idx    ON injection_log(observed_at);
