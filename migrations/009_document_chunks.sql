-- 002/FR-110 002/FR-108 — passage-level index.
--
-- The memo stays the unit of identity, versioning, provenance and return.
-- These rows exist ONLY so that search can match a paragraph instead of
-- averaging a whole document into one vector. Nothing here alters or replaces
-- stored memo content, and no reader should ever assemble a memo from them.
--
-- Measured 2026-07-30: at a 384-token target this table holds ~21k rows for a
-- 7.5k-memo corpus (2.8x). Passage counts are in specs/002-passage-retrieval/
-- research.md R-02.

CREATE TABLE IF NOT EXISTS document_chunks (
    doc_id       TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_start  INTEGER NOT NULL,   -- the passage's OWN span in the document,
    token_end    INTEGER NOT NULL,   -- excluding any overlap prepended for context

    -- 002/FR-108. A corpus that mixes providers must stay auditable AFTER the
    -- fact. quantum-data measured the same text embedded via OpenRouter vs
    -- OpenAI-direct as 4/5 bit-identical and one at cosine 0.999580 — so one
    -- model *label* can cover non-identical outputs, and without the route
    -- recorded a mixed corpus cannot be told apart later.
    embedding_model TEXT NOT NULL,
    embedding_route TEXT NOT NULL,

    created_at   REAL NOT NULL,

    PRIMARY KEY (doc_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_doc_idx ON document_chunks(doc_id);

-- Grouping passage hits back to memos is on the hot path of every query, and
-- the provenance columns are read whenever a mixed-route corpus is audited.
CREATE INDEX IF NOT EXISTS document_chunks_model_idx
    ON document_chunks(embedding_model, embedding_route);
