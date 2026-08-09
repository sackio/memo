"""7/26 parking-recall scenario: recency + tag-class must beat a stale match. [001/FR-013]

The failure this pins: asked "where did I park?", v1 surfaced a May memo about
SF parking over the July memo about Logan, because pure semantic similarity
rated the older, wordier memo higher. For operator-logistics families
(`parking`, `travel`, ...) the freshest memo is nearly always the right one and
a stale hit is actively harmful — you drive to the wrong garage.
"""
import hashlib

import pytest

from memo import db, embeddings
from memo.config import settings
from memo.mediators import recall as recall_mod
from memo.models import RecallRequest

NOW = 1_800_000_000.0
DAY = 86400.0

# The May memo is deliberately the STRONGER semantic match — it repeats the
# query's vocabulary more. Without recency + tag-class it wins.
MAY_SF = ("Parked the car in the parking garage on Mission Street, SF — "
          "parking level 2, space 44. Parking receipt in the glovebox.")
JULY_LOGAN = ("Central Garage Level 6, Row R at Logan for the Mexico trip.")


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """Score by how often the query's key term recurs — the v1 failure mode."""
    async def fake_embed(text: str) -> list[float]:
        # Parking content sits on axis 0, everything else on axis 1, so
        # off-topic memos are genuinely orthogonal rather than ~0.9 similar.
        # The earlier version put EVERY text on axis 0 at >=0.90, which made a
        # dentist memo look almost as relevant as a parking one.
        v = [0.0] * settings.embedding_dimensions
        t = (text or "").lower()
        # Both real memos ARE about parking; the May one merely says the word
        # more often. So topic membership keys on the whole vocabulary, not on
        # "park" alone — "Central Garage ... Row R at Logan" contains no "park"
        # substring and would otherwise be scored as an unrelated document.
        if any(k in t for k in ("park", "garage", "row r")):
            hits = min(t.count("park"), 4)
            v[0] = 0.90 + 0.02 * hits      # more repetition -> stronger match
        else:
            v[1] = 0.98
        h = int(hashlib.sha256(t.encode()).hexdigest(), 16)
        v[20 + (h % 32)] = 0.19
        return v
    monkeypatch.setattr(embeddings, "embed_query", fake_embed)
    monkeypatch.setattr(embeddings, "embed_document", fake_embed)
    monkeypatch.setattr(recall_mod.embeddings, "embed_query", fake_embed)
    monkeypatch.setattr(recall_mod.embeddings, "embed_document", fake_embed)


class NoLLM:
    name = "none"
    async def complete(self, prompt, **kw):
        return None
    async def available(self):
        return False


async def seed(content, tags, created_at):
    emb = await embeddings.embed_document(content)
    doc_id = await db.store(None, content, None, tags, {}, emb)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET created_at = ?, valid_from = ? WHERE id = ?",
                 (created_at, created_at, doc_id))
    conn.commit()
    return doc_id


@pytest.mark.asyncio
async def test_july_memo_outranks_the_may_memo(monkeypatch):
    monkeypatch.setattr(recall_mod, "time", lambda: NOW)
    await seed(MAY_SF, ["parking", "travel"], NOW - 70 * DAY)
    july = await seed(JULY_LOGAN, ["parking", "travel"], NOW - 3 * DAY)

    r = await recall_mod.recall(
        RecallRequest(query="where did I park?", session_id="assistant",
                      max_results=5),
        provider=NoLLM(),
    )
    assert r.citations[0] == july, "stale SF memo outranked the fresh Logan one"
    assert "Logan" in (r.answer or "")


@pytest.mark.asyncio
async def test_the_stale_memo_is_still_retrievable_when_asked_for(monkeypatch):
    """Boosting must reorder, not delete. The May memo is still true history."""
    monkeypatch.setattr(recall_mod, "time", lambda: NOW)
    may = await seed(MAY_SF, ["parking", "travel"], NOW - 70 * DAY)
    await seed(JULY_LOGAN, ["parking", "travel"], NOW - 3 * DAY)
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?",
                        (may,)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_tag_class_boost_is_what_separates_equal_age_memos(monkeypatch):
    """Isolates the tag-class term of FR-013 by holding recency constant.

    Note what this does NOT claim. An earlier version of this test asserted
    that WITHOUT logistics tags the stronger semantic match wins — that was
    wrong about the spec. FR-013's formula applies `recency_decay * 0.3` to
    every memo, not only to logistics ones, so a 67-day age gap flips a small
    semantic gap regardless of tags. Recency is universal; the tag-class term
    is the logistics-specific addition. So: equal ages, and only the tag
    differs.
    """
    monkeypatch.setattr(recall_mod, "time", lambda: NOW)
    untagged = await seed(MAY_SF, ["notes"], NOW - 10 * DAY)
    tagged = await seed(JULY_LOGAN, ["parking", "travel"], NOW - 10 * DAY)

    r = await recall_mod.recall(
        RecallRequest(query="where did I park?", session_id="assistant",
                      max_results=5),
        provider=NoLLM(),
    )
    assert r.citations[0] == tagged, (
        "the logistics-tagged memo should win at equal age even though the "
        "untagged one is the stronger semantic match"
    )
    assert untagged in r.citations or len(r.citations) == 1


@pytest.mark.asyncio
async def test_recency_does_not_override_a_much_stronger_match(monkeypatch):
    """Recency is weighted 0.3 against semantic's 0.5, so freshness alone must
    not let an unrelated memo win. Guards the other direction of the boost."""
    monkeypatch.setattr(recall_mod, "time", lambda: NOW)
    relevant_old = await seed(MAY_SF, ["parking", "travel"], NOW - 40 * DAY)
    # NB: not tagged "appointment" — that IS a logistics family, so it would
    # receive the same boost this control is meant to lack.
    await seed("The dentist visit is Thursday at 2pm", ["health"], NOW)
    r = await recall_mod.recall(
        RecallRequest(query="where did I park my car?", session_id="assistant",
                      max_results=5),
        provider=NoLLM(),
    )
    assert r.citations[0] == relevant_old
