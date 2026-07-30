"""Storage-mediator contract. [001/FR-015a 001/FR-015b 001/FR-015c 001/FR-015d 001/FR-015e 001/FR-015f 001/FR-015g]

One test per Response section of contracts/mediator-store.md — MERGE,
WRITE-NEW, SUPERSEDE, CLARIFY, REJECT, SPLIT — plus the degrade paths R-17
requires.

Embeddings are stubbed to orthogonal unit vectors keyed on a topic word, so
"same topic" scores 1.0 and "different topic" scores ~0.0. That makes the
similarity thresholds deterministic instead of dependent on a live embedding
service, and keeps the suite offline.
"""
import pytest

from memo import clarify, db, embeddings
from memo.config import settings
from memo.mediators import store as store_mod
from memo.models import MediatorStoreRequest, Provenance

TOPICS = ["ups", "dentist", "cluster", "parking"]


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    """Deterministic, offline, content-keyed embeddings."""
    async def fake_embed(text: str) -> list[float]:
        v = [0.0] * settings.embedding_dimensions
        idx = next((i for i, t in enumerate(TOPICS) if t in (text or "").lower()), len(TOPICS))
        v[idx] = 1.0
        return v
    monkeypatch.setattr(embeddings, "embed", fake_embed)
    monkeypatch.setattr(store_mod.embeddings, "embed", fake_embed)


@pytest.fixture(autouse=True)
def _clean_clarify():
    clarify.clear()
    yield
    clarify.clear()


class StubLLM:
    """Scripted provider. `replies=None` simulates an unavailable session."""

    def __init__(self, replies=None):
        self.name = "stub"
        self.replies = replies
        self.calls: list[str] = []

    async def complete(self, prompt, *, budget_tokens=512, timeout_s=10.0):
        self.calls.append(prompt)
        if self.replies is None:
            return None
        return self.replies.pop(0) if self.replies else "COMPATIBLE"

    async def available(self):
        return self.replies is not None


def req(content, **kw):
    kw.setdefault("session_id", "cluster")
    kw.setdefault("provenance", Provenance(url="https://example.invalid/x"))
    return MediatorStoreRequest(content=content, **kw)


async def seed(content, *, cls="fact", tags=None):
    """Put an existing memo in the corpus with a chosen class."""
    emb = await embeddings.embed(content)
    doc_id = await db.store(None, content, None, tags or [], {}, emb)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class=? WHERE id=?", (cls, doc_id))
    conn.commit()
    return doc_id


# --- WRITE-NEW ---

@pytest.mark.asyncio
async def test_write_new_on_empty_corpus():
    r = await store_mod.store(req("the ups is a cyberpower cp1500"), provider=StubLLM([]))
    assert r.action == "write-new"
    assert r.memo_id
    assert r.class_inferred == "fact"


@pytest.mark.asyncio
async def test_write_new_when_nothing_is_similar():
    await seed("the dentist appointment is thursday")
    r = await store_mod.store(req("the ups is a cyberpower cp1500"), provider=StubLLM([]))
    assert r.action == "write-new"


@pytest.mark.asyncio
async def test_canonical_tags_applied():
    """C44: the hard-rule fragmentation collapses to one vocabulary."""
    r = await store_mod.store(
        req("ups rule", tags=["hard-rule", "ben-hard-rule", "k8s"]),
        provider=StubLLM([]),
    )
    assert "behavioral-rule" in r.canonical_tags_applied
    assert "hard-rule" not in r.canonical_tags_applied
    assert r.canonical_tags_applied.count("behavioral-rule") == 1


@pytest.mark.asyncio
async def test_missing_provenance_reclassifies_rather_than_rejecting():
    """data-model.md: a fact without provenance becomes legacy-unattributed.

    Losing the write would be worse — the mediator owns this call precisely
    because it can reclassify instead.
    """
    r = await store_mod.store(
        MediatorStoreRequest(content="ups with no provenance", session_id="s"),
        provider=StubLLM([]),
    )
    assert r.action == "write-new"
    assert r.class_inferred == "legacy-unattributed"


