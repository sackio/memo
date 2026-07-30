"""Clarification round-trip state. [001/FR-015d]

Pure in-process state — no DB, no LLM.
"""
import pytest

from memo import clarify


@pytest.fixture(autouse=True)
def _clean():
    clarify.clear()
    yield
    clarify.clear()


def open_one(session_id="cluster", **kw):
    kw.setdefault("prompt", "is this a supersession or a second interface?")
    kw.setdefault("request_snapshot", {"content": "original body"})
    return clarify.open_clarification(session_id=session_id, **kw)


def test_open_returns_prefixed_token():
    p = open_one()
    assert p.token.startswith("clr-")
    assert clarify.pending_count() == 1


def test_tokens_are_unique():
    assert len({open_one().token for _ in range(50)}) == 50


def test_resolve_returns_the_snapshot():
    p = open_one(request_snapshot={"content": "original body", "tags": ["k8s"]})
    got = clarify.resolve(p.token, session_id="cluster")
    assert got is not None
    assert got.request_snapshot["content"] == "original body"


def test_resolve_is_single_use():
    """One answer must not be able to drive two writes."""
    p = open_one()
    assert clarify.resolve(p.token, session_id="cluster") is not None
    assert clarify.resolve(p.token, session_id="cluster") is None


def test_resolve_unknown_token():
    assert clarify.resolve("clr-nope", session_id="cluster") is None


def test_resolve_rejects_a_different_session():
    """A token authorizes ONE pending write for ONE caller.

    Without this check, any agent that learned a token could complete another
    agent's clarification — including one gated on operator authority.
    """
    p = open_one(session_id="cluster")
    assert clarify.resolve(p.token, session_id="dojo") is None
    # ...and the rightful owner can still use it.
    assert clarify.resolve(p.token, session_id="cluster") is not None


def test_expired_token_does_not_resolve():
    p = open_one(ttl_s=0.0)
    assert clarify.resolve(p.token, session_id="cluster") is None


def test_expiry_is_ttl_based_not_immediate():
    p = open_one(ttl_s=300)
    assert not p.expired()
    assert p.expired(now=p.created_at + 301)


def test_default_ttl_matches_contract():
    """contracts/mediator-store.md advertises expires_in: 300."""
    assert clarify.DEFAULT_TTL_S == 300
    assert open_one().ttl_s == 300


def test_expired_entries_are_evicted():
    open_one(ttl_s=0.0)
    open_one(ttl_s=0.0)
    live = open_one(ttl_s=300)
    assert clarify.pending_count() == 1
    assert clarify.peek(live.token) is not None


def test_peek_does_not_consume():
    p = open_one()
    assert clarify.peek(p.token) is not None
    assert clarify.peek(p.token) is not None
    assert clarify.resolve(p.token, session_id="cluster") is not None


def test_table_is_bounded():
    """A burst of ambiguous writes nobody retries must not grow without bound."""
    for i in range(clarify.MAX_PENDING + 25):
        open_one(session_id=f"s{i}", ttl_s=300)
    assert clarify.pending_count() <= clarify.MAX_PENDING


def test_conflicting_memo_id_is_carried():
    p = open_one(conflicting_memo_id="abc-123")
    assert clarify.resolve(p.token, session_id="cluster").conflicting_memo_id == "abc-123"


def test_rounds_tracked_for_audit():
    """clarification_rounds is an audit-log column (FR-015f)."""
    assert open_one().rounds == 1
    assert open_one(rounds=2).rounds == 2
