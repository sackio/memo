"""Provider abstractions: conductor, agent-controller, LLM. [001/FR-041 001/FR-042 001/FR-042a 001/FR-043 001/FR-045 001/FR-046]

Covers T085b/T087/T088/T089/T090. No network: the ATC adapters are exercised
against monkeypatched transports, and the null adapters need none.
"""
import asyncio

import pytest

from memo import main
from memo.providers import registry
from memo.providers.agent_controller.agents_supervisor import AgentsSupervisorController
from memo.providers.conductor.atc import ATCConductor
from memo.providers.conductor.base import EVENT_KINDS, Event
from memo.providers.llm.null import NullLLMProvider
from memo.providers.null import NullAgentController, NullConductor


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


# --- Event wrapper (FR-041) ---

def test_event_wire_shape():
    e = Event(event_kind="memo.stored", payload={"memo_id": "x"},
              target="dojo", priority="warning", delivery_mode="beacon")
    w = e.to_wire()
    assert w["event_kind"] == "memo.stored"
    assert w["source"] == "memo"
    assert w["delivery_hints"] == {"target": "dojo", "priority": "warning",
                                   "delivery_mode": "beacon"}
    assert w["event_id"] and w["event_time"] > 0


def test_all_contract_event_kinds_declared():
    for k in ("memo.stored", "memo.superseded", "mediator.anomaly",
              "auditor.recommendation", "injection.updated",
              "time_scope.enter", "time_scope.exit"):
        assert k in EVENT_KINDS


# --- Null providers (FR-045) ---

@pytest.mark.asyncio
async def test_null_conductor_logs_and_drops():
    c = NullConductor()
    assert await c.emit(Event(event_kind="memo.stored", payload={})) is True
    assert len(c.emitted) == 1
    assert c.dropped == 1


@pytest.mark.asyncio
async def test_null_agent_controller_refuses_every_op():
    a = NullAgentController()
    for coro in (a.spawn(session_name="x"), a.respawn(session_name="x"),
                 a.compact(session_name="x"), a.inject(session_name="x", content="y")):
        r = await coro
        assert r["ok"] is False
        assert "would_have" in r


@pytest.mark.asyncio
async def test_null_llm_reports_unavailable():
    p = NullLLMProvider()
    assert await p.complete("anything") is None
    assert await p.available() is False


# --- Registry selection (FR-045) ---

def test_registry_selects_configured_providers(monkeypatch):
    from memo.config import settings
    monkeypatch.setattr(settings, "memo_conductor_provider", "null")
    monkeypatch.setattr(settings, "memo_agent_controller_provider", "null")
    registry.reset()
    assert registry.get_conductor().name == "null"
    assert registry.get_agent_controller().name == "null"


def test_unknown_provider_falls_back_to_null(monkeypatch):
    """A typo'd env var must not crash-loop the container."""
    from memo.config import settings
    monkeypatch.setattr(settings, "memo_conductor_provider", "not-a-provider")
    registry.reset()
    assert registry.get_conductor().name == "null"


def test_registry_memoizes(monkeypatch):
    from memo.config import settings
    monkeypatch.setattr(settings, "memo_conductor_provider", "null")
    registry.reset()
    assert registry.get_conductor() is registry.get_conductor()


# --- ATC conductor: isolation properties (FR-041) ---

@pytest.mark.asyncio
async def test_emit_never_blocks_on_the_hot_path():
    """emit() enqueues and returns; delivery happens on a worker.

    A mediator response must never wait on a message bus.
    """
    c = ATCConductor(url="http://127.0.0.1:9")   # nothing listening
    assert await c.emit(Event(event_kind="memo.stored", payload={})) is True
    assert c._queue.qsize() == 1                  # queued, not delivered


@pytest.mark.asyncio
async def test_queue_is_bounded_and_drops_oldest():
    """An unreachable Conductor must not become a memory leak."""
    c = ATCConductor(url="http://127.0.0.1:9")
    for i in range(c._queue.maxsize + 25):
        await c.emit(Event(event_kind="memo.stored", payload={"n": i}))
    assert c._queue.qsize() <= c._queue.maxsize
    assert c.dropped >= 25


@pytest.mark.asyncio
async def test_delivery_failure_does_not_raise(monkeypatch):
    c = ATCConductor(url="http://127.0.0.1:9")
    monkeypatch.setattr("memo.providers.conductor.atc.BACKOFF_BASE_S", 0.001)
    ok = await c._deliver(Event(event_kind="memo.stored", payload={}))
    assert ok is False


@pytest.mark.asyncio
async def test_endpoint_selection_by_delivery_mode():
    c = ATCConductor()
    assert c._endpoint(Event(event_kind="k", payload={}, delivery_mode="beacon")) == "/beacons"
    assert c._endpoint(Event(event_kind="k", payload={}, delivery_mode="board-post")) == "/statuses"
    assert c._endpoint(Event(event_kind="k", payload={}, delivery_mode="message")) == "/messages"


# --- Agent controller (FR-043) ---

@pytest.mark.asyncio
async def test_agent_controller_never_raises_when_unreachable():
    """These are requests about OTHER processes; a refusal is an ordinary outcome."""
    a = AgentsSupervisorController(url="http://127.0.0.1:9")
    r = await a.respawn(session_name="memo-llm", reason="test")
    assert r["ok"] is False
    assert r["op"] == "respawn"


# The claude_session adapter (R-17) was removed 2026-07-30 along with its
# tests. It never served a request: `memo_llm_provider` defaulted to "null"
# from the day it was written, and the `memo-llm` session it dialled was never
# created on this fleet. ~180 lines of adapter and ~100 of tests exercising an
# integration that had never run once.
