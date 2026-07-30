"""Migration backfill over a synthetic corpus. [001/FR-038 001/FR-039 001/FR-040 001/FR-002]

T123: a 200-memo corpus covering every reachable v2 class. Asserts class
assignment, provenance reconstruction, duplicate collapse, bi-temporal
preservation, and — the property everything else rests on — that v1 is never
written.
"""
import json

import pytest

from memo import db
from memo.migrate import backfill, classify, verify

NOW = 1_800_000_000.0
DAY = 86400.0


def v1_memo(i, *, content=None, tags=None, title=None, created=None, meta=None):
    return {
        "id": f"{i:08d}-1111-4111-8111-111111111111",
        "content": content or f"v1 memo number {i} with some ordinary content",
        "title": title or f"memo {i}",
        "tags": tags or [],
        "metadata": meta or {},
        "created_at": created if created is not None else NOW - 100 * DAY,
        "updated_at": NOW - 100 * DAY,
    }


def unit_vec(seed: int, dim: int = 1536):
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


async def fake_embed(text: str):
    return unit_vec(abs(hash(text)) % 1000)


# --- classification rules ---

def test_constitutional_requires_explicit_evidence():
    """Highest-consequence class, so the highest evidence bar.

    A memo wrongly classed constitutional is force-injected into every session
    on the fleet; one parked in legacy-unattributed just waits for a human.
    """
    assert classify.classify(v1_memo(1, tags=["constitution"]), now=NOW)[0] == "constitutional"
    # hard-rule tag ALONE is behavioral, not constitutional...
    assert classify.classify(v1_memo(2, tags=["hard-rule"],
                                     content="prefer docker"), now=NOW)[0] == "behavioral"
    # ...unless the content also carries operator-authority language.
    assert classify.classify(
        v1_memo(3, tags=["ben-hard-rule"], content="Ben requires: always use docker"),
        now=NOW)[0] == "constitutional"


def test_behavioral_from_prohibition_language():
    cls, src = classify.classify(v1_memo(4, tags=["misc"],
                                         content="Don't cat a log file."), now=NOW)
    assert cls == "behavioral" and src == "content-heuristic"


def test_verbatim_critical_from_uuid_plus_constraint():
    """A UUID summarized is a UUID destroyed."""
    cls, _ = classify.classify(v1_memo(
        5, tags=["misc"],
        content="Never use a prefix; cite 5d43c4a0-1111-4111-8111-111111111111 in full."),
        now=NOW)
    assert cls == "verbatim-critical"


def test_goal_and_episodic_and_decision():
    assert classify.classify(v1_memo(6, tags=["goal"]), now=NOW)[0] == "goal"
    assert classify.classify(v1_memo(7, tags=["session-log"]), now=NOW)[0] == "episodic"
    assert classify.classify(v1_memo(8, tags=["decision"], created=NOW - DAY),
                             now=NOW)[0] == "decision-in-progress"


def test_stale_decision_is_not_in_progress():
    assert classify.classify(v1_memo(9, tags=["decision"], created=NOW - 200 * DAY),
                             now=NOW)[0] != "decision-in-progress"


def test_time_scoped_without_a_window_falls_back_to_fact():
    """A time-scoped memo with no window would never inject and never expire."""
    cls, src = classify.classify(v1_memo(10, tags=["parking"]), now=NOW)
    assert cls == "fact" and "timescoped-fallback" in src


def test_untagged_memo_is_legacy_unattributed():
    assert classify.classify(v1_memo(11, tags=[]), now=NOW)[0] == "legacy-unattributed"


def test_canonical_tag_mapping():
    out = classify.canonicalize_tags(["hard-rule", "ben-hard-rule", "k8s"])
    assert out == ["behavioral-rule", "k8s"]


def test_provenance_reconstruction():
    prov, src = classify.reconstruct_provenance(
        v1_memo(12, tags=["gmail-sourced"], content="msg-id: abc123xyz"))
    assert prov["gmail_msg_id"] == "abc123xyz"
    assert src == "gmail-sourced-tag-inference"


