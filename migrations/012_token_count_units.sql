-- 2026-09-28 — Ben's condition on the supersession approval (#atc ts 1790613818,
-- confirmed at source): ANY STORED COUNT MUST NAME ITS TOKENIZER.
--
-- ⛔ WHY, MEASURED. `token_count` is cl100k. The write ceiling is enforced in the
-- embedder's Qwen3 tokenizer at max_model_len = 16,384. The column named neither
-- unit, and that one missing label is what kept a real, fixed ceiling invisible:
-- in cl100k the refusal points look scattered (14,594 / 15,489 / 15,544) and imply
-- no constant, while in Qwen3 every boundary document sits 13-231 tokens under
-- 16,384. Six wrong numbers were published across the fleet on 2026-09-28, every
-- one a quantity read in one unit against a limit enforced in another.
--
-- ⛔ THE TWO COUNTS CANNOT BE DERIVED FROM EACH OTHER. Measured over all 108
-- documents with cl100k >= 11,000: cl100k understates Qwen3 by 67 to 4,279 tokens,
-- mean 1,110 — a 64x spread, so no correction factor exists. Worse, the stored
-- column does not even ORDER correctly: the cl100k top-5 and the Qwen3 top-5
-- nearest the ceiling share exactly ONE member. Ranking split candidates by
-- `token_count` picks the wrong documents.
--
-- ✅ `token_count` IS LEFT IN PLACE, UNCHANGED, AND STILL cl100k. ~50 seats read
-- it; renaming it would break every one of them to fix a label. The unit is now
-- stated beside it instead, so a reader can tell what they are holding without
-- knowing memo's internals — the shape ATC's /session-payload uses, which was the
-- one instrument nobody got wrong today because it ships its own unit.
--
-- Additive and reversible: both columns are nullable or defaulted, no existing
-- read changes behaviour, and dropping them restores the prior schema exactly.
ALTER TABLE documents ADD COLUMN token_count_tokenizer TEXT DEFAULT 'cl100k_base';
ALTER TABLE documents ADD COLUMN token_count_qwen3 INTEGER;

-- For "which documents are near the ceiling" — the query the stored cl100k count
-- answers wrongly.
CREATE INDEX IF NOT EXISTS documents_token_count_qwen3_idx
    ON documents(token_count_qwen3);
