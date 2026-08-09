"""memo:<uuid> transclusion resolution. [001/FR-016 001/FR-017]"""
import pytest

from memo import db
from memo.injection import transclude

UUID_A = "11111111-1111-4111-8111-111111111111"


def test_finds_uuid_references():
    assert transclude.find_refs(f"see memo:{UUID_A} for details") == [UUID_A]


def test_ignores_non_uuid_references():
    """`memo:later` is prose, not a reference — it must never reach the DB."""
    assert transclude.find_refs("memo:later memo:todo memo:xyz") == []


def test_dedupes_and_preserves_order():
    b = "22222222-2222-4222-8222-222222222222"
    text = f"memo:{b} then memo:{UUID_A} then memo:{b} again"
    assert transclude.find_refs(text) == [b, UUID_A]


def test_case_insensitive_normalised_to_lower():
    assert transclude.find_refs(f"memo:{UUID_A.upper()}") == [UUID_A]


@pytest.mark.asyncio
async def test_resolves_to_current_content(embedding):
    doc_id = await db.store(None, "the door code is 4417", None, [], {}, embedding)
    out = await transclude.resolve(f"rule: memo:{doc_id}", source_file="CLAUDE.md")
    assert len(out) == 1
    assert out[0]["resolved_content"] == "the door code is 4417"
    assert out[0]["source_file"] == "CLAUDE.md"


@pytest.mark.asyncio
async def test_reference_to_superseded_memo_yields_current_version(embedding):
    """The reason to cite a uuid instead of pasting text: it follows the lineage."""
    old = await db.store(None, "the door code is 4417", None, [], {}, embedding)
    res = await db.supersede(None, old, {"content": "the door code is 9921",
                                         "tags": [], "metadata": {}},
                             embedding, actor="operator:ben")
    out = await transclude.resolve(f"memo:{old}", source_file="CLAUDE.md")
    assert out[0]["resolved_content"] == "the door code is 9921"
    assert out[0]["resolved_id"] == res["new_id"]


@pytest.mark.asyncio
async def test_unknown_reference_is_skipped_not_fatal():
    """A stale uuid in someone's CLAUDE.md must not break session start."""
    out = await transclude.resolve(f"memo:{UUID_A}", source_file="CLAUDE.md")
    assert out == []


@pytest.mark.asyncio
async def test_reference_count_is_capped(embedding):
    doc_id = await db.store(None, "x", None, [], {}, embedding)
    text = " ".join(f"memo:{doc_id}" for _ in range(50))
    out = await transclude.resolve(text, source_file="big.md", max_refs=3)
    # Same uuid dedupes to one ref regardless; use distinct ids for the cap test.
    assert len(out) <= 3


@pytest.mark.asyncio
async def test_cap_applies_across_distinct_ids(embedding):
    ids = [await db.store(None, f"memo body {i}", None, [], {}, embedding)
           for i in range(6)]
    text = " ".join(f"memo:{i}" for i in ids)
    out = await transclude.resolve(text, source_file="big.md", max_refs=3)
    assert len(out) == 3, "a pathological file must not fan out into many DB reads"
