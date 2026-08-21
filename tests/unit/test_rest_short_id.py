"""The REST document routes must resolve a short id, like `memo_get` does.

[memo, 2026-08-21] `8b5255dd` gave MCP `memo_get` prefix resolution on 08-20.
The REST routes kept 404ing the identical id, so which id worked depended on
which transport you happened to use.

⭐ MEASURED, one 32-minute window on 2026-08-21: **13 404s, 12 of them short-id
lookups, 10 from a single seat against 9 distinct memos.** An 8-char id is what
circulates — pins, commit messages, agent DMs — so this was the common call.

⛔ The harm is the READING, not the failed fetch. A 404 says "this memo is
gone"; the truth was "you abbreviated it". `atc` came one step from reporting a
canonical memo deleted on that misreading.

⛔ ASSERTS THE CONTRACT ACROSS ALL FIVE ROUTES. The MCP-only fix is the exact
mistake this repeats otherwise — and the 413 fix earlier the same day failed the
same way, correct at one call site and absent at six others.
"""
import pytest
from fastapi.testclient import TestClient

from memo import db
from memo.main import app


@pytest.fixture()
def client():
    # Not the context-manager form — it starts the MCP session manager, which
    # refuses to run twice per process. See test_ready_probe.py.
    return TestClient(app)


@pytest.fixture()
def stored(temp_db):
    async def _mk(content="the gate code lives in the lockbox", title="t"):
        vec = [0.1] * db.settings.embedding_dimensions
        return await db.store(db_path=None, content=content, title=title,
                              tags=[], metadata={}, embedding=vec)
    return _mk


@pytest.mark.asyncio
async def test_get_resolves_a_unique_prefix_and_says_it_did(client, stored):
    full = await stored()

    r = client.get(f"/documents/{full[:8]}")

    assert r.status_code == 200, "a unique 8-char prefix is the id that circulates"
    assert r.json()["id"] == full
    assert r.headers.get("X-Resolved-From") == full[:8], \
        "an expanded abbreviation must be visible to the caller, not silent"


@pytest.mark.asyncio
async def test_a_full_uuid_is_unchanged_and_unannotated(client, stored):
    """The fix is additive: nothing about the existing call may move."""
    full = await stored()

    r = client.get(f"/documents/{full}")

    assert r.status_code == 200
    assert r.json()["id"] == full
    assert "X-Resolved-From" not in r.headers, \
        "no expansion happened, so claiming one would be a lie"


@pytest.mark.asyncio
async def test_a_miss_says_it_is_not_evidence_of_deletion(client, temp_db):
    r = client.get("/documents/deadbeef")

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["reason"] == "not_found"
    assert detail["requested_id"] == "deadbeef"
    assert "not evidence" in " ".join(detail["message"].split()), \
        "the 404 must disown the deleted-memo reading that caused the incident"


@pytest.mark.asyncio
async def test_an_ambiguous_prefix_is_refused_with_its_candidates(client, temp_db):
    """⛔ Two memos sharing a prefix is exactly when picking one is worst."""
    vec = [0.1] * db.settings.embedding_dimensions
    shared = "abcdef00"
    ids = []
    for n in range(2):
        ids.append(await db.store(
            db_path=None, content=f"doc {n}", title=f"t{n}", tags=[], metadata={},
            embedding=vec, doc_id=f"{shared}-0000-0000-0000-00000000000{n}"))

    r = client.get(f"/documents/{shared}")

    assert r.status_code == 409, "ambiguity is a conflict, not a miss and not a guess"
    detail = r.json()["detail"]
    assert detail["reason"] == "ambiguous"
    assert detail["candidate_count"] == 2
    assert len(detail["candidates"]) == 2, \
        "the caller needs the candidates to disambiguate without a second round trip"


@pytest.mark.asyncio
async def test_delete_refuses_an_ambiguous_prefix_rather_than_destroying_one(client, temp_db):
    """⛔⛔ The route where a guess is unrecoverable. There is no undo."""
    vec = [0.1] * db.settings.embedding_dimensions
    shared = "beefcafe"
    for n in range(2):
        await db.store(db_path=None, content=f"doc {n}", title=f"t{n}", tags=[],
                       metadata={}, embedding=vec,
                       doc_id=f"{shared}-0000-0000-0000-00000000000{n}")

    r = client.request("DELETE", f"/documents/{shared}")

    assert r.status_code == 409
    for n in range(2):
        survivor = await db.get(None, f"{shared}-0000-0000-0000-00000000000{n}")
        assert survivor is not None, "a refused delete must destroy nothing"


@pytest.mark.asyncio
async def test_a_zero_padded_short_id_is_named_as_such(client, stored):
    """⚑ The workaround that outlives the bug. Observed live 2026-08-21.

    A seat working around the missing short-id support padded `42e85a8c` out to
    `42e85a8c-0000-0000-0000-000000000000` — the right SHAPE to pass a validator,
    and not a prefix of anything, so it can NEVER resolve.

    ⛔ Prefix resolution does not rescue it. A caller who adapted to the bug is
    left broken by the fix, and the 404 still reads as "memo deleted". The only
    way they learn is if the message says so.
    """
    full = await stored()
    padded = f"{full[:8]}-0000-0000-0000-000000000000"

    r = client.get(f"/documents/{padded}")

    assert r.status_code == 404
    msg = r.json()["detail"]["message"]
    assert "PADDED" in msg, "the padding must be named, not left for them to infer"
    assert full[:8] in msg, "tell them the exact bare prefix to send instead"


@pytest.mark.asyncio
async def test_a_real_uuid_ending_in_zeros_is_not_mistaken_for_padding(client, temp_db):
    """⛔ The guard must not slander a legitimate id that happens to end in zeros."""
    vec = [0.1] * db.settings.embedding_dimensions
    real = "42e85a8c-0000-0000-0000-000000000000"
    await db.store(db_path=None, content="legit", title="t", tags=[], metadata={},
                   embedding=vec, doc_id=real)

    r = client.get(f"/documents/{real}")

    assert r.status_code == 200, \
        "an id that EXISTS resolves exactly, whatever it looks like"
    assert r.json()["id"] == real