@pytest.mark.asyncio
async def test_v2_columns_are_persisted():
    r = await store_mod.store(
        req("ups fact", scope=["project:memo"]), provider=StubLLM([]))
    doc = await db.get_current(None, r.memo_id)
    assert doc["class"] == "fact"
    assert doc["scope"] == ["project:memo"]
    assert doc["provenance"]["url"] == "https://example.invalid/x"


# --- MERGE ---

@pytest.mark.asyncio
async def test_near_identical_write_merges():
    existing = await seed("the ups is a cyberpower cp1500", cls="episodic", tags=["power"])
    r = await store_mod.store(
        req("the ups is a cyberpower cp1500", tags=["rack"]), provider=StubLLM([]))
    assert r.action == "merge"
    assert r.memo_id == existing
    assert r.merged_into == [existing]


@pytest.mark.asyncio
async def test_merge_unions_tags():
    existing = await seed("the ups is a cyberpower cp1500", cls="episodic", tags=["power"])
    await store_mod.store(req("the ups is a cyberpower cp1500", tags=["rack"]),
                          provider=StubLLM([]))
    doc = await db.get_current(None, existing)
    assert set(doc["tags"]) == {"power", "rack"}


# --- SUPERSEDE (authorized refutation) ---

@pytest.mark.asyncio
async def test_authorized_refutation_supersedes():
    old = await seed("the ups is a cyberpower cp1500", cls="fact")
    r = await store_mod.store(
        req("the ups is an apc back-ups 1500",
            operator_directive_ref={"from": "ben", "at": 1.0}),
        provider=StubLLM(["CONTRADICTS"]),
    )
    assert r.action == "supersede"
    assert r.superseded == old
    assert r.supersede_edge_id is not None
    # Old version is closed out; the lineage resolves forward to the new one.
    assert (await db.get_current(None, old))["id"] == r.memo_id


# --- CLARIFY then REJECT (operator ruling 2026-07-29) ---

@pytest.mark.asyncio
async def test_unauthorized_refutation_asks_first():
    """FR-015c's 409 is the OPENING move, not a hard stop."""
    old = await seed("the ups is a cyberpower cp1500", cls="fact")
    r = await store_mod.store(
        req("the ups is an apc back-ups 1500"), provider=StubLLM(["CONTRADICTS"]))
    assert r.action == "clarify"
    assert r.conflicting_memo_id == old
    assert r.clarification_token.startswith("clr-")
    assert r.expires_in == 300
    assert "authoriz" in r.prompt.lower()


@pytest.mark.asyncio
async def test_retry_without_authority_is_rejected():
    """...and the contract's 403 is the TERMINAL state."""
    old = await seed("the ups is a cyberpower cp1500", cls="fact")
    first = await store_mod.store(
        req("the ups is an apc back-ups 1500"), provider=StubLLM(["CONTRADICTS"]))
    second = await store_mod.store(
        req("the ups is an apc back-ups 1500",
            clarification_token=first.clarification_token,
            clarification_response={"answer": "no authority"}),
        provider=StubLLM(["CONTRADICTS"]),
    )
    assert second.action == "reject"
    assert second.conflicting_memo_id == old
    assert "operator authority" in second.reason
    assert second.how_to_authorize


@pytest.mark.asyncio
async def test_retry_with_authority_supersedes():
    """The recoverable path: the agent simply forgot to attach authority."""
    old = await seed("the ups is a cyberpower cp1500", cls="fact")
    first = await store_mod.store(
        req("the ups is an apc back-ups 1500"), provider=StubLLM(["CONTRADICTS"]))
    second = await store_mod.store(
        req("the ups is an apc back-ups 1500",
            clarification_token=first.clarification_token,
            operator_directive_ref={"from": "ben", "at": 2.0}),
        provider=StubLLM(["CONTRADICTS"]),
    )
    assert second.action == "supersede"
    assert second.superseded == old


