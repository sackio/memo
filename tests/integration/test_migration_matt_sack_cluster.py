"""Matt-Sack duplicate cluster through migration. [001/FR-012 001/FR-039]

T124. The real v1 cluster (`0c55a9a3` / `c664f4a1` / `98efbda5`): one fact
stored three times. Migration must collapse it to ONE canonical v2 memo and
leave redirects, so the two retired v1 ids still resolve instead of 404-ing
every reference that was ever written to them.

Note the DIFFERENT dedup rule from the read path. Here both embeddings are in
hand, so R-13 applies: cosine >= 0.90 AND title 4-gram >= 60%. The read-path
filter uses content-word Jaccard because it only has each candidate's
similarity to the QUERY, not to the other candidates.
"""
import pytest

from memo import db
from memo.migrate import backfill

NOW = 1_800_000_000.0
DAY = 86400.0

CANONICAL = ("Matt Sack is Ben's brother. He lives in Massachusetts and works "
             "in software. Reachable on his mobile for family logistics.")
DUP_1 = ("Matt Sack is Ben's brother — lives in Massachusetts, works in "
         "software. Reachable on his mobile for family logistics.")
DUP_2 = ("Matt Sack is Ben's brother. He lives in Massachusetts and works in "
         "software. Reach him on his mobile for family logistics.")

IDS = ["0c55a9a3-1111-4111-8111-111111111111",
       "c664f4a1-2222-4222-8222-222222222222",
       "98efbda5-3333-4333-8333-333333333333"]


def cluster_member(doc_id, content, created):
    return {"id": doc_id, "content": content, "title": "Matt Sack",
            "tags": ["family", "contact"], "metadata": {},
            "created_at": created, "updated_at": created}


async def near_identical_embed(text: str):
    """Cluster members embed near-identically; unrelated memos do not."""
    v = [0.0] * 1536
    if "matt sack" in (text or "").lower():
        v[0] = 1.0
        v[1] = 0.02 * (len(text) % 5)      # tiny drift, still cosine > 0.99
    else:
        v[500] = 1.0
    return v


@pytest.fixture
def corpus():
    return [cluster_member(IDS[0], CANONICAL, NOW - 300 * DAY),
            cluster_member(IDS[1], DUP_1, NOW - 200 * DAY),
            cluster_member(IDS[2], DUP_2, NOW - 100 * DAY)]


@pytest.mark.asyncio
async def test_cluster_collapses_to_one_canonical_memo(corpus):
    stats, lines = await backfill.migrate_corpus(
        corpus, dry_run=False, now=NOW, embed=near_identical_embed)

    assert stats.written == 1, f"expected 1 canonical write, got {stats.written}"
    assert stats.merged == 2

    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_the_first_member_becomes_canonical(corpus):
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                  embed=near_identical_embed)
    canonical = await db.get(None, IDS[0])
    assert canonical is not None
    assert canonical["content"] == CANONICAL


@pytest.mark.asyncio
async def test_retired_ids_resolve_via_redirect(corpus):
    """The reason redirects exist: every prior reference to a retired id would
    otherwise 404."""
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                  embed=near_identical_embed)
    for retired in IDS[1:]:
        assert await db.get(None, retired) is None, "duplicate should not be stored"
        assert backfill.resolve_v1_id(retired) == IDS[0]


@pytest.mark.asyncio
async def test_audit_log_records_the_merges(corpus, tmp_path):
    audit = tmp_path / "audit.jsonl"
    _, lines = await backfill.migrate_corpus(
        corpus, dry_run=False, audit_path=str(audit), now=NOW,
        embed=near_identical_embed)

    merges = [ln for ln in lines if ln.action == "merge"]
    assert len(merges) == 2
    assert all(m.merged_into == IDS[0] for m in merges)
    assert all(m.redirect_from in IDS[1:] for m in merges)
    assert len(audit.read_text().strip().splitlines()) == 3


@pytest.mark.asyncio
async def test_distinct_family_memos_are_not_collapsed():
    """Guard against over-collapsing: Laura is not Matt."""
    corpus = [cluster_member(IDS[0], CANONICAL, NOW - 300 * DAY),
              {"id": "aaaaaaaa-4444-4444-8444-444444444444",
               "content": "Laura Sack is Ben's wife and works in clinical genetics.",
               "title": "Laura Sack", "tags": ["family"], "metadata": {},
               "created_at": NOW - 250 * DAY, "updated_at": NOW - 250 * DAY}]
    stats, _ = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                             embed=near_identical_embed)
    assert stats.written == 2 and stats.merged == 0


@pytest.mark.asyncio
async def test_verification_sees_no_duplicate_clusters(corpus):
    """SC-005, end to end."""
    from memo.migrate import verify
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                  embed=near_identical_embed)
    result = await verify.verify([m["id"] for m in corpus])
    dup_check = next(c for c in result["checks"] if c["name"] == "no_duplicate_clusters")
    assert dup_check["passed"] is True
    id_check = next(c for c in result["checks"] if c["name"] == "every_v1_id_resolves")
    assert id_check["passed"] is True, "retired ids must resolve via redirect"


@pytest.mark.asyncio
async def test_provenance_from_a_duplicate_enriches_the_canonical(tmp_path):
    """A duplicate carrying provenance the canonical lacks should contribute it
    rather than being discarded outright."""
    corpus = [
        cluster_member(IDS[0], CANONICAL, NOW - 300 * DAY),
        {**cluster_member(IDS[1], DUP_1, NOW - 200 * DAY),
         "tags": ["family", "gmail-sourced"],
         "content": DUP_1 + " msg-id: fam-42"},
    ]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                  embed=near_identical_embed)
    canonical = await db.get(None, IDS[0])
    assert canonical["provenance"] is not None
    assert canonical["provenance"]["gmail_msg_id"] == "fam-42"
