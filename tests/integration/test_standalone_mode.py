"""Standalone mode — memo with no fleet around it. [001/FR-045]

FR-045: with every provider set to `null`, CRUD and BOTH mediators must still
work, and integration features must WARN-log what they would have done rather
than failing or silently no-op'ing.

This is the deployment where someone runs memo on a laptop with no ATC, no
supervisor, and no memo-llm session.
"""
import logging

import pytest

from memo import db
from memo.config import settings
from memo.mediators import recall as recall_mod
from memo.mediators import store as store_mod
from memo.models import MediatorStoreRequest, Provenance, RecallRequest
from memo.providers import registry
from memo.providers.conductor.base import Event
from memo.providers.llm import get_llm_provider, reset_llm_provider


@pytest.fixture(autouse=True)
def standalone(monkeypatch):
    monkeypatch.setattr(settings, "memo_conductor_provider", "null")
    monkeypatch.setattr(settings, "memo_agent_controller_provider", "null")
    monkeypatch.setattr(settings, "memo_llm_provider", "null")
    registry.reset()
    reset_llm_provider()
    yield
    registry.reset()
    reset_llm_provider()


def req(content, **kw):
    kw.setdefault("session_id", "solo")
    kw.setdefault("provenance", Provenance(url="https://example.invalid/x"))
    return MediatorStoreRequest(content=content, **kw)


@pytest.mark.asyncio
async def test_store_mediator_works_standalone():
    r = await store_mod.store(req("the ups is a cyberpower cp1500"))
    assert r.action == "write-new"
    assert r.memo_id


@pytest.mark.asyncio
async def test_recall_mediator_works_standalone():
    await store_mod.store(req("the ups is a cyberpower cp1500"))
    r = await recall_mod.recall(RecallRequest(query="what ups do we have?",
                                              session_id="solo"))
    assert r.answer is not None
    assert r.citations


@pytest.mark.asyncio
async def test_crud_works_standalone(embedding):
    doc_id = await db.store(None, "plain crud", None, [], {}, embedding)
    assert (await db.get(None, doc_id))["content"] == "plain crud"
    assert await db.delete(None, doc_id) is True


@pytest.mark.asyncio
async def test_injection_set_works_standalone():
    from memo.injection import set as inj
    r = await inj.build(session_id="solo", agent_family="solo", use_cache=False)
    assert "forcible_constitutional" in r
    assert r["token_budget_ceiling"] > 0


@pytest.mark.asyncio
async def test_llm_is_null_and_mediators_degrade_gracefully():
    """No memo-llm session exists, so every LLM path takes the degrade branch."""
    assert get_llm_provider().name == "null"
    r = await store_mod.store(req("the ups is a cyberpower cp1500"))
    assert r.action == "write-new"      # not an error


@pytest.mark.asyncio
async def test_conductor_warn_logs_what_it_would_have_sent(caplog):
    """FR-045's 'would have fired' requirement.

    A silent no-op would make a misconfigured deployment look healthy while
    every notification vanished.
    """
    c = registry.get_conductor()
    assert c.name == "null"
    with caplog.at_level(logging.WARNING):
        await c.emit(Event(event_kind="memo.stored", payload={"memo_id": "abc"},
                           target="dojo"))
    assert any("would have emitted" in r.message.lower() or
               "would have emitted" in r.getMessage().lower()
               for r in caplog.records), caplog.text


@pytest.mark.asyncio
async def test_agent_controller_warn_logs_and_refuses(caplog):
    a = registry.get_agent_controller()
    assert a.name == "null"
    with caplog.at_level(logging.WARNING):
        r = await a.respawn(session_name="memo-llm", reason="test")
    assert r["ok"] is False
    assert any("would have requested" in rec.getMessage().lower()
               for rec in caplog.records), caplog.text


@pytest.mark.asyncio
async def test_standalone_is_reported_by_the_providers_endpoint():
    from memo import main
    r = await main.providers_status()
    assert r["standalone"] is True
