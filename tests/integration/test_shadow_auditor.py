"""Shadow auditor behavior + boundaries. [001/FR-021 001/FR-022 001/FR-023 001/FR-025 001/FR-037]

Covers T108 (proposal on a synthetic anti-pattern) and T109 (composite bloat →
compact). The boundary tests matter most: the shadow has real autonomy, so what
it must REFUSE is as load-bearing as what it does.
"""
import json

import pytest

from memo import db
from memo.auditor import actions
from memo.auditor.shadow import (
    BLOAT_CACHE_READ_PER_DAY,
    BLOAT_TRANSCRIPT_BYTES,
    BLOAT_TURNS,
    RESPAWN_TRANSCRIPT_BYTES,
    Observation,
    ShadowAuditor,
)


class FakeController:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def compact(self, *, session_name, reason=""):
        self.calls.append(("compact", session_name, reason))
        return {"ok": True}

    async def respawn(self, *, session_name, preserve_transcript=False, reason=""):
        self.calls.append(("respawn", session_name, reason))
        return {"ok": True}

    async def spawn(self, **kw):
        return {"ok": True}

    async def inject(self, **kw):
        return {"ok": True}


def auditor(**kw):
    kw.setdefault("session_id", "dojo")
    kw.setdefault("agent_controller", FakeController())
    return ShadowAuditor(**kw)


def bloated(**kw):
    base = dict(session_id="dojo", transcript_bytes=BLOAT_TRANSCRIPT_BYTES + 1,
                turns=BLOAT_TURNS + 1,
                cache_read_tokens_today=BLOAT_CACHE_READ_PER_DAY + 1,
                idle_seconds=600)
    base.update(kw)
    return Observation(**base)


# --- C-10 composite bloat (T109 / FR-037) ---

def test_bloat_requires_all_three_signals():
    a = auditor()
    assert a.is_bloated(bloated()) is True
    assert a.is_bloated(bloated(turns=5)) is False
    assert a.is_bloated(bloated(transcript_bytes=1000)) is False
    assert a.is_bloated(bloated(cache_read_tokens_today=0)) is False


@pytest.mark.asyncio
async def test_bloated_and_idle_triggers_compact(embedding):
    ctrl = FakeController()
    a = auditor(agent_controller=ctrl)
    r = await a.maybe_compact(bloated(idle_seconds=600))
    assert r["compacted"] is True
    assert ctrl.calls and ctrl.calls[0][0] == "compact"


@pytest.mark.asyncio
async def test_bloated_but_busy_does_not_compact(embedding):
    """The idle gate is not politeness — compacting mid-turn destroys work."""
    ctrl = FakeController()
    a = auditor(agent_controller=ctrl)
    r = await a.maybe_compact(bloated(idle_seconds=1))
    assert r["compacted"] is False
    assert "not idle" in r["reason"]
    assert ctrl.calls == []


@pytest.mark.asyncio
async def test_compaction_is_logged_with_a_rationale(embedding):
    """FR-025: an action without a reason cannot be reviewed."""
    a = auditor()
    await a.maybe_compact(bloated(idle_seconds=600))
    log = await actions.recent(limit=10)
    entry = next(e for e in log if e["chosen_action"] == "compact")
    assert "composite bloat" in entry["query"]["rationale"]


@pytest.mark.asyncio
async def test_oversized_transcript_prefers_respawn(embedding):
    ctrl = FakeController()
    a = auditor(agent_controller=ctrl)
    out = await a.step(bloated(transcript_bytes=RESPAWN_TRANSCRIPT_BYTES + 1,
                               idle_seconds=600))
    assert out["respawn"]["respawned"] is True
    assert ctrl.calls[0][0] == "respawn"
    assert "compact" not in out, "respawn supersedes compaction"


# --- FR-023 boundary: constitutional memos are operator-owned ---

@pytest.mark.asyncio
async def test_shadow_can_modify_an_ordinary_memo(embedding):
    doc = await db.store(None, "the ups is a cyberpower", None, [], {}, embedding)
    a = auditor()
    r = await a.modify_memo(doc, content="the ups is a cyberpower cp1500",
                            rationale="added the model number")
    assert r["ok"] is True
    assert (await db.get_current(None, doc))["content"].endswith("cp1500")


@pytest.mark.asyncio
async def test_shadow_refuses_to_modify_a_constitutional_memo(embedding):
    """Principle V, enforced in code rather than trusted.

    An auditor able to edit constitutional memos could silently rewrite the
    standing rules force-injected into every session on the fleet.
    """
    doc = await db.store(None, "never rest with work pending", None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class='constitutional' WHERE id=?", (doc,))
    conn.commit()

    a = auditor()
    r = await a.modify_memo(doc, content="rest whenever you like",
                            rationale="trying to weaken a rule")
    assert r["ok"] is False
    assert "operator-owned" in r["error"]
    assert (await db.get_current(None, doc))["content"] == "never rest with work pending"


@pytest.mark.asyncio
async def test_refusal_is_itself_logged(embedding):
    """A blocked attempt is exactly what an operator review wants to see."""
    doc = await db.store(None, "a rule", None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class='constitutional' WHERE id=?", (doc,))
    conn.commit()
    await auditor().modify_memo(doc, content="x", rationale="attempt")
    log = await actions.recent(limit=10)
    assert any("REFUSED" in e["query"]["rationale"] for e in log)


# --- T108: proposal on a synthetic anti-pattern ---

@pytest.mark.asyncio
async def test_frustration_signals_detected():
    a = auditor()
    obs = Observation(session_id="dojo", recent_operator_messages=[
        "I already told you to use the full UUID",
        "looks good, thanks",
    ])
    hits = a.detect_frustration(obs)
    assert len(hits) == 1
    assert "already told you" in hits[0].lower()


@pytest.mark.asyncio
async def test_repeated_anti_pattern_can_be_proposed(embedding):
    a = auditor()
    r = await a.propose_rule(
        content="Always cite a full 36-char UUID in a rewarm pin.",
        evidence={"recurrence_count": 3,
                  "frustration_signals": ["I already told you"]},
    )
    assert r["status"] == "pending"
    # ...and still nothing constitutional was created.
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute(
        "SELECT COUNT(*) FROM documents WHERE class='constitutional'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_step_reports_without_acting_on_a_healthy_session(embedding):
    ctrl = FakeController()
    a = auditor(agent_controller=ctrl)
    out = await a.step(Observation(session_id="dojo", transcript_bytes=1000,
                                   turns=3, idle_seconds=5))
    assert out["bloated"] is False
    assert "compact" not in out and "respawn" not in out
    assert ctrl.calls == []
