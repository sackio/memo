"""Bi-temporal read/write round-trips: supersede, as-of, current-filter. [001/FR-002 001/FR-003]

Versioning model under test (data-model.md): a supersession does not mutate a
row's id — each version is its own `documents` row, linked by a
`supersede_edges` entry. So any id in a chain is a valid handle to the lineage,
and the helpers must resolve the chain rather than doing a bare id lookup.
"""
import pytest

from memo import db


async def store(content, embedding, **kw):
    return await db.store(
        None, content, kw.get("title"), kw.get("tags", []),
        kw.get("metadata", {}), embedding,
    )


def raw_row(doc_id):
    """Read a row bypassing the bi-temporal helpers, to assert on stored state."""
    conn = db._get_or_create_conn(db.global_path())
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def new_memo(content="v2 body", **kw):
    payload = {"content": content, "tags": [], "metadata": {}}
    payload.update(kw)
    return payload


# --- Baseline write path ---

@pytest.mark.asyncio
async def test_new_write_sets_valid_from_to_now(embedding):
    """Regression: migration 001 adds valid_from NOT NULL DEFAULT 0.

    An INSERT that omits the column silently takes 0, which would make every
    new memo look valid from the epoch and break get_as_of. Found 2026-07-29
    when the first row in the fresh v2 DB had valid_from=0.
    """
    doc_id = await store("hello", embedding)
    row = raw_row(doc_id)
    assert row["valid_from"] > 0
    assert row["valid_from"] == pytest.approx(row["created_at"])


@pytest.mark.asyncio
async def test_new_write_is_current(embedding):
    doc_id = await store("hello", embedding)
    assert raw_row(doc_id)["valid_until"] is None
    current = await db.get_current(None, doc_id)
    assert current is not None
    assert current["id"] == doc_id


@pytest.mark.asyncio
async def test_v2_json_columns_are_decoded(embedding):
    """scope/derived_from are JSON-in-TEXT; callers must not receive raw strings."""
    doc_id = await store("hello", embedding)
    current = await db.get_current(None, doc_id)
    assert current["scope"] == ["global"]
    assert current["derived_from"] == []
    assert current["tags"] == []
    assert current["metadata"] == {}


# --- FR-003: supersede ---

@pytest.mark.asyncio
async def test_supersede_closes_old_and_opens_new(embedding):
    old_id = await store("v1 body", embedding)
    result = await db.supersede(
        None, old_id, new_memo("v2 body"), embedding, actor="operator:ben",
        reason="corrected",
    )
    assert result is not None
    new_id = result["new_id"]
    assert new_id != old_id

    old, new = raw_row(old_id), raw_row(new_id)
    assert old["valid_until"] == result["superseded_at"]
    assert new["valid_from"] == result["superseded_at"]
    assert new["valid_until"] is None
    assert new["content"] == "v2 body"


@pytest.mark.asyncio
async def test_supersede_writes_edge_with_audit_fields(embedding):
    old_id = await store("v1", embedding)
    result = await db.supersede(
        None, old_id, new_memo(), embedding, actor="auditor:memo",
        reason="stale", operator_directive_ref={"from": "ben", "at": 123.0},
    )
    conn = db._get_or_create_conn(db.global_path())
    edge = conn.execute(
        "SELECT * FROM supersede_edges WHERE id = ?", (result["edge_id"],)
    ).fetchone()
    assert edge["old_id"] == old_id
    assert edge["new_id"] == result["new_id"]
    assert edge["actor"] == "auditor:memo"
    assert edge["reason"] == "stale"
    assert '"from": "ben"' in edge["operator_directive_ref"]


@pytest.mark.asyncio
async def test_supersede_carries_v2_fields_onto_new_row(embedding):
    old_id = await store("v1", embedding)
    result = await db.supersede(
        None, old_id,
        new_memo(
            "v2",
            **{"class": "behavioral"},
            injection_mode="forcible-current-focus",
            scope=["project:memo"],
            provenance={"gmail_msg_id": "m-1"},
            derived_from=[old_id],
        ),
        embedding, actor="operator:ben",
    )
    new = await db.get_current(None, result["new_id"])
    assert new["class"] == "behavioral"
    assert new["injection_mode"] == "forcible-current-focus"
    assert new["scope"] == ["project:memo"]
    assert new["provenance"] == {"gmail_msg_id": "m-1"}
    assert new["derived_from"] == [old_id]


