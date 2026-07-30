"""Claude Code hook endpoints. [001/FR-017 001/FR-018 001/FR-036 001/FR-044]

FR-044 (the hook INTERFACE as a whole — all five fire points reachable with a
documented contract) is anchored here as well as on the endpoints. It had no
task of its own; see T086a.

Endpoint functions are called directly rather than through TestClient: these
are plain async functions, and going through the app would spin up the MCP
session manager and the TTL reaper for no added coverage.

The theme of every test here: a hook fires on the critical path of STARTING a
session, so it must never raise. Worst case it returns empty additionalContext
and the session starts without Layer 2.
"""
import json

import pytest

from memo import db, main
from memo.injection import posture


async def seed_rule(content, embedding, cls="constitutional"):
    doc_id = await db.store(None, content, None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class=?, scope=? WHERE id=?",
                 (cls, json.dumps(["global"]), doc_id))
    conn.commit()
    return doc_id


@pytest.mark.asyncio
async def test_session_start_returns_additional_context(embedding):
    await seed_rule("never rest with work pending", embedding)
    r = await main.hook_session_start({"session_id": "dojo"})
    assert r["fire_point"] == "session-start"
    assert "never rest with work pending" in r["additionalContext"]


@pytest.mark.asyncio
async def test_post_compact_returns_additional_context(embedding):
    await seed_rule("never rest with work pending", embedding)
    r = await main.hook_post_compact({"session_id": "dojo"})
    assert r["fire_point"] == "post-compact"
    assert "MEMO Layer 2 injection" in r["additionalContext"]


@pytest.mark.asyncio
async def test_post_compact_includes_previous_flush_generation(embedding):
    """C58 replacement: the post-compact session gets its flushed state back."""
    from memo import flush as flush_mod
    await flush_mod.flush(session_id="dojo", flush_generation=7,
                          slots={"open-tasks": "finish the T094 seam"})
    r = await main.hook_post_compact({"session_id": "dojo", "flush_generation": 7})
    assert "finish the T094 seam" in r["additionalContext"]


@pytest.mark.asyncio
async def test_hooks_never_raise_on_internal_failure(monkeypatch):
    """A broken injection build must not stop a session from starting."""
    async def boom(**kw):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(main.injection_set, "build", boom)
    r = await main.hook_session_start({"session_id": "dojo"})
    assert r["additionalContext"] == ""
    assert r["degraded"] is True


@pytest.mark.asyncio
async def test_opt_out_yields_no_context(embedding, monkeypatch):
    await seed_rule("a rule", embedding)
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_INJECTION: "1"})
    r = await main.hook_session_start({"session_id": "dojo", "pid": 4321})
    assert r["additionalContext"] == ""
    assert r["opt_out"] is True


@pytest.mark.asyncio
async def test_instructions_loaded_resolves_transclusions(embedding):
    doc_id = await db.store(None, "the door code is 4417", None, [], {}, embedding)
    r = await main.hook_instructions_loaded({
        "instruction_files": [
            {"path": "~/.claude/CLAUDE.md", "content": f"door rule: memo:{doc_id}"}
        ]
    })
    assert "the door code is 4417" in r["additionalContext"]
    assert r["transclusions"][0]["referenced_uuid"] == doc_id


@pytest.mark.asyncio
async def test_instructions_loaded_with_no_refs_is_empty():
    r = await main.hook_instructions_loaded({
        "instruction_files": [{"path": "CLAUDE.md", "content": "no refs here"}]})
    assert r["additionalContext"] == ""
    assert r["transclusions"] == []


@pytest.mark.asyncio
async def test_instructions_loaded_tolerates_bad_files():
    r = await main.hook_instructions_loaded({"instruction_files": [None, {}, {"path": "x"}]})
    assert r["additionalContext"] == ""


@pytest.mark.asyncio
async def test_session_end_acknowledges_without_context():
    """Nothing to inject into — the session is ending."""
    r = await main.hook_session_end({"session_id": "dojo"})
    assert r["acknowledged"] is True
    assert "additionalContext" not in r


# --- FR-044: the hook INTERFACE as a whole ---
#
# Added 2026-07-30. The Phase 5 gate requires FR-044 (endpoints for the whole
# hook chain) and no task had implemented or anchored it — PreCompact and
# SessionStop did not exist at all.

@pytest.mark.asyncio
async def test_pre_compact_flushes_synchronously(embedding):
    """FR-036: the flush must COMPLETE before compaction drops the context.

    Asserted by reading the memo back immediately after the call returns — if
    the hook were fire-and-forget this would race.
    """
    r = await main.hook_pre_compact({
        "session_id": "dojo", "flush_generation": 3,
        "slots": {"open-tasks": "finish the seam"},
    })
    assert r["flushed"] is True
    doc = await db.get_current(None, r["memo_ids"]["open-tasks"])
    assert doc["content"] == "finish the seam"


@pytest.mark.asyncio
async def test_pre_compact_failure_is_reported_not_swallowed(monkeypatch):
    """Opposite posture from the injecting hooks, deliberately.

    A silent flush failure means the session compacts believing its state was
    saved — worse than a visible error.
    """
    async def boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(main.flush_mod, "flush", boom)
    r = await main.hook_pre_compact({"session_id": "dojo", "flush_generation": 1,
                                     "slots": {"open-tasks": "x"}})
    assert r["flushed"] is False
    assert "error" in r


@pytest.mark.asyncio
async def test_pre_compact_requires_session_and_slots():
    assert (await main.hook_pre_compact({"session_id": "dojo"}))["flushed"] is False
    assert (await main.hook_pre_compact({"slots": {"a": "b"}}))["flushed"] is False


@pytest.mark.asyncio
async def test_pre_compact_then_post_compact_round_trip(embedding):
    """The C58 replacement, end to end through the hook chain."""
    await main.hook_pre_compact({
        "session_id": "dojo", "flush_generation": 9,
        "slots": {"in-flight-work": "background verify job running"},
    })
    r = await main.hook_post_compact({"session_id": "dojo", "flush_generation": 9})
    assert "background verify job running" in r["additionalContext"]


@pytest.mark.asyncio
async def test_session_stop_checkpoints_when_given_slots(embedding):
    r = await main.hook_session_stop({
        "session_id": "dojo", "flush_generation": 4,
        "slots": {"key-decisions": "chose content-word dedup"},
    })
    assert r["acknowledged"] is True and r["flushed"] is True


@pytest.mark.asyncio
async def test_session_stop_without_slots_just_acknowledges():
    r = await main.hook_session_stop({"session_id": "dojo"})
    assert r["acknowledged"] is True and r["flushed"] is False


@pytest.mark.asyncio
async def test_every_hook_in_the_chain_has_an_endpoint():
    """FR-044 names five fire points; all five must be reachable."""
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for p in ("/hooks/session-start", "/hooks/post-compact", "/hooks/pre-compact",
              "/hooks/session-stop", "/hooks/session-end",
              "/hooks/instructions-loaded"):
        assert p in paths, f"{p} missing"
