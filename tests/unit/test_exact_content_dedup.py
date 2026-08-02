"""An exact content match does not need the title's permission. [002/FR-114]

T274, from R-09. A clean migration of 7,336 memos left FOUR content-identical
pairs unmerged — `no_duplicate_clusters` is the one verification check that
failed — and in every case the titles differed only in a token carrying no
information:

  - `Backfill checkpoint — office — …` vs `— server4 — …` (the hostname; both
    hosts proxy one database since the 2026-06-29 single-global refactor, so the
    differing token is precisely the meaningless one)
  - a Storage Taxonomy memo where one copy carried a long inline `[⚠️ STALE …]`
    annotation *in its title*
  - one fact stored under two phrasings

R-08 predicted this exactly: of 384 nearest-neighbour pairs, zero cleared both
halves of the rule and 87 cleared cosine alone, so the 4-gram title gate is not a
tiebreaker — it is the entire decision.

**The conjunction is right for NEAR duplicates and wrong for identical ones.**
Where content merely resembles content, a title disagreement is real evidence the
two memos mean different things. Where the content is byte-identical, the title
cannot carry information the content does not.

The bypass is deliberately `==`, not a similarity threshold: a test strictly
stronger than the one it skips cannot loosen the rule.
"""
from __future__ import annotations

import pytest

from memo import db
from memo.config import settings
from memo.migrate import backfill

NOW = 1_800_000_000.0

SHARED = ("Backfill checkpoint — session UUIDs processed: "
          "138a8eb6-44f2-42f8-bfa2-33fb3dd8bb6a@1204, "
          "29968098-870a-4536-a30e-6455d30c8451@903.")


def _memo(doc_id: str, content: str, title: str) -> dict:
    return {"id": doc_id, "content": content, "title": title,
            "tags": ["maintenance"], "metadata": {},
            "created_at": NOW - 100, "updated_at": NOW - 100}


async def _distinct_embed(text: str):
    """Every memo embeds ORTHOGONALLY — cosine is ~0 for every pair.

    That is the point: it forces the exact-content branch to be the only thing
    that can merge these. If the test passed because cosine happened to be high,
    it would prove nothing about T274.
    """
    v = [0.0] * settings.embedding_dimensions
    v[hash(text) % settings.embedding_dimensions] = 1.0
    return v


@pytest.mark.asyncio
async def test_identical_content_merges_despite_unrelated_titles():
    """The headline. Fails against the old conjunctive rule."""
    corpus = [
        _memo("aaaaaaaa-1111-4111-8111-111111111111", SHARED,
              "Backfill checkpoint — office — 2026-07-20 06:58 (p2/13)"),
        _memo("bbbbbbbb-2222-4222-8222-222222222222", SHARED,
              "Backfill checkpoint — server4 — 2026-07-20 06:58 (p2/13)"),
    ]
    stats, lines = await backfill.migrate_corpus(
        corpus, dry_run=False, now=NOW, embed=_distinct_embed)

    assert stats.written == 1, "byte-identical content must collapse to one memo"
    assert stats.merged == 1
    merge = [ln for ln in lines if ln.action == "merge"]
    assert len(merge) == 1
    assert merge[0].merged_into == corpus[0]["id"], "the FIRST is canonical"
    assert merge[0].redirect_from == corpus[1]["id"], "the retired id must redirect"


@pytest.mark.asyncio
async def test_the_retired_id_still_resolves():
    """Collapsing must never 404 a reference that already exists in the wild."""
    corpus = [
        _memo("aaaaaaaa-1111-4111-8111-111111111111", SHARED, "Checkpoint A"),
        _memo("bbbbbbbb-2222-4222-8222-222222222222", SHARED,
              "Something else entirely, sharing no words at all"),
    ]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                  embed=_distinct_embed)

    assert backfill.resolve_v1_id(corpus[1]["id"]) == corpus[0]["id"]
    assert await db.get(None, corpus[1]["id"]) is None


@pytest.mark.asyncio
async def test_a_title_annotation_does_not_prevent_the_merge():
    """The Storage Taxonomy case: one copy's title carries an inline STALE note,
    which tanks 4-gram overlap while saying nothing about the content."""
    body = "# Storage Taxonomy\n\nNFS + ZFS + mergerfs across server4/5/6."
    corpus = [
        _memo("cccccccc-3333-4333-8333-333333333333", body,
              "Storage Taxonomy — NFS + ZFS + mergerfs (per-path layouts)"),
        _memo("dddddddd-4444-4444-8444-444444444444", body,
              "Storage Taxonomy — NFS + ZFS + mergerfs [⚠️ STALE on corpus "
              "location 2026-06-12: the bar cache claim is REVERSED, live corpus "
              "is the single NAS root; see 06123ef8 §1/§8a for current truth]"),
    ]
    stats, _ = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                             embed=_distinct_embed)
    assert stats.written == 1 and stats.merged == 1


# --- The rule must not get looser for anything else ---

@pytest.mark.asyncio
async def test_near_identical_content_still_needs_the_title_gate():
    """One character different is NOT an exact match, so the conjunctive rule
    still applies — and with orthogonal embeddings it refuses to merge.

    This is the guard on the guard: if this ever passes, the `==` has been
    relaxed into a similarity test and the change is no longer conservative.
    """
    corpus = [
        _memo("eeeeeeee-5555-4555-8555-555555555555", SHARED, "Checkpoint A"),
        _memo("ffffffff-6666-4666-8666-666666666666", SHARED + " ", "Checkpoint B"),
    ]
    stats, _ = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                             embed=_distinct_embed)
    assert stats.written == 2, "near-identical is not identical — do not collapse"
    assert stats.merged == 0


@pytest.mark.asyncio
async def test_distinct_content_is_never_collapsed():
    corpus = [
        _memo("11111111-1111-4111-8111-111111111111",
              "Matt Sack is Ben's brother, lives in Massachusetts.", "Matt Sack"),
        _memo("22222222-2222-4222-8222-222222222222",
              "Laura Sack works in clinical genetics.", "Laura Sack"),
    ]
    stats, _ = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                             embed=_distinct_embed)
    assert stats.written == 2 and stats.merged == 0


@pytest.mark.asyncio
async def test_prior_records_actually_carry_content():
    """Guard against the silent no-op this nearly shipped as.

    The exact-match branch compares against `prior["content"]`. The record it
    reads did not originally contain that key, so the comparison was always
    False and the entire feature was dead code that read as working. Assert the
    field exists rather than trusting it.
    """
    corpus = [_memo("99999999-9999-4999-8999-999999999999", SHARED, "Only one")]
    migrated: list[dict] = []
    await backfill.migrate_one(corpus[0],
                               embedding=await _distinct_embed(SHARED),
                               migrated=migrated, dry_run=True, now=NOW)
    assert migrated, "migrate_one must record what it wrote"
    assert "content" in migrated[0], (
        "prior records must carry content or the exact-match dedup silently "
        "never fires")
    assert migrated[0]["content"] == SHARED