@pytest.mark.asyncio
async def test_supersede_defaults_when_v2_fields_omitted(embedding):
    old_id = await store("v1", embedding)
    result = await db.supersede(
        None, old_id, new_memo(), embedding, actor="mediator:auto",
    )
    new = await db.get_current(None, result["new_id"])
    assert new["class"] == "fact"
    assert new["injection_mode"] == "on-recall"
    assert new["scope"] == ["global"]
    assert new["provenance"] is None


@pytest.mark.asyncio
async def test_supersede_unknown_id_returns_none(embedding):
    assert await db.supersede(
        None, "no-such-id", new_memo(), embedding, actor="operator:ben"
    ) is None


@pytest.mark.asyncio
async def test_supersede_already_superseded_returns_none(embedding):
    """Refusing here is what stops a lineage forking into two current heads."""
    old_id = await store("v1", embedding)
    await db.supersede(None, old_id, new_memo("v2"), embedding, actor="operator:ben")
    assert await db.supersede(
        None, old_id, new_memo("v2-again"), embedding, actor="operator:ben"
    ) is None


@pytest.mark.asyncio
async def test_lineage_never_has_two_current_versions(embedding):
    """The core bi-temporal invariant, asserted over a 3-long chain."""
    a = await store("A", embedding)
    r1 = await db.supersede(None, a, new_memo("B"), embedding, actor="operator:ben")
    r2 = await db.supersede(None, r1["new_id"], new_memo("C"), embedding,
                            actor="operator:ben")
    conn = db._get_or_create_conn(db.global_path())
    ids = (a, r1["new_id"], r2["new_id"])
    current = conn.execute(
        "SELECT COUNT(*) FROM documents "
        "WHERE valid_until IS NULL AND id IN (?, ?, ?)", ids
    ).fetchone()[0]
    assert current == 1


# --- FR-002: get_current chain resolution ---

@pytest.mark.asyncio
async def test_get_current_follows_chain_from_superseded_id(embedding):
    """A caller holding a stale id still gets the current truth."""
    old_id = await store("v1", embedding)
    result = await db.supersede(None, old_id, new_memo("v2"), embedding,
                               actor="operator:ben")
    current = await db.get_current(None, old_id)
    assert current["id"] == result["new_id"]
    assert current["content"] == "v2"


@pytest.mark.asyncio
async def test_get_current_walks_full_chain(embedding):
    a = await store("A", embedding)
    r1 = await db.supersede(None, a, new_memo("B"), embedding, actor="operator:ben")
    r2 = await db.supersede(None, r1["new_id"], new_memo("C"), embedding,
                            actor="operator:ben")
    for handle in (a, r1["new_id"], r2["new_id"]):
        current = await db.get_current(None, handle)
        assert current["id"] == r2["new_id"], f"from handle {handle}"
        assert current["content"] == "C"


@pytest.mark.asyncio
async def test_get_current_unknown_id_returns_none():
    assert await db.get_current(None, "no-such-id") is None


# --- FR-002: get_as_of ---

@pytest.mark.asyncio
async def test_get_as_of_returns_old_version_before_supersede(embedding):
    old_id = await store("v1", embedding)
    before = raw_row(old_id)["valid_from"]
    result = await db.supersede(None, old_id, new_memo("v2"), embedding,
                               actor="operator:ben")

    as_of = await db.get_as_of(None, old_id, before)
    assert as_of["id"] == old_id
    assert as_of["content"] == "v1"

    # Same instant, queried via the NEW id — chain resolves backwards too.
    as_of_via_new = await db.get_as_of(None, result["new_id"], before)
    assert as_of_via_new["id"] == old_id


@pytest.mark.asyncio
async def test_get_as_of_at_supersede_instant_returns_new_version(embedding):
    """Window is half-open: valid_from <= t < valid_until.

    At exactly the transition instant the NEW version owns the time, so the two
    versions never both match and never both miss.
    """
    old_id = await store("v1", embedding)
    result = await db.supersede(None, old_id, new_memo("v2"), embedding,
                               actor="operator:ben")
    at = result["superseded_at"]
    as_of = await db.get_as_of(None, old_id, at)
    assert as_of["id"] == result["new_id"]
    assert as_of["content"] == "v2"


