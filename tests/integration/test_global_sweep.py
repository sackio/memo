"""Global auditor sweep. [001/FR-024 001/FR-025]"""
import pytest

from memo import db, flush as flush_mod, main
from memo.auditor import global_sweep
from memo.auditor.liveness import LivenessMonitor


@pytest.mark.asyncio
async def test_sweep_reaps_expired_flush_memos(embedding):
    """(c) — belt-and-braces with the 5-min reaper.

    Redundant on purpose: if the process was down or the reaper disabled,
    TTLs silently stopped being honored. A sweep that finds nothing is cheap.
    """
    r = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"open-tasks": "x"}, expires_at=1.0)
    out = await global_sweep.sweep()
    assert r["memo_ids"]["open-tasks"] in out["reaped"]


@pytest.mark.asyncio
async def test_sweep_coalesces_a_long_chain(embedding):
    """(d) — supersession is append-only, so a value corrected N times leaves
    N rows. The chain is history; the intermediate BODIES are not."""
    doc = await db.store(None, "value v0", None, [], {}, embedding)
    cur = doc
    ids = [doc]
    for i in range(1, 6):
        res = await db.supersede(None, cur, {"content": f"value v{i}", "tags": [],
                                             "metadata": {}},
                                 embedding, actor="operator:ben")
        cur = res["new_id"]
        ids.append(cur)

    out = await global_sweep.sweep()
    assert out["coalesced_chains"] == 1

    tip = await db.get(None, ids[-1])
    assert "coalesced_history" in tip["metadata"]
    assert "previous values" in tip["metadata"]["coalesced_history"]

    # Intermediate bodies compacted...
    mid = await db.get(None, ids[2])
    assert "coalesced" in mid["content"]
    # ...but the EDGES survive; the audit trail is the part worth keeping.
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM supersede_edges").fetchone()[0] == 5


@pytest.mark.asyncio
async def test_short_chains_are_left_alone(embedding):
    doc = await db.store(None, "v0", None, [], {}, embedding)
    await db.supersede(None, doc, {"content": "v1", "tags": [], "metadata": {}},
                       embedding, actor="operator:ben")
    out = await global_sweep.sweep()
    assert out["coalesced_chains"] == 0


@pytest.mark.asyncio
async def test_sweep_reports_stalled_shadows(embedding):
    """(a) — police the shadows. Content-based, not pid-based: a wedged
    session is still a running process."""
    class FakeShadow:
        def __init__(self):
            self.liveness = LivenessMonitor(stall_after_s=1.0)
            self.liveness.observe("session:dojo", "unchanged", now=0.0)
            self.liveness.observe("session:dojo", "unchanged", now=100.0)

    out = await global_sweep.sweep(shadows={"dojo": FakeShadow()})
    assert any(s["shadow"] == "dojo" for s in out["stalled_shadows"])


@pytest.mark.asyncio
async def test_sweep_survives_a_failing_stage(embedding, monkeypatch):
    """A scheduled job must not die on one bad stage."""
    async def boom(*a, **kw):
        raise RuntimeError("reap exploded")
    monkeypatch.setattr(global_sweep.reaper, "sweep_once", boom)
    out = await global_sweep.sweep()
    assert any("reap" in e for e in out["errors"])
    assert "duration_s" in out, "sweep still completed"


@pytest.mark.asyncio
async def test_sweep_endpoint(embedding):
    out = await main.auditor_global_sweep({"coalesce": False})
    assert "reaped" in out and "duration_s" in out
