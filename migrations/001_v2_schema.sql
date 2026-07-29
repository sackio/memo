-- 001/FR-001 001/FR-002 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009
-- v2 additive columns on the existing `documents` table.
-- Every column is nullable or defaulted so v1 continues to read cleanly
-- (rollback-safe). Applied idempotently by db.py migration runner.

-- FR-001: class taxonomy
ALTER TABLE documents ADD COLUMN class TEXT NOT NULL DEFAULT 'fact';

-- FR-006: injection_mode
ALTER TABLE documents ADD COLUMN injection_mode TEXT NOT NULL DEFAULT 'on-recall';

-- FR-008: scope (JSON array; default ["global"])
ALTER TABLE documents ADD COLUMN scope TEXT NOT NULL DEFAULT '["global"]';

-- FR-004: provenance (JSON block; nullable for legacy)
ALTER TABLE documents ADD COLUMN provenance TEXT;

-- FR-002: bi-temporal validity window
ALTER TABLE documents ADD COLUMN valid_from REAL NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN valid_until REAL;

-- FR-007: TTL for ephemeral-flush class primarily
ALTER TABLE documents ADD COLUMN expires_at REAL;

-- FR-005: time-scoped memos (JSON: {start, end, trip_id?, calendar_event_id?})
ALTER TABLE documents ADD COLUMN time_scope TEXT;

-- FR-009: reopenability for decision-in-progress class
ALTER TABLE documents ADD COLUMN reopenability TEXT;

-- FR-004: derived_from — array of parent memo IDs
ALTER TABLE documents ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]';

-- Constitution metadata for class=constitutional memos
ALTER TABLE documents ADD COLUMN constitution_meta TEXT;

-- Backfill valid_from for existing v1 rows (= created_at). Only rows with
-- valid_from = 0 (the default) get updated, so this is idempotent.
UPDATE documents SET valid_from = created_at WHERE valid_from = 0;