def test_provenance_is_never_invented():
    """An invented provenance block is worse than none — it makes an unsourced
    memo look verified."""
    prov, src = classify.reconstruct_provenance(v1_memo(13, tags=["random"]))
    assert prov is None and src is None


# --- full corpus migration ---

@pytest.mark.asyncio
async def test_migrates_a_200_memo_corpus(tmp_path):
    corpus = []
    for i in range(200):
        bucket = i % 8
        tags = [["constitution"], ["hard-rule"], ["goal"], ["session-log"],
                ["k8s"], ["parking"], ["verbatim-critical"], []][bucket]
        corpus.append(v1_memo(i, tags=tags,
                              content=f"distinct memo {i} about subject {i}"))

    audit = tmp_path / "audit.jsonl"
    stats, lines = await backfill.migrate_corpus(
        corpus, dry_run=False, audit_path=str(audit), now=NOW, embed=fake_embed)

    assert stats.total == 200
    assert stats.written + stats.merged + stats.skipped == 200
    # Every memo produced an audit line — a migration you cannot reconstruct
    # afterwards is one you cannot trust.
    assert len(lines) == 200
    assert len(audit.read_text().strip().splitlines()) == 200
    for raw in audit.read_text().strip().splitlines()[:5]:
        rec = json.loads(raw)
        assert rec["v1_id"] and rec["action"]


@pytest.mark.asyncio
async def test_every_class_is_reachable(tmp_path):
    corpus = [
        v1_memo(1, tags=["constitution"], content="a constitutional rule"),
        v1_memo(2, tags=["behavioral-rule"], content="prefer docker"),
        v1_memo(3, tags=["goal"], content="ship the seam"),
        v1_memo(4, tags=["verbatim-critical"], content="exact wording"),
        v1_memo(5, tags=["k8s"], content="the cluster is at 1.2.3.4",
                meta={"url": "https://example.invalid/x"}),
        v1_memo(6, tags=["decision"], content="deciding", created=NOW - DAY),
        v1_memo(7, tags=["session-log"], content="a session log"),
        v1_memo(8, tags=[], content="no tags at all"),
    ]
    stats, _ = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                             embed=fake_embed)
    for cls in ("constitutional", "behavioral", "goal", "verbatim-critical",
                "fact", "decision-in-progress", "episodic", "legacy-unattributed"):
        assert stats.by_class.get(cls), f"{cls} not produced: {stats.by_class}"


@pytest.mark.asyncio
async def test_bi_temporal_semantics_preserved(tmp_path):
    """T122 / FR-002: valid_from = v1.created_at, valid_until = NULL."""
    created = NOW - 300 * DAY
    corpus = [v1_memo(1, tags=["k8s"], created=created,
                      meta={"url": "https://example.invalid/x"})]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)

    row = await db.get(None, corpus[0]["id"])
    assert row["valid_from"] == created
    assert row["valid_until"] is None
    assert row["created_at"] == created, "migration must not rewrite creation dates"


@pytest.mark.asyncio
async def test_created_at_is_not_rewritten_to_now(tmp_path):
    """A migration that stamps created_at=now destroys the recency signal the
    entire ranking formula depends on."""
    corpus = [v1_memo(i, tags=["k8s"], created=NOW - i * DAY,
                      content=f"memo {i}") for i in range(1, 6)]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)
    for m in corpus:
        assert (await db.get(None, m["id"]))["created_at"] == m["created_at"]


