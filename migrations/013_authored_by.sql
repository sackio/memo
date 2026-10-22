-- 013: record WHICH SEAT wrote a memo.
--
-- Ben, Slack #memo 2026-10-22 11:47 EDT: "I think it should record who wrote
-- each memo that's good idea" — answering ask a0r, open since 2026-09-20.
--
-- ⭐ WHY A COLUMN AND NOT `metadata.authored_by`. That ad-hoc key already exists
-- on 10 of 600 sampled live docs, written only by `assistant`, and nothing
-- reads it. A fact that lives in a free-form blob on 1.7% of rows cannot be
-- filtered, counted or indexed, so per-seat rollups stay impossible — which is
-- the thing a0r was asked to make possible.
--
-- ⛔ IT IS NULLABLE AND MUST STAY NULLABLE. ~9,900 existing memos have no
-- recoverable author: the writer was never recorded, and inferring one from
-- session transcripts would be a guess stamped as provenance. An honest NULL
-- is a different claim from a wrong name, and only NULL can be corrected later
-- without first being disbelieved.
--
-- ⚠️ `authored_by` IS THE SEAT, NOT THE HUMAN. Every memo here is written by an
-- agent on Ben's behalf; this column answers "which seat's judgement is this",
-- which is what makes a finding checkable against that seat.
ALTER TABLE documents ADD COLUMN authored_by TEXT;

-- Partial index: the overwhelming majority of rows are NULL for the
-- foreseeable future, and indexing those buys nothing.
CREATE INDEX IF NOT EXISTS documents_authored_by_idx
    ON documents(authored_by) WHERE authored_by IS NOT NULL;
