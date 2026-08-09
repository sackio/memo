"""memory-on vs memory-off vs opted-out sessions. [001/FR-017]

C71: `CLAUDE_CODE_DISABLE_AUTO_MEMORY` does NOT mean inject less — it means
memo's Layer 2 is now the session's ONLY memory. Only the explicit
`MEMO_DISABLE_INJECTION` suppresses injection.
"""
import json

import pytest
import pytest_asyncio

from memo import db
from memo.injection import posture
from memo.injection import set as inj


async def seed(content, cls, embedding):
    doc_id = await db.store(None, content, None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET class=?, scope=? WHERE id=?",
                 (cls, json.dumps(["global"]), doc_id))
    conn.commit()
    return doc_id


# MUST be pytest_asyncio.fixture — a plain @pytest.fixture async generator
# is never awaited in strict mode. Same trap as the reaper suite; noted in
# tasks.md Phase 2 note 9 and walked into anyway.
@pytest_asyncio.fixture
async def corpus(embedding):
    await seed("never rest with work pending", "constitutional", embedding)
    await seed("always build in docker", "behavioral", embedding)
    await seed("ship the T094 seam", "goal", embedding)


async def build(pid=None):
    return await inj.build(session_id="dojo", agent_family="dojo", pid=pid,
                           use_cache=False)


@pytest.mark.asyncio
async def test_memory_on_session_gets_the_full_set(corpus, monkeypatch):
    monkeypatch.setattr(posture, "read_environ", lambda pid: {})
    r = await build(pid=1)
    assert r["memory_posture"] == "on"
    assert len(r["forcible_constitutional"]) == 2
    assert len(r["forcible_current_focus"]) == 1


@pytest.mark.asyncio
async def test_memory_off_session_still_gets_the_full_set(corpus, monkeypatch):
    """The C71 point. Injecting LESS here would leave the session with nothing."""
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_AUTO_MEMORY: "1"})
    r = await build(pid=2)
    assert r["memory_posture"] == "off"
    assert len(r["forcible_constitutional"]) == 2
    assert len(r["forcible_current_focus"]) == 1


@pytest.mark.asyncio
async def test_posture_changes_only_the_rendered_note(corpus, monkeypatch):
    monkeypatch.setattr(posture, "read_environ", lambda pid: {})
    on_text = inj.render(await build(pid=1))
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_AUTO_MEMORY: "1"})
    off_text = inj.render(await build(pid=2))

    assert "Memory posture: on" in on_text
    assert "native MEMORY.md also loads" in on_text
    assert "Memory posture: off" in off_text
    assert "this injection IS your memory" in off_text


@pytest.mark.asyncio
async def test_opted_out_session_gets_nothing(corpus, monkeypatch):
    """The ONE env var that actually suppresses injection."""
    monkeypatch.setattr(posture, "read_environ",
                        lambda pid: {posture.DISABLE_INJECTION: "1"})
    r = await build(pid=3)
    assert r["opt_out"] is True
    assert inj.render(r) == ""


@pytest.mark.asyncio
async def test_unreadable_environ_still_injects(corpus):
    """Fails open: a session whose environ we cannot read keeps its memory."""
    r = await build(pid=999_999_999)
    assert r.get("opt_out") is not True
    assert r["memory_posture"] == "on"
    assert len(r["forcible_constitutional"]) == 2