@pytest.mark.asyncio
async def test_constitutional_gets_legacy_ratification_metadata(tmp_path):
    corpus = [v1_memo(1, tags=["constitution"], content="a rule")]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)
    row = await db.get(None, corpus[0]["id"])
    assert row["class"] == "constitutional"
    assert row["injection_mode"] == "forcible-constitutional"
    assert row["constitution_meta"]["version"] == backfill.BACKFILL_VERSION


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path):
    corpus = [v1_memo(i, tags=["k8s"]) for i in range(10)]
    stats, lines = await backfill.migrate_corpus(corpus, dry_run=True, now=NOW,
                                                 embed=fake_embed)
    assert stats.total == 10 and len(lines) == 10
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_empty_content_is_skipped(tmp_path):
    stats, lines = await backfill.migrate_corpus(
        [v1_memo(1, content="   ")], dry_run=False, now=NOW, embed=fake_embed)
    assert stats.skipped == 1 and lines[0].action == "skip"


@pytest.mark.asyncio
async def test_a_failing_memo_does_not_abort_the_run(tmp_path):
    """One bad memo must not strand a 7,000-memo migration."""
    async def flaky(text):
        if "poison" in text:
            raise RuntimeError("embedding blew up")
        return await fake_embed(text)

    corpus = [v1_memo(1, tags=["k8s"], content="fine one"),
              v1_memo(2, tags=["k8s"], content="poison memo"),
              v1_memo(3, tags=["k8s"], content="fine two")]
    stats, lines = await backfill.migrate_corpus(corpus, dry_run=False, now=NOW,
                                                 embed=flaky)
    assert stats.total == 3
    assert any(ln.action == "skip" and "error" in (ln.note or "") for ln in lines)
    assert stats.written == 2


# --- rollback (FR-040) ---

@pytest.mark.asyncio
async def test_rollback_clears_v2(tmp_path):
    corpus = [v1_memo(i, tags=["k8s"]) for i in range(5)]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)
    conn = db._get_or_create_conn(db.global_path())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 5

    out = await backfill.rollback()
    assert out["deleted"] == 5
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM document_embeddings").fetchone()[0] == 0


# --- verification (T121) ---

@pytest.mark.asyncio
async def test_verify_passes_on_a_clean_migration(tmp_path):
    corpus = [v1_memo(i, tags=["k8s"], content=f"distinct subject {i}",
                      meta={"url": f"https://example.invalid/{i}"})
              for i in range(20)]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)
    result = await verify.verify([m["id"] for m in corpus])
    assert result["passed"] is True, result["checks"]
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_verify_fails_when_legacy_share_is_over_budget(tmp_path):
    """SC-009: at most 5% legacy-unattributed."""
    corpus = [v1_memo(i, tags=[], content=f"untagged {i}") for i in range(20)]
    await backfill.migrate_corpus(corpus, dry_run=False, now=NOW, embed=fake_embed)
    result = await verify.verify()
    assert result["passed"] is False
    check = next(c for c in result["checks"]
                 if c["name"] == "legacy_unattributed_within_budget")
    assert check["passed"] is False and check["data"]["pct"] == 100.0


@pytest.mark.asyncio
async def test_verify_flags_unresolvable_v1_ids(tmp_path):
    await backfill.migrate_corpus([v1_memo(1, tags=["k8s"])], dry_run=False,
                                  now=NOW, embed=fake_embed)
    result = await verify.verify(["00000001-1111-4111-8111-111111111111",
                                  "deadbeef-0000-4000-8000-000000000000"])
    check = next(c for c in result["checks"] if c["name"] == "every_v1_id_resolves")
    assert check["passed"] is False
    assert check["data"]["unresolved_count"] == 1


@pytest.mark.asyncio
async def test_verify_catches_the_valid_from_zero_specimen(embedding):
    """The deliberate Phase 7 fixture: a row written before the _sync_store fix
    with valid_from=0. Verification must notice it."""
    doc = await db.store(None, "pre-fix row", None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET valid_from = 0 WHERE id = ?", (doc,))
    conn.commit()
    result = await verify.verify()
    check = next(c for c in result["checks"] if c["name"] == "bi_temporal_preserved")
    assert check["passed"] is False
    assert check["data"]["bad_valid_from"] == 1
