"""Constitution proposal workflow. [001/FR-023 001/FR-025]

Principle V: the operator owns the constitution. The property under test is
that an auditor CANNOT create a constitutional memo — only propose one.
"""
import pytest

from memo import db, main
from memo.auditor import actions, proposals


async def file_one(**kw):
    kw.setdefault("proposed_by", "dojo-shadow-auditor")
    kw.setdefault("layer", "constitutional")
    kw.setdefault("proposed_content", "Always cite a full 36-char UUID in a rewarm pin.")
    kw.setdefault("evidence", {"recurrence_count": 3})
    return await proposals.propose(**kw)


@pytest.mark.asyncio
async def test_propose_creates_a_pending_proposal(embedding):
    r = await file_one()
    assert r["status"] == "pending"
    assert isinstance(r["proposal_id"], int)


@pytest.mark.asyncio
async def test_proposing_does_not_create_a_memo(embedding):
    """The core Principle V property."""
    await file_one()
    conn = db._get_or_create_conn(db.global_path())
    n = conn.execute("SELECT COUNT(*) FROM documents WHERE class='constitutional'"
                     ).fetchone()[0]
    assert n == 0, "a proposal must not put anything in documents"


@pytest.mark.asyncio
async def test_invalid_layer_rejected(embedding):
    with pytest.raises(ValueError):
        await file_one(layer="not-a-layer")


@pytest.mark.asyncio
async def test_empty_content_rejected(embedding):
    with pytest.raises(ValueError):
        await file_one(proposed_content="   ")


@pytest.mark.asyncio
async def test_list_pending(embedding):
    await file_one()
    await file_one(proposed_content="another rule")
    pending = await proposals.list_proposals("pending")
    assert len(pending) == 2
    assert all(p["status"] == "pending" for p in pending)


@pytest.mark.asyncio
async def test_accept_creates_the_constitutional_memo(embedding):
    r = await file_one()
    out = await proposals.resolve(proposal_id=r["proposal_id"], accept=True,
                                  resolved_by="operator:ben")
    assert out["ok"] is True and out["status"] == "accepted"

    memo = await db.get_current(None, out["resulting_memo_id"])
    assert memo["class"] == "constitutional"
    assert memo["injection_mode"] == "forcible-constitutional"
    # C45: constitutional memos require ratification metadata, and the Memo
    # model refuses to validate without it.
    assert memo["constitution_meta"]["incident_ref"].startswith("constitution-proposal:")


@pytest.mark.asyncio
async def test_reject_creates_nothing(embedding):
    r = await file_one()
    out = await proposals.resolve(proposal_id=r["proposal_id"], accept=False,
                                  resolved_by="operator:ben", note="too narrow")
    assert out["status"] == "rejected"
    assert out["resulting_memo_id"] is None
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_double_accept_is_refused(embedding):
    """The pending-only guard: otherwise one proposal makes two memos."""
    r = await file_one()
    first = await proposals.resolve(proposal_id=r["proposal_id"], accept=True,
                                    resolved_by="operator:ben")
    second = await proposals.resolve(proposal_id=r["proposal_id"], accept=True,
                                     resolved_by="operator:ben")
    assert first["ok"] is True
    assert second["ok"] is False and "already" in second["error"]


@pytest.mark.asyncio
async def test_resolving_unknown_proposal(embedding):
    out = await proposals.resolve(proposal_id=99999, accept=True,
                                  resolved_by="operator:ben")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_accepted_behavioral_layer_is_not_forcibly_constitutional(embedding):
    r = await file_one(layer="behavioral", proposed_content="prefer docker")
    out = await proposals.resolve(proposal_id=r["proposal_id"], accept=True,
                                  resolved_by="operator:ben")
    memo = await db.get_current(None, out["resulting_memo_id"])
    assert memo["class"] == "behavioral"
    assert memo["injection_mode"] == "on-recall"
    assert memo["constitution_meta"] is None


# --- endpoints ---

@pytest.mark.asyncio
async def test_endpoint_round_trip(embedding):
    created = await main.constitution_propose({
        "proposed_by": "dojo-shadow", "layer": "constitutional",
        "proposed_content": "A rule worth having.", "evidence": {},
    })
    listed = await main.constitution_list(status="pending", limit=10)
    assert any(p["id"] == created["proposal_id"] for p in listed["proposals"])

    resolved = await main.constitution_resolve(
        {"proposal_id": created["proposal_id"], "accept": True,
         "resolved_by": "operator:ben"})
    assert resolved["status"] == "accepted"


@pytest.mark.asyncio
async def test_resolve_endpoint_409s_on_already_resolved(embedding):
    from fastapi import HTTPException
    created = await main.constitution_propose({
        "proposed_by": "x", "layer": "goal", "proposed_content": "y", "evidence": {}})
    await main.constitution_resolve({"proposal_id": created["proposal_id"],
                                     "accept": False, "resolved_by": "ben"})
    with pytest.raises(HTTPException) as exc:
        await main.constitution_resolve({"proposal_id": created["proposal_id"],
                                         "accept": True, "resolved_by": "ben"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_proposals_are_logged_for_review(embedding):
    """FR-025: everything the auditor does must be reviewable after the fact."""
    from memo.auditor.shadow import ShadowAuditor
    a = ShadowAuditor(session_id="dojo")
    await a.propose_rule(content="a proposed rule", evidence={"recurrence_count": 2})
    log = await actions.recent(limit=10)
    assert any(e["chosen_action"] == "propose" for e in log)