@pytest.mark.asyncio
async def test_stale_or_foreign_token_is_rejected_not_honored():
    await seed("the ups is a cyberpower cp1500", cls="fact")
    r = await store_mod.store(
        req("the ups is an apc back-ups 1500", clarification_token="clr-bogus"),
        provider=StubLLM(["CONTRADICTS"]),
    )
    assert r.action == "reject"
    assert "token" in r.reason.lower()


@pytest.mark.asyncio
async def test_non_fact_memos_do_not_gate():
    """Only class=fact refutation requires authority (FR-015c)."""
    await seed("the ups is a cyberpower cp1500", cls="episodic")
    r = await store_mod.store(
        req("the ups situation is completely different now"),
        provider=StubLLM(["CONTRADICTS"]),
    )
    assert r.action in ("write-new", "merge")


# --- R-17 degrade paths: LLM down must never lose a write ---

@pytest.mark.asyncio
async def test_llm_unavailable_does_not_produce_a_spurious_reject():
    """The most important test here.

    An undetectable-because-degraded refutation must fall through to write-new
    with an auditor flag — never a 403. A mediator that rejects writes when its
    LLM is down is worse than the sprawl it was built to prevent.
    """
    await seed("the ups is a cyberpower cp1500", cls="fact")
    r = await store_mod.store(
        req("the ups is an apc back-ups 1500"), provider=StubLLM(replies=None))
    assert r.action == "write-new", f"degraded path must not reject, got {r.action}"
    assert r.memo_id


@pytest.mark.asyncio
async def test_degradation_is_recorded_in_the_audit_log():
    """Silent degradation is the failure mode — the auditor must be able to find it."""
    await seed("the ups is a cyberpower cp1500", cls="fact")
    await store_mod.store(req("the ups is an apc back-ups 1500"),
                          provider=StubLLM(replies=None))
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute(
        "SELECT anomaly_flags FROM mediator_audit_log "
        "WHERE mediator_kind='storage' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "storage call was not audited"
    assert "degraded" in row["anomaly_flags"]


# --- SPLIT (FR-015g) ---

@pytest.mark.asyncio
async def test_compound_content_asks_before_splitting():
    long_compound = "\n\n".join([
        "The ups is a cyberpower cp1500 in the rack.",
        "Separately, the rack door code is 4417.",
        "Also the rack was installed in March 2024 by the electrician.",
    ])
    r = await store_mod.store(req(long_compound),
                              provider=StubLLM(["SPLIT — 3 distinct facts"]))
    assert r.action == "clarify"
    assert r.clarification_token


@pytest.mark.asyncio
async def test_single_fact_is_not_split():
    long_single = "The ups is a cyberpower cp1500.\n\n" + ("detail. " * 200)
    r = await store_mod.store(req(long_single), provider=StubLLM(["SINGLE"]))
    assert r.action == "write-new"


@pytest.mark.asyncio
async def test_short_content_never_triggers_the_split_check():
    llm = StubLLM([])
    await store_mod.store(req("ups is fine"), provider=llm)
    assert llm.calls == [], "short content should not spend an LLM call"


# --- bypass ---

@pytest.mark.asyncio
async def test_bypass_mediator_skips_reconcile():
    """Operator escape hatch (e.g. bulk migration) — no merge, no LLM."""
    await seed("the ups is a cyberpower cp1500", cls="fact")
    llm = StubLLM([])
    r = await store_mod.store(
        req("the ups is a cyberpower cp1500", bypass_mediator=True), provider=llm)
    assert r.action == "write-new"
    assert llm.calls == []


# --- audit (FR-015f) ---

@pytest.mark.asyncio
async def test_every_call_is_audited_with_its_action():
    await store_mod.store(req("the ups is a cyberpower cp1500"), provider=StubLLM([]))
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute(
        "SELECT * FROM mediator_audit_log WHERE mediator_kind='storage' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["chosen_action"] == "write-new"
    assert row["calling_session_id"] == "cluster"
