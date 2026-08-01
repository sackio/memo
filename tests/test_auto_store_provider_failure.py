"""A provider failure must never be rendered as a skip. [0.3.7]

**The fourth instance of one shape in this repo.** `test_response_ambiguity.py`
already documents three (memo_context doc_count:0, search_multi [], memo_update
null) and its own docstring calls it "third instance of one shape in a day". This
is the same bug in the auto-store path, and it is the worst-consequence one so far.

Reported by the `embeddings` seat 2026-08-01 and confirmed against the source:

  - `analyze_for_store()` caught every exception into
    `{"should_store": False, "reason": f"analysis error: {e}"}`
  - `/auto-store` turned that into **HTTP 200 `action="skipped"`** — byte-identical
    in shape to "this exchange wasn't worth storing"
  - `hooks/memo-auto-store.sh` read `.action // "skipped"`, so a dead server, a
    malformed reply and a deliberate skip were one state

An agent banks its state at the end of a turn, sees no error it would notice, and
compacts or respawns believing that state is durable. Nothing was written and
nothing is recoverable. This is the mechanism that made 2026-07-31 invisible.

The reason string always carried the truth. The `action` did not, and the hook
only reads `action` — so the distinction had to move to where the caller looks.

**402 is called out separately from 429 on purpose.** OpenRouter signals exhausted
credit as 402 Payment Required, not as a rate limit, so a retry table that only
knows 429 sails straight past it. 402 is not transient: retrying cannot fix it.
"""

import asyncio

import pytest

from memo import auto_store, main


def _run(coro):
    return asyncio.run(coro)


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


@pytest.fixture
def no_similar(monkeypatch):
    async def _embed(_text):
        return [0.0] * 8

    async def _search(**_kwargs):
        return []

    monkeypatch.setattr(main.embeddings, "embed", _embed)
    monkeypatch.setattr(main.db, "search", _search)


def _req(content="a substantial exchange worth analysing"):
    from memo.models import AutoStoreRequest
    return AutoStoreRequest(content=content, session_id="s-1")


# --- The headline: a 402 must not read as "skipped" ---

def test_a_402_is_not_reported_as_skipped(monkeypatch, no_similar):
    async def _boom(_content):
        return {"error": auto_store._provider_error(_HTTPError(402))}

    monkeypatch.setattr("memo.auto_store.analyze_for_store", _boom)

    resp = _run(main.auto_store(_req()))

    assert resp.action == "error", (
        "a provider failure rendered as 'skipped' is indistinguishable from "
        "'not worth storing' — this is the data-loss bug")
    assert resp.action != "skipped"
    assert resp.id is None


def test_a_402_is_marked_payment_required_and_not_retryable(monkeypatch, no_similar):
    async def _boom(_content):
        return {"error": auto_store._provider_error(_HTTPError(402))}

    monkeypatch.setattr("memo.auto_store.analyze_for_store", _boom)
    resp = _run(main.auto_store(_req()))

    assert resp.error_kind == "payment_required"
    assert resp.retryable is False, "402 needs a human top-up; retrying cannot fix it"


def test_a_429_is_distinct_from_a_402_and_is_retryable(monkeypatch, no_similar):
    async def _boom(_content):
        return {"error": auto_store._provider_error(_HTTPError(429))}

    monkeypatch.setattr("memo.auto_store.analyze_for_store", _boom)
    resp = _run(main.auto_store(_req()))

    assert resp.error_kind == "rate_limited"
    assert resp.retryable is True


def test_a_genuine_skip_still_reads_as_skipped(monkeypatch, no_similar):
    """The fix must not make every skip look like a failure."""
    async def _skip(_content):
        return {"should_store": False, "reason": "generic chitchat"}

    monkeypatch.setattr("memo.auto_store.analyze_for_store", _skip)
    resp = _run(main.auto_store(_req()))

    assert resp.action == "skipped"
    assert resp.error_kind is None
    assert resp.retryable is False


# --- The quieter half: a failed merge must not silently duplicate ---

def test_a_failed_merge_analysis_does_not_fall_through_to_create(monkeypatch):
    """Degrading to `create` duplicates the memo we just found.

    The caller asked "merge or not?"; a provider failure is not an answer, and
    guessing resolves it the destructive way.
    """
    async def _store_ok(_content):
        return {"should_store": True, "content": "extracted", "title": "T", "tags": []}

    async def _embed(_text):
        return [0.0] * 8

    async def _search(**_kwargs):
        return [{"document": {"id": "existing-1", "content": "old", "title": "T",
                              "tags": []}, "score": 0.99}]

    stored = {"n": 0}

    async def _store(**_kwargs):
        stored["n"] += 1
        return "new-id"

    async def _merge_boom(_a, _b):
        return {"error": auto_store._provider_error(_HTTPError(402))}

    monkeypatch.setattr("memo.auto_store.analyze_for_store", _store_ok)
    monkeypatch.setattr("memo.auto_store.analyze_for_merge", _merge_boom)
    monkeypatch.setattr(main.embeddings, "embed", _embed)
    monkeypatch.setattr(main.db, "search", _search)
    monkeypatch.setattr(main.db, "store", _store)

    resp = _run(main.auto_store(_req()))

    assert resp.action == "error"
    assert stored["n"] == 0, "a failed merge analysis must not create a duplicate"


# --- The tests that actually fail against the old code ---
#
# Everything above monkeypatches `analyze_for_store` wholesale, so it exercises
# the ENDPOINT's handling of an error dict and nothing else. Reverting the fix
# leaves all of it green — checked, 2026-08-01. That is the "a test that never
# failed detects nothing" trap, and these two close it: they drive the REAL
# function with a provider that raises, which is where the bug lived.

def test_analyze_for_store_returns_an_error_not_a_should_store_false(monkeypatch):
    """The regression test. Fails against the old `{"should_store": False, ...}`."""
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**_kwargs):
                    raise _HTTPError(402)

    monkeypatch.setattr(auto_store, "_client", _Boom)
    out = _run(auto_store.analyze_for_store("some content"))

    assert "error" in out, (
        "a provider failure must not come back as a normal analysis result — "
        "the old code returned should_store=False, which /auto-store renders "
        "as action='skipped' and the caller reads as 'nothing worth storing'")
    assert out["error"]["kind"] == "payment_required"
    assert out.get("should_store") is not False or "error" in out


def test_analyze_for_merge_returns_an_error_not_a_create(monkeypatch):
    """Fails against the old `{"action": "create", ...}` fallback."""
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**_kwargs):
                    raise _HTTPError(500)

    monkeypatch.setattr(auto_store, "_client", _Boom)
    out = _run(auto_store.analyze_for_merge("existing", "new"))

    assert "error" in out
    assert out.get("action") != "create", (
        "degrading to 'create' on a provider failure duplicates the memo we "
        "just found")


# --- The status classifier itself ---

@pytest.mark.parametrize("status,kind,retryable", [
    (402, "payment_required", False),
    (429, "rate_limited", True),
    (500, "provider_error", False),
    (None, "provider_error", False),
])
def test_provider_error_classifies_status(status, kind, retryable):
    exc = _HTTPError(status) if status else ValueError("no status at all")
    err = auto_store._provider_error(exc)
    assert err["kind"] == kind
    assert err["retryable"] is retryable
    assert err["detail"], "the detail string is what lands in the durable log"
