"""Retrieval-mediator contract. [001/FR-010 001/FR-011 001/FR-012 001/FR-013 001/FR-014 001/FR-015]

One test per Response section of contracts/mediator-recall.md — SUCCESS,
NO-RESULTS, ANOMALY/CONFLICT — plus the R-17 degrade path and the FR-014 audit
requirement.

Embeddings stubbed as in the store contract tests: dominant topic axis plus a
content component, so band membership is deterministic and the suite is offline.
"""
import hashlib

import pytest

from memo import db, embeddings
from memo.config import settings
from memo.mediators import recall as recall_mod
from memo.models import RecallRequest

# Keys must be substrings of BOTH the query and the memo. "parking" is not a
# substring of "park?", so keying on it made the query orthogonal to every
# memo and ranked results by the hash component alone.
TOPICS = ["park", "cluster", "dentist", "ups"]


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    async def fake_embed(text: str) -> list[float]:
        v = [0.0] * settings.embedding_dimensions
        t = (text or "").lower()
        idx = next((i for i, k in enumerate(TOPICS) if k in t), len(TOPICS))
        v[idx] = 0.95
        h = int(hashlib.sha256(t.encode()).hexdigest(), 16)
        v[len(TOPICS) + 1 + (h % 64)] = 0.3122498999199199
        return v
    monkeypatch.setattr(embeddings, "embed_query", fake_embed)
    monkeypatch.setattr(embeddings, "embed_document", fake_embed)
    monkeypatch.setattr(recall_mod.embeddings, "embed_query", fake_embed)
    monkeypatch.setattr(recall_mod.embeddings, "embed_document", fake_embed)


class StubLLM:
    def __init__(self, reply=None):
        self.name = "stub"
        self.reply = reply
        self.calls = 0

    async def complete(self, prompt, *, budget_tokens=512, timeout_s=10.0):
        self.calls += 1
        return self.reply

    async def available(self):
        return self.reply is not None


async def seed(content, *, tags=None, created_at=None, valid_until=None, cls="fact"):
    emb = await embeddings.embed_document(content)
    doc_id = await db.store(None, content, None, tags or [], {}, emb)
    conn = db._get_or_create_conn(db.global_path())
    sets, params = ["class = ?"], [cls]
    if created_at is not None:
        # valid_from must move with created_at. Leaving it at wall-clock now
        # makes `valid_from <= as_of` false for any historical as_of, so the
        # bi-temporal filter drops the row and the test sees an empty result.
        sets.append("created_at = ?"); params.append(created_at)
        sets.append("valid_from = ?"); params.append(created_at)
    if valid_until is not None:
        sets.append("valid_until = ?"); params.append(valid_until)
    params.append(doc_id)
    conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return doc_id


def rq(query, **kw):
    kw.setdefault("session_id", "assistant")
    return RecallRequest(query=query, **kw)


# --- SUCCESS ---

@pytest.mark.asyncio
async def test_success_returns_answer_and_citations():
    doc = await seed("Central Garage Level 6 Row R, parking at Logan")
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    assert r.answer and "Level 6" in r.answer
    assert doc in r.citations
    assert r.mediator_version == "1.0.0"
    assert r.latency_ms >= 0


@pytest.mark.asyncio
async def test_happy_path_makes_no_llm_call():
    """The design property that keeps ordinary recalls fast."""
    await seed("Central Garage Level 6 Row R, parking at Logan")
    llm = StubLLM()
    r = await recall_mod.recall(rq("where did I park?"), provider=llm)
    assert llm.calls == 0
    assert r.llm_fallback_used is False


@pytest.mark.asyncio
async def test_filter_chain_trace_is_populated():
    """Explainability: a surprising result must be diagnosable without a re-run."""
    await seed("parking at Logan")
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    joined = " | ".join(r.filter_chain_trace)
    for stage in ("semantic_top_k", "scope", "bi_temporal", "dedup", "boost"):
        assert stage in joined, f"{stage} missing from {joined}"


@pytest.mark.asyncio
async def test_unrelated_memos_are_not_dragged_into_the_answer():
    """max_results is a CEILING, not a target (caught live on /recall)."""
    await seed("Central Garage Level 6 Row R, parking at Logan")
    await seed("the dentist appointment is Thursday at 2pm")
    r = await recall_mod.recall(rq("where did I park?", max_results=5),
                                provider=StubLLM())
    assert len(r.citations) == 1
    assert "dentist" not in (r.answer or "").lower()


# --- NO RESULTS ---

@pytest.mark.asyncio
async def test_no_results_returns_explicit_null_answer():
    """An explicit null is distinguishable from a confidently wrong answer."""
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    assert r.answer is None
    assert r.citations == []
    assert any("gap" in a for a in r.anomalies)


# --- FR-011: bi-temporal ---

@pytest.mark.asyncio
async def test_superseded_memos_are_never_surfaced():
    """The default filter agents rely on — they must not see stale truth."""
    await seed("parking in the old Mission garage", valid_until=1.0)
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    assert r.answer is None, "a superseded memo leaked into recall"


@pytest.mark.asyncio
async def test_as_of_surfaces_the_historical_version():
    await seed("parking in the old Mission garage",
               created_at=1000.0, valid_until=5000.0)
    r = await recall_mod.recall(rq("where did I park?", as_of=3000.0),
                                provider=StubLLM())
    assert r.answer and "Mission" in r.answer


# --- FR-015 / ANOMALY ---

@pytest.mark.asyncio
async def test_duplicate_collapse_is_reported_as_an_anomaly():
    """The caller may want to know one 'fact' is stored several times."""
    body = "Central Garage Level 6 Row R, parking at Logan for the Mexico trip"
    await seed(body)
    await seed(body + " ")   # near-identical restatement
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    assert len(r.citations) == 1
    assert any("duplicate" in a for a in r.anomalies)


# --- R-17 degrade ---

@pytest.mark.asyncio
async def test_llm_unavailable_still_returns_an_answer():
    """Degrade, never block: a search-only answer plus a named degradation."""
    for i in range(20):
        await seed(f"parking note number {i} in some garage")
    r = await recall_mod.recall(rq("where did I park?"), provider=StubLLM(None))
    assert r.answer is not None, "degraded recall must still answer"
    assert r.llm_fallback_used is False
    assert any("degraded" in a for a in r.anomalies)


@pytest.mark.asyncio
async def test_fallback_used_flag_when_llm_answers():
    for i in range(20):
        await seed(f"parking note number {i} in some garage")
    r = await recall_mod.recall(rq("where did I park?"),
                                provider=StubLLM("Reconciled: garage 7."))
    assert r.llm_fallback_used is True
    assert r.answer == "Reconciled: garage 7."


# --- FR-014: audit ---

@pytest.mark.asyncio
async def test_every_recall_is_audited():
    await seed("parking at Logan")
    await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute(
        "SELECT * FROM mediator_audit_log WHERE mediator_kind='retrieval' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["calling_session_id"] == "assistant"
    assert "where did I park" in row["query"]


@pytest.mark.asyncio
async def test_no_results_is_audited_too():
    """A gap is exactly what the auditor most needs to see."""
    await recall_mod.recall(rq("where did I park?"), provider=StubLLM())
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute(
        "SELECT anomaly_flags FROM mediator_audit_log "
        "WHERE mediator_kind='retrieval' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None and "gap" in row["anomaly_flags"]
