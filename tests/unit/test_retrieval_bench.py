"""The bench measures the system; these tests measure the bench. [002/FR-111]

T255. `An uninstrumented bench rots; that is how the original defect survived.`

**What CI can and cannot cover, stated plainly.** Retrieval *quality* — SC-101,
SC-102, SC-103 — cannot be checked here: it needs a real corpus and a live
embedding provider, and the test container has neither by design (`network_mode:
none`, a throwaway /tmp DB). Pinning expected rank-1 counts in CI would either
require checking in megabytes of vectors or quietly become a test of a fixture
rather than of the corpus. Those numbers stay a deliberate run against :8091,
recorded in research.md with the date and the sample size.

What CI *can* protect is the instrument, and that is where every defect actually
appeared:

  - 2026-07-30 — an unrestricted passage run scored un-indexed memos as `absent`,
    so it reported INDEXING COVERAGE while looking exactly like a retrieval
    result, and showed the passage path doing worse than it does.
  - 2026-07-31 — `--both-indexed-only` restricted the own-title sample but never
    the fact set, so 22 of 30 fact cases were unreachable by construction: the
    criterion read 5/30 (17%) when the honest number was 5/8.
  - 2026-07-31 — the sample was 14 per band against populations of 63–67, which
    was too small to distinguish a real effect from one document of noise, and
    produced two wrong headline figures.

None of those was a bug in retrieval. All three were measurement code confidently
reporting a number. So these tests assert the properties whose violation produced
a plausible-looking wrong answer, and they run with no network at all.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_bench():
    """Import the bench, which has no `.py` extension by design (it is a CLI)."""
    for candidate in (Path("/app/scripts/memo-retrieval-bench"),
                      Path(__file__).resolve().parents[2] / "scripts" / "memo-retrieval-bench"):
        if candidate.exists():
            spec = importlib.util.spec_from_loader(
                "memo_retrieval_bench",
                importlib.machinery.SourceFileLoader("memo_retrieval_bench", str(candidate)))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("memo-retrieval-bench not present in this image")


bench = _load_bench()


def _doc(doc_id: str, tokens: int, title: str | None = None) -> dict:
    return {"id": doc_id, "token_count": tokens, "title": title or f"title {doc_id}"}


class _Stub:
    """Stands in for the HTTP surface. No network, no server, no corpus."""

    def __init__(self, docs, indexed=None, answers=None, fail=()):
        self.docs = docs
        self.indexed = indexed if indexed is not None else [d["id"] for d in docs]
        self.answers = answers or {}
        self.fail = set(fail)
        self.queried: list[str] = []

    def get(self, _url, path, timeout=0):
        if path.startswith("/documents"):
            return self.docs
        if path == "/admin/passage-indexed-ids":
            return self.indexed
        raise AssertionError(f"unexpected GET {path}")

    def search(self, _url, query, limit=10, timeout=0, path="document"):
        self.queried.append(query)
        if query in self.fail:
            raise RuntimeError("query blew up")
        ids = self.answers.get(query, [])
        return [{"document": {"id": i}, "score": 0.5} for i in ids[:limit]]


@pytest.fixture
def stub(monkeypatch):
    def _install(s):
        monkeypatch.setattr(bench, "_get", s.get)
        monkeypatch.setattr(bench, "_search", s.search)
        return s
    return _install


def test_both_indexed_only_restricts_the_own_title_sample(stub):
    """The restriction that keeps a coverage gap from reading as a retrieval result."""
    docs = [_doc("a", 3000), _doc("b", 3000), _doc("c", 3000)]
    s = stub(_Stub(docs, indexed=["a"], answers={"title a": ["a"]}))

    res = bench.run("http://x", per_band=10, limit=10, seed=1,
                    path="passages", both_indexed_only=True)

    band = next(b for b in res["bands"] if b["band"] == "2000+")
    assert band["n"] == 1, "only the indexed memo is eligible"
    assert band["rank1"] == 1
    assert band["absent"] == 0, (
        "b and c have no passages; scoring them as `absent` would measure "
        "indexing coverage while looking exactly like a retrieval result")


def test_unrestricted_run_scores_unindexed_memos_as_absent(stub):
    """The 2026-07-30 defect, pinned so it cannot come back silently."""
    docs = [_doc("a", 3000), _doc("b", 3000), _doc("c", 3000)]
    s = stub(_Stub(docs, indexed=["a"], answers={"title a": ["a"]}))

    res = bench.run("http://x", per_band=10, limit=10, seed=1,
                    path="passages", both_indexed_only=False)

    band = next(b for b in res["bands"] if b["band"] == "2000+")
    assert band["n"] == 3 and band["absent"] == 2, (
        "without the restriction the un-indexed memos count against the score — "
        "this is the shape of the original wrong answer, asserted so the flag's "
        "value is visible rather than assumed")


def test_a_failed_query_counts_as_absent_and_never_as_a_pass(stub):
    docs = [_doc("a", 3000), _doc("b", 3000)]
    s = stub(_Stub(docs, answers={"title a": ["a"]}, fail={"title b"}))

    res = bench.run("http://x", per_band=10, limit=10, seed=1)

    band = next(b for b in res["bands"] if b["band"] == "2000+")
    assert band["n"] == 2
    assert band["absent"] == 1, "an exception must not be scored as a hit"
    assert band["rank1"] == 1


def test_rank1_requires_position_zero_not_mere_presence(stub):
    docs = [_doc("a", 3000)]
    s = stub(_Stub(docs, answers={"title a": ["other", "a"]}))

    res = bench.run("http://x", per_band=10, limit=10, seed=1)

    band = next(b for b in res["bands"] if b["band"] == "2000+")
    assert band["rank1"] == 0, "present at rank 2 is not rank-1"
    assert band["top5"] == 1
    assert band["absent"] == 0


def test_bands_split_on_token_count(stub):
    docs = [_doc("tiny", 100), _doc("small", 300), _doc("mid", 700),
            _doc("big", 1500), _doc("huge", 5000)]
    s = stub(_Stub(docs))

    res = bench.run("http://x", per_band=10, limit=10, seed=1)

    assert {b["band"]: b["n"] for b in res["bands"]} == {
        "0-200": 1, "200-500": 1, "500-1000": 1, "1000-2000": 1, "2000+": 1}


def test_untitled_memos_are_excluded_from_the_own_title_set(stub):
    """A blank title is not a query; scoring it would be scoring nothing."""
    docs = [_doc("a", 3000), {"id": "b", "token_count": 3000, "title": "   "}]
    s = stub(_Stub(docs, answers={"title a": ["a"]}))

    res = bench.run("http://x", per_band=10, limit=10, seed=1)

    band = next(b for b in res["bands"] if b["band"] == "2000+")
    assert band["n"] == 1


def test_doc_of_tolerates_both_hit_shapes():
    assert bench._doc_of({"document": {"id": "x"}, "score": 1})["id"] == "x"
    assert bench._doc_of({"id": "x"})["id"] == "x"


def test_sample_is_deterministic_for_a_seed(stub):
    docs = [_doc(f"d{i}", 3000) for i in range(40)]
    a = stub(_Stub(docs))
    bench.run("http://x", per_band=5, limit=10, seed=7)
    first = list(a.queried)

    b = stub(_Stub(docs))
    bench.run("http://x", per_band=5, limit=10, seed=7)

    assert b.queried == first, (
        "a comparison between two paths is only a comparison if the same seed "
        "draws the same sample")


# --- the fact set: the 2026-07-31 defect, now reachable from a test -----------
#
# This logic was inline in `main()`, which is why it shipped unnoticed — nothing
# could call it. It is extracted precisely so these assertions can exist.

def test_factset_restriction_drops_cases_the_path_cannot_reach():
    cases = [{"query": "q1", "expect_id": "a"},
             {"query": "q2", "expect_id": "b"},
             {"query": "q3", "expect_id": "c"}]

    kept, skipped = bench.eligible_factset(cases, {"a"})

    assert [c["expect_id"] for c in kept] == ["a"]
    assert skipped == 2, (
        "b and c have no passages — scoring them would measure coverage, and "
        "that is how the criterion read 17% when the real figure was 62.5%")


def test_factset_reports_the_excluded_count_rather_than_hiding_it(stub):
    s = stub(_Stub([], answers={"q1": ["a"]}))
    cases = [{"query": "q1", "expect_id": "a"}]

    out = bench.score_factset("http://x", cases, limit=10, path="passages", skipped=22)

    assert out["skipped_unindexed"] == 22, (
        "a silently reduced denominator is how a partial index flatters itself")
    assert out["n"] == 1 and out["rank1"] == 1


def test_factset_counts_a_missing_answer_as_absent(stub):
    s = stub(_Stub([], answers={"q1": ["someone-else"]}))
    cases = [{"query": "q1", "expect_id": "a"}]

    out = bench.score_factset("http://x", cases, limit=10, path="passages")

    assert out["rank1"] == 0 and out["absent"] == 1


def test_factset_failed_query_is_not_a_pass(stub):
    s = stub(_Stub([], fail={"q1"}))
    cases = [{"query": "q1", "expect_id": "a"}]

    out = bench.score_factset("http://x", cases, limit=10, path="passages")

    assert out["absent"] == 1 and out["rank1"] == 0
