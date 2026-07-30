"""Migration-duplicate cluster collapses to one canonical citation. [001/FR-012]

Reproduces the real Matt-Sack scenario from the v1 corpus — memos
`0c55a9a3` / `c664f4a1` / `98efbda5`: one canonical memo plus two
near-identical duplicates left behind by migration (C-06). In v1 a recall
returned all three, so the same fact looked corroborated by three independent
sources and burned three result slots. Retrieval must surface exactly one.
"""
import hashlib

import pytest

from memo import db, embeddings
from memo.config import settings
from memo.mediators import recall as recall_mod
from memo.models import RecallRequest

# The shared claim, as it appears three times in the corpus with the small
# wording drift that migration duplicates actually exhibit.
CANONICAL = ("Matt Sack is Ben's brother. He lives in Massachusetts and works "
             "in software. Reachable on his mobile for family logistics.")
DUP_1 = ("Matt Sack is Ben's brother — lives in Massachusetts, works in "
         "software. Reachable on his mobile for family logistics.")
DUP_2 = ("Matt Sack is Ben's brother. He lives in Massachusetts and works in "
         "software. Reach him on his mobile for family logistics.")


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """Duplicates share a topic axis; wording drift gives a small content delta."""
    async def fake_embed(text: str) -> list[float]:
        v = [0.0] * settings.embedding_dimensions
        t = (text or "").lower()
        v[0 if "matt" in t or "brother" in t else 1] = 0.97
        h = int(hashlib.sha256(t.encode()).hexdigest(), 16)
        v[10 + (h % 32)] = 0.24310871685131  # sqrt(1 - 0.97**2)
        return v
    monkeypatch.setattr(embeddings, "embed", fake_embed)
    monkeypatch.setattr(recall_mod.embeddings, "embed", fake_embed)


class NoLLM:
    name = "none"
    async def complete(self, prompt, **kw):
        return None
    async def available(self):
        return False


async def seed(content):
    emb = await embeddings.embed(content)
    return await db.store(None, content, None, ["family"], {}, emb)


@pytest.mark.asyncio
async def test_cluster_collapses_to_a_single_citation():
    ids = [await seed(CANONICAL), await seed(DUP_1), await seed(DUP_2)]
    r = await recall_mod.recall(
        RecallRequest(query="who is Matt Sack?", session_id="assistant"),
        provider=NoLLM(),
    )
    assert len(r.citations) == 1, f"expected 1 canonical citation, got {r.citations}"
    assert r.citations[0] in ids


@pytest.mark.asyncio
async def test_answer_states_the_fact_once():
    await seed(CANONICAL)
    await seed(DUP_1)
    await seed(DUP_2)
    r = await recall_mod.recall(
        RecallRequest(query="who is Matt Sack?", session_id="assistant"),
        provider=NoLLM(),
    )
    assert (r.answer or "").lower().count("brother") == 1, \
        "the same fact was restated once per duplicate"


@pytest.mark.asyncio
async def test_collapse_is_visible_in_the_trace_and_anomalies():
    """Collapsing must be reported, not silent — three sources becoming one
    is exactly the kind of thing a curator needs to be able to see."""
    await seed(CANONICAL)
    await seed(DUP_1)
    await seed(DUP_2)
    r = await recall_mod.recall(
        RecallRequest(query="who is Matt Sack?", session_id="assistant"),
        provider=NoLLM(),
    )
    assert any("dedup" in t and "collapsed" in t for t in r.filter_chain_trace), \
        r.filter_chain_trace
    assert any("duplicate" in a for a in r.anomalies)


@pytest.mark.asyncio
async def test_a_genuinely_different_family_memo_is_not_collapsed():
    """Guard against over-collapsing: dedup must not eat distinct relatives."""
    await seed(CANONICAL)
    await seed("Laura Sack is Ben's wife and works in clinical genetics.")
    r = await recall_mod.recall(
        RecallRequest(query="tell me about Ben's brother Matt", session_id="a",
                      max_results=5),
        provider=NoLLM(),
    )
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2, \
        "both memos should still be stored"
    assert r.citations, "recall should still answer"