@pytest.mark.asyncio
async def test_get_as_of_future_returns_current(embedding):
    old_id = await store("v1", embedding)
    result = await db.supersede(None, old_id, new_memo("v2"), embedding,
                               actor="operator:ben")
    as_of = await db.get_as_of(None, old_id, result["superseded_at"] + 10_000)
    assert as_of["id"] == result["new_id"]


@pytest.mark.asyncio
async def test_get_as_of_before_creation_returns_none(embedding):
    doc_id = await store("v1", embedding)
    before_creation = raw_row(doc_id)["valid_from"] - 1
    assert await db.get_as_of(None, doc_id, before_creation) is None


@pytest.mark.asyncio
async def test_get_as_of_picks_correct_middle_version(embedding):
    """3-long chain: the as-of query must land on B, not A or C."""
    a = await store("A", embedding)
    r1 = await db.supersede(None, a, new_memo("B"), embedding, actor="operator:ben")
    r2 = await db.supersede(None, r1["new_id"], new_memo("C"), embedding,
                            actor="operator:ben")

    # Any instant strictly inside B's window.
    mid = (r1["superseded_at"] + r2["superseded_at"]) / 2
    assert r1["superseded_at"] <= mid < r2["superseded_at"]
    as_of = await db.get_as_of(None, a, mid)
    assert as_of["content"] == "B"


# --- Chain-walk robustness ---

@pytest.mark.asyncio
async def test_cyclic_edges_do_not_hang(embedding):
    """supersede_edges has no FK constraints, so a cycle is possible in principle.

    The chain walk must terminate on a malformed edge set rather than spin.
    """
    a = await store("A", embedding)
    r1 = await db.supersede(None, a, new_memo("B"), embedding, actor="operator:ben")
    conn = db._get_or_create_conn(db.global_path())
    # Hand-forge the back-edge B -> A that closes the loop.
    conn.execute(
        "INSERT INTO supersede_edges (old_id, new_id, superseded_at, actor) "
        "VALUES (?, ?, ?, ?)",
        (r1["new_id"], a, r1["superseded_at"] + 1, "test:forged"),
    )
    conn.commit()

    chain = db._lineage_chain(conn, a)
    assert len(chain) <= 2
    assert await db.get_current(None, a) is not None


# --- v2 JSON decoding on the read paths ---
#
# Regression guard. `_row_to_dict` only decodes v1's tags/metadata, so a read
# path still using it hands callers `scope` as the raw string '["global"]'.
# That fails SILENTLY and destructively: the recall mediator's ScopeFilter
# iterates the value, so a string makes it compare single CHARACTERS and
# quietly drop every candidate. Caught 2026-07-29 while wiring the mediator.

@pytest.mark.asyncio
async def test_search_decodes_v2_json_columns(embedding):
    await store("the barn k8s control plane lives here", embedding)
    rows = await db.search(
        db_path=None, embedding=embedding, limit=10, min_score=None,
        tags=[], after=None, before=None, min_tokens=None, max_tokens=None,
    )
    assert rows, "expected the stored doc back"
    doc = rows[0]["document"]
    assert doc["scope"] == ["global"], f"scope not decoded: {doc['scope']!r}"
    assert isinstance(doc["derived_from"], list)
    assert isinstance(doc["tags"], list)


@pytest.mark.asyncio
async def test_get_decodes_v2_json_columns(embedding):
    doc_id = await store("hello", embedding)
    doc = await db.get(None, doc_id)
    assert doc["scope"] == ["global"], f"scope not decoded: {doc['scope']!r}"
    assert isinstance(doc["derived_from"], list)


@pytest.mark.asyncio
async def test_list_decodes_v2_json_columns(embedding):
    await store("hello", embedding)
    docs = await db.list_docs(None, [], 10, None, None, None, None)
    assert docs
    assert docs[0]["scope"] == ["global"]


@pytest.mark.asyncio
async def test_copy_path_keeps_raw_json(embedding, tmp_path):
    """copy/move re-INSERT the row, so they must NOT decode.

    Pins the asymmetry so a future 'consistency' cleanup doesn't switch
    _sync_copy to _row_to_memo and start inserting dicts into TEXT columns.
    """
    doc_id = await store("copy me", embedding)
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    raw = db._row_to_dict(row)
    assert isinstance(raw["scope"], str), "copy path must see the stored TEXT form"
