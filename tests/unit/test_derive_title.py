"""A memo stored with no title gets one derived from its own opening line.

⛔ DERIVE, NOT REFUSE. `title` is optional on both doors; requiring it would
422 an otherwise-good write, and a refused write loses the memo. Same call
already recorded on `StoreRequest.actor`.

⚠️ THE MEASUREMENT THAT NEARLY KILLED THIS CHANGE, kept because the mistake is
the reusable part: a scan of all 12,247 documents at 08:20 on 2026-10-24 found
ZERO untitled memos, and "zero targets" read as "this does not happen". By
08:41 memo-watch reported the count had risen to 1 (`884431e4`, a domains memo
whose first line was a perfectly good title). A snapshot of a rare event is a
RATE, not an absence — and the watch exists precisely because the rate is not
zero.
"""
import pytest
from fastapi.testclient import TestClient

from memo.db import derive_title
from memo.main import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.mark.parametrize("content,expected", [
    ("a one-line fact worth keeping", "a one-line fact worth keeping"),
    ("# A markdown heading\nand a body", "A markdown heading"),
    ("\n\n  indented after blanks\nmore", "indented after blanks"),
    ("", None),
    ("   \n\n \t ", None),
])
def test_derive_title_cases(content, expected):
    assert derive_title(content) == expected


def test_a_long_first_line_is_cut_on_a_word_boundary():
    t = derive_title("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike")
    assert t.endswith("…")
    assert len(t) <= 73
    assert not t[:-1].endswith(" "), "trailing space before the ellipsis"
    assert " " not in t[-6:-1] or t.rstrip("…").split()[-1] in t


def test_b_empty_content_yields_none_not_empty_string():
    """⛔ "" would satisfy every untitled check while still rendering blank —
    the defect in disguise. NULL is the honest value."""
    assert derive_title("") is None
    assert derive_title("\n\n") is None


@pytest.mark.asyncio
async def test_c_store_without_a_title_comes_back_titled(client, embedding):
    r = client.post("/documents", json={
        "content": "katamacreek.com linked to ACS outreach 2026-10-24 (measured)\nmore detail"})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    assert client.get(f"/documents/{doc_id}").json()["title"] == \
        "katamacreek.com linked to ACS outreach 2026-10-24 (measured)"


@pytest.mark.asyncio
async def test_d_a_blank_title_is_treated_as_no_title(client, embedding):
    """"" and "   " pass an `is None` check and still render blank, so they
    must collapse to the derive path or it is skipped by the worst inputs."""
    for blank in ("", "   "):
        r = client.post("/documents", json={"content": "derivable opening line\nx",
                                            "title": blank})
        assert client.get(f"/documents/{r.json()['id']}").json()["title"] == \
            "derivable opening line"


@pytest.mark.asyncio
async def test_e_a_real_title_is_never_overwritten(client, embedding):
    r = client.post("/documents", json={"content": "opening line\nbody", "title": "chosen"})
    assert client.get(f"/documents/{r.json()['id']}").json()["title"] == "chosen"
