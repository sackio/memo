-- 001/FR-028a — deletion log with content snapshots.
--
-- This is what makes aggressive pruning safe. Principle II (amended
-- 2026-07-30) lets rule-bound agents DELETE — byte-identical duplicates,
-- superseded state, expired memos — because a knowledge base nobody prunes
-- decays into the "stale fog" that halted the fleet on 2026-07-20.
--
-- The protection is not a prohibition on deleting. It is that every deletion
-- is RECOVERABLE: enough of the row is captured here to reconstruct the memo,
-- so a wrong call is undoable rather than merely regrettable.
--
-- This also replaces bi-temporal versioning (FR-002, withdrawn 2026-07-30).
-- Retaining every superseded version to answer "what did the corpus believe on
-- date X" cost a valid_until check on every read to serve a query nobody makes.
-- A deletion log serves the real need — recovery and rescue — at a fraction of
-- the machinery.

CREATE TABLE IF NOT EXISTS deletion_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT NOT NULL,
    deleted_at    REAL NOT NULL,

    -- Enough to reconstruct the memo. Content is NOT truncated: a snapshot that
    -- drops the tail of a long memo cannot restore it, which defeats the point.
    content       TEXT NOT NULL,
    title         TEXT,
    tags          TEXT,               -- JSON array
    metadata      TEXT,               -- JSON object
    memo_class    TEXT,
    created_at    REAL,

    -- Who and why. An unattributed deletion is indistinguishable from data loss.
    actor         TEXT NOT NULL,      -- operator:<name> | agent:<name> | cron:<job> | mediator:auto
    reason        TEXT NOT NULL,      -- duplicate-of:<id> | superseded-by:<id> | ttl-expired | empty-stub | operator-directed
    replaced_by   TEXT                -- the surviving memo, when this was a collapse or a supersede
);

CREATE INDEX IF NOT EXISTS deletion_log_doc_idx  ON deletion_log(doc_id);
CREATE INDEX IF NOT EXISTS deletion_log_when_idx ON deletion_log(deleted_at);
CREATE INDEX IF NOT EXISTS deletion_log_actor_idx ON deletion_log(actor);
