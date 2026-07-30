"""Claude Code hook endpoints. [001/FR-017 001/FR-018 001/FR-036]

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
