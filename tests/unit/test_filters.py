"""Retrieval filter chain. [001/FR-011 001/FR-012 001/FR-013]

Pure functions over candidate lists — no DB, no LLM. Includes the two scenarios
the spec calls out by name: the migration-duplicate cluster collapse (FR-012)
and the 7/26 parking recall where recency + tag-class must beat a semantically
stronger but stale memo (FR-013).
"""
from memo.mediators.filters import (
    Boost,
    BiTemporalFilter,
    Candidate,
    DedupFilter,
    FilterContext,
    ScopeFilter,
    apply_chain,
    recency_decay,
)

NOW = 1_800_000_000.0
DAY = 86400.0


def cand(id_, semantic=0.9, *, content="body", title=None, tags=None,
         created_at=NOW, valid_from=0.0, valid_until=None, scope=None):
    return Candidate(
        memo={
            "id": id_,
            "content": content,
            "title": title,
            "tags": tags or [],
            "created_at": created_at,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "scope": scope if scope is not None else ["global"],
        },
        semantic=semantic,
    )


def ctx(**kw):
    kw.setdefault("now", NOW)
    return FilterContext(**kw)


# --- FR-011: bi-temporal ---

def test_superseded_memos_hidden_by_default():
    c = ctx()
    out = BiTemporalFilter()([cand("a"), cand("b", valid_until=NOW - 1)], c)
    assert [x.id for x in out] == ["a"]


def test_as_of_returns_the_version_valid_then():
    """Half-open window, matching db.get_as_of."""
    old = cand("old", valid_from=NOW - 10 * DAY, valid_until=NOW - 5 * DAY)
    new = cand("new", valid_from=NOW - 5 * DAY, valid_until=None)
    out = BiTemporalFilter()([old, new], ctx(as_of=NOW - 7 * DAY))
    assert [x.id for x in out] == ["old"]


def test_as_of_at_transition_instant_picks_new():
    old = cand("old", valid_from=NOW - 10 * DAY, valid_until=NOW - 5 * DAY)
    new = cand("new", valid_from=NOW - 5 * DAY)
    out = BiTemporalFilter()([old, new], ctx(as_of=NOW - 5 * DAY))
    assert [x.id for x in out] == ["new"]


# --- FR-008/011: scope ---

def test_no_scope_hint_is_passthrough_not_global_only():
    """An unhinted caller must still see project memos that answer the query."""
    out = ScopeFilter()([cand("g"), cand("p", scope=["project:memo"])], ctx())
    assert [x.id for x in out] == ["g", "p"]


def test_scope_hint_drops_unrelated_project_memos():
    out = ScopeFilter()(
        [cand("g"), cand("mine", scope=["project:memo"]),
         cand("theirs", scope=["project:other"])],
        ctx(scope_hint=["project:memo"]),
    )
    assert [x.id for x in out] == ["g", "mine"]


def test_global_always_matches_any_hint():
    out = ScopeFilter()([cand("g", scope=["global"])], ctx(scope_hint=["session:x"]))
    assert [x.id for x in out] == ["g"]


# --- FR-012: dedup ---

DUP = ("The barn K8s control-plane node is reachable at 192.168.1.243 "
       "and hosts the api server for the cluster")


def test_migration_duplicate_cluster_collapses_to_one():
    """C-06: same fact stored 3x by 3 sessions must not consume 3 result slots."""
    out = DedupFilter()(
        [cand("canon", 0.95, content=DUP),
         cand("dup1", 0.93, content=DUP),
         cand("dup2", 0.91, content=DUP)],
        ctx(),
    )
    assert len(out) == 1
    assert out[0].id == "canon", "highest-scoring member should survive"
    assert sorted(out[0].absorbed) == ["dup1", "dup2"]


def test_distinct_memos_are_not_collapsed():
    out = DedupFilter()(
        [cand("a", content="The barn K8s control plane is at 192.168.1.243"),
         cand("b", content="Laura's dentist appointment is on Thursday at 2pm")],
        ctx(),
    )
    assert len(out) == 2


def test_dedup_noop_on_single_candidate():
    assert len(DedupFilter()([cand("a")], ctx())) == 1


def test_dedup_survivor_is_best_scoring_not_first():
    out = DedupFilter()(
        [cand("weak", 0.70, content=DUP), cand("strong", 0.99, content=DUP)],
        ctx(),
    )
    assert out[0].id == "strong"


# --- FR-013: recency + tag-class boost ---

def test_recency_decay_bounds():
    assert recency_decay(NOW, NOW) == 1.0
    assert recency_decay(NOW + 1000, NOW) == 1.0, "future clamps, never exceeds 1"
    assert 0.0 < recency_decay(NOW - 3650 * DAY, NOW) < 0.01


def test_parking_recall_recent_logistics_memo_wins():
    """The 7/26 scenario: a stale SF-parking memo must not beat the fresh Logan one.

    The stale memo is given the STRONGER semantic score on purpose — if recency
    and tag-class weren't applied, it would win.
    """
    stale_sf = cand("sf", semantic=0.93, tags=["parking", "travel"],
                    created_at=NOW - 70 * DAY,
                    content="Parked in the Mission garage, level 2, SF trip")
    fresh_logan = cand("logan", semantic=0.88, tags=["parking", "travel"],
                       created_at=NOW - 3 * DAY,
                       content="Central Garage Level 6 Row R at Logan for Mexico trip")

    out = Boost()([stale_sf, fresh_logan], ctx())
    assert out[0].id == "logan"


def test_tag_class_boost_only_for_logistics_families():
    logistics = cand("l", semantic=0.80, tags=["parking"])
    other = cand("o", semantic=0.80, tags=["k8s"])
    out = Boost()([logistics, other], ctx())
    assert out[0].id == "l"
    assert out[0].score > out[1].score


def test_semantic_still_dominates_a_weak_match():
    """Recency must not let an irrelevant-but-fresh memo outrank a strong hit.

    Recency is weighted 0.3 against semantic's 0.5, so a 0.95-vs-0.10 semantic
    gap cannot be closed by freshness alone.
    """
    strong_old = cand("strong", semantic=0.95, created_at=NOW - 60 * DAY)
    weak_new = cand("weak", semantic=0.10, created_at=NOW)
    out = Boost()([strong_old, weak_new], ctx())
    assert out[0].id == "strong"


# --- chain integration ---

def test_full_chain_order_and_trace():
    c = ctx(scope_hint=["project:memo"])
    out = apply_chain(
        [
            cand("keep", 0.90, content="unique content about the thing", tags=["parking"]),
            cand("superseded", 0.99, content=DUP, valid_until=NOW - 1),
            cand("dupA", 0.80, content=DUP),
            cand("dupB", 0.79, content=DUP),
            cand("offscope", 0.95, content="other project entirely",
                 scope=["project:other"]),
        ],
        c,
    )
    ids = [x.id for x in out]
    assert "offscope" not in ids, "scope filter"
    assert "superseded" not in ids, "bi-temporal filter"
    assert len([i for i in ids if i in ("dupA", "dupB")]) == 1, "dedup collapsed the pair"
    # Trace must name every stage, in order, for explainability.
    joined = " | ".join(c.trace)
    for stage in ("scope:", "bi_temporal", "dedup:", "boost:"):
        assert stage in joined, f"{stage} missing from trace: {joined}"


def test_chain_on_empty_candidate_list():
    c = ctx()
    assert apply_chain([], c) == []
    assert len(c.trace) == 4, "every stage should still report"
