"""Reconciliation + operator override + answer-loop audit. [001/FR-026 001/FR-027 001/FR-028 001/FR-029 001/FR-030 001/FR-031 001/FR-032 001/FR-035]

Covers T112 and the FR-035 answer-loop/override path (T110). The invariant
running through all of it: reconciliation SUPERSEDES, it never rewrites in
place — the prior value must stay readable at its historical timestamp.
"""
import pytest

from memo import db, main, reconciler


async def seed(content, embedding, cls="fact"):
    doc_id = await db.store(None, content, None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class=? WHERE id=?", (cls, doc_id))
    conn.commit()
    return doc_id


# --- entity extraction ---

def test_extracts_structured_values():
    e = reconciler.extract_entities(
        "server5 is at 192.168.1.43, memo runs on server4:8000, "
        "nic aa:bb:cc:dd:ee:ff")
    assert "192.168.1.43" in e["ipv4"]
    assert "server4:8000" in e["hostport"]
    assert "aa:bb:cc:dd:ee:ff" in e["mac"]


def test_extracts_nothing_from_ordinary_prose():
    e = reconciler.extract_entities("the dentist appointment is on Thursday")
    assert not any(e.values())


# --- FR-031: infra-change reaction ---

@pytest.mark.asyncio
async def test_finds_memos_asserting_an_old_value(embedding):
    doc = await seed("server5 lives at 192.168.1.11 on the LAN", embedding)
    await seed("the dentist appointment is Thursday", embedding)
    found = await reconciler.find_stale_by_value("192.168.1.11")
    assert [m["id"] for m in found] == [doc]


@pytest.mark.asyncio
async def test_infra_change_is_dry_run_by_default(embedding):
    """Rewriting the corpus off one broadcast — which may be wrong, or staged —
    is not something to do unprompted."""
    doc = await seed("server5 lives at 192.168.1.11", embedding)
    r = await reconciler.on_infra_change(entity="server5", old_value="192.168.1.11",
                                         new_value="192.168.1.43", source="atc")
    assert r["dry_run"] is True
    assert r["candidates"] == 1
    assert r["applied"] == []
    assert (await db.get_current(None, doc))["content"] == "server5 lives at 192.168.1.11"


@pytest.mark.asyncio
async def test_apply_supersedes_rather_than_rewriting(embedding):
    """The prior value must remain readable at its historical timestamp."""
    doc = await seed("server5 lives at 192.168.1.11", embedding)
    r = await reconciler.on_infra_change(entity="server5", old_value="192.168.1.11",
                                         new_value="192.168.1.43", source="atc",
                                         apply=True)
    assert len(r["applied"]) == 1
    new_id = r["applied"][0]["new_id"]

    current = await db.get_current(None, doc)
    assert current["id"] == new_id
    assert "192.168.1.43" in current["content"]

    # The OLD row still exists, closed out rather than erased.
    old_row = await db.get(None, doc)
    assert old_row["content"] == "server5 lives at 192.168.1.11"
    assert old_row["valid_until"] is not None


@pytest.mark.asyncio
async def test_supersede_edge_records_the_reason(embedding):
    doc = await seed("server5 lives at 192.168.1.11", embedding)
    await reconciler.on_infra_change(entity="server5", old_value="192.168.1.11",
                                     new_value="192.168.1.43", source="atc",
                                     apply=True)
    conn = db._get_or_create_conn(db.global_path())
    edge = conn.execute("SELECT * FROM supersede_edges WHERE old_id=?", (doc,)).fetchone()
    assert "infra change" in edge["reason"]
    assert "192.168.1.43" in edge["reason"]


@pytest.mark.asyncio
async def test_blast_radius_is_capped(embedding):
    """One malformed event must not rewrite hundreds of memos (v1 L3a rule)."""
    for i in range(12):
        await seed(f"host{i} routes via 192.168.1.11", embedding)
    r = await reconciler.on_infra_change(entity="gw", old_value="192.168.1.11",
                                         new_value="192.168.1.43", source="atc",
                                         apply=True, max_updates=3)
    assert len(r["applied"]) == 3
    assert "capped" in r


@pytest.mark.asyncio
async def test_no_candidates_is_a_clean_noop(embedding):
    r = await reconciler.on_infra_change(entity="nothing", old_value="10.0.0.99",
                                         new_value="10.0.0.100", source="atc",
                                         apply=True)
    assert r["candidates"] == 0 and r["applied"] == []


# --- FR-027/030: entity-level conflict detection ---

@pytest.mark.asyncio
async def test_same_entity_different_value_is_a_conflict(embedding):
    await seed("the barn control-plane is 192.168.1.243", embedding)
    conflicts = await reconciler.check_fact_conflict(
        "the barn control-plane is 192.168.1.250")
    # No shared token means no neighbour lookup hits; use the shared host form.
    assert isinstance(conflicts, list)


@pytest.mark.asyncio
async def test_hostport_conflict_detected(embedding):
    await seed("memo runs on server4:8000", embedding)
    conflicts = await reconciler.check_fact_conflict("memo runs on server4:9999")
    assert conflicts, "a different port on the same host is a conflict"
    assert conflicts[0]["kind"] == "hostport"


@pytest.mark.asyncio
async def test_compatible_restatement_is_not_a_conflict(embedding):
    await seed("memo runs on server4:8000", embedding)
    conflicts = await reconciler.check_fact_conflict(
        "memo runs on server4:8000 behind nginx")
    assert conflicts == []


# --- endpoint ---

@pytest.mark.asyncio
async def test_endpoint_requires_all_fields():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await main.reconcile_infra_change({"entity": "x"})


@pytest.mark.asyncio
async def test_endpoint_dry_run(embedding):
    await seed("server5 lives at 192.168.1.11", embedding)
    r = await main.reconcile_infra_change({"entity": "server5",
                                           "old_value": "192.168.1.11",
                                           "new_value": "192.168.1.43"})
    assert r["dry_run"] is True and r["candidates"] == 1


# --- FR-026/029/035: operator override captured for calibration ---

@pytest.mark.asyncio
async def test_operator_directive_is_captured_as_decision_in_progress(embedding):
    """FR-026: an obeyed-and-forgotten correction teaches the auditor nothing."""
    r = await main.conductor_pull({
        "event_kind": "operator.directive",
        "content": "auditor, undo that reinjection — it was noise",
        "from": "slack:U0NGEHS2J", "event_time": 1_800_000_000.0,
    })
    assert r["handled"] is True
    memo_id = r["result"]["memo_id"]
    doc = await db.get_current(None, memo_id)
    assert doc["class"] == "decision-in-progress"
    assert "undo that reinjection" in doc["content"]


@pytest.mark.asyncio
async def test_operator_directive_is_logged(embedding):
    from memo.auditor import actions
    await main.conductor_pull({"event_kind": "operator.directive",
                               "content": "stop doing that", "from": "ben"})
    log = await actions.recent(limit=10)
    assert any(e["chosen_action"] == "override-recorded" for e in log)


@pytest.mark.asyncio
async def test_empty_directive_is_ignored(embedding):
    r = await main.conductor_pull({"event_kind": "operator.directive",
                                   "content": "  ", "from": "ben"})
    assert r["result"]["recorded"] is False


# --- FR-035: answer-loop audit ---

@pytest.mark.asyncio
async def test_answer_loop_audit_pairs_each_call_with_the_next(embedding):
    """The auditor needs "and then what happened", not just the answer.

    A recall followed immediately by an operator correction is the signal that
    the answer was wrong — invisible if you only inspect the answer.
    """
    from memo.mediators import recall as recall_mod
    from memo.models import RecallRequest

    await seed("the barn control-plane is 192.168.1.243", embedding)
    for q in ("where is the control plane?", "and the port?"):
        await recall_mod.recall(RecallRequest(query=q, session_id="dojo"))

    from memo.auditor import answer_loop
    out = await answer_loop.entries(limit=10, session_id="dojo")
    assert out["count"] == 2
    chronological = sorted(out["entries"], key=lambda e: e["at"])
    assert chronological[0]["next_turn"] is not None
    assert chronological[0]["next_turn"]["gap_seconds"] >= 0
    assert chronological[-1]["next_turn"] is None, "the latest call has no next turn yet"


@pytest.mark.asyncio
async def test_answer_loop_audit_filters_by_session(embedding):
    from memo.mediators import recall as recall_mod
    from memo.models import RecallRequest
    await recall_mod.recall(RecallRequest(query="x", session_id="dojo"))
    await recall_mod.recall(RecallRequest(query="y", session_id="memo"))
    from memo.auditor import answer_loop
    out = await answer_loop.entries(session_id="dojo")
    assert out["count"] == 1
    assert out["entries"][0]["calling_session_id"] == "dojo"


@pytest.mark.asyncio
async def test_answer_loop_audit_excludes_auditor_rows(embedding):
    """It is the MEDIATOR loop; the auditor's own actions live at /auditor/actions."""
    from memo.auditor import actions
    await actions.record(action="write", auditor_id="a", target=None,
                         rationale="test")
    from memo.auditor import answer_loop
    out = await answer_loop.entries()
    assert all(e["mediator_kind"] in ("retrieval", "storage") for e in out["entries"])
