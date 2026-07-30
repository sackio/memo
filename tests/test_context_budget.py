"""Regression tests for memo_context's token-budget packing.

2026-07-30: `memo_context` returned `{content: "", doc_count: 0, truncated: true}`
whenever the top-ranked memo was larger than `token_budget` — the fill loop
`break`ed on the first non-fitting doc. Reported by three sessions in ten
minutes, two of whom read it as "the corpus has nothing on this topic".

The failure mode is the dangerous one: an empty result is a *vacuous success*.
Nothing errors, and the caller concludes it has no grounding to load.
"""

import asyncio

import pytest

from memo import db, main


def _doc(doc_id: str, content: str, score: float) -> dict:
    return {
        "score": score,
        "document": {"id": doc_id, "title": f"title-{doc_id}", "tags": [], "content": content},
    }


@pytest.fixture
def fake_corpus(monkeypatch):
    """Patch out embedding + search so only the packing logic is under test."""

    async def _fake_embed(_query):
        return [0.0] * 4

    def _install(results):
        async def _fake_search(**_kwargs):
            return results

        monkeypatch.setattr(main.embeddings, "embed", _fake_embed)
        monkeypatch.setattr(main.db, "search", _fake_search)

    return _install


def _run(**kwargs):
    return asyncio.run(main.memo_context(query="q", **kwargs))


def test_oversized_top_hit_does_not_starve_smaller_ones(fake_corpus):
    """The bug: one huge top-ranked memo suppressed every memo below it."""
    fake_corpus([
        _doc("big", "word " * 4000, 0.90),
        _doc("small", "a short but relevant memo", 0.80),
    ])

    result = _run(token_budget=300)

    assert result["doc_count"] == 1, "the smaller memo still fits and must be returned"
    assert "short but relevant" in result["content"]
    assert result["truncated"] is True, "skipping the big memo must be reported"
    assert result["matched_count"] == 2


def test_nothing_fits_returns_an_excerpt_not_emptiness(fake_corpus):
    """When no memo fits whole, return the top one excerpted — never ""."""
    fake_corpus([_doc("big", "word " * 4000, 0.90)])

    result = _run(token_budget=300)

    assert result["content"], "empty content is indistinguishable from 'no matches'"
    assert "[excerpt — truncated to fit token_budget]" in result["content"]
    assert result["doc_count"] == 1
    assert result["truncated"] is True
    assert result["token_count"] <= 300


def test_genuinely_empty_corpus_is_distinguishable_from_a_budget_miss(fake_corpus):
    """matched_count is what makes the response able to report its own absence."""
    fake_corpus([])

    result = _run(token_budget=300)

    assert result["content"] == ""
    assert result["doc_count"] == 0
    assert result["matched_count"] == 0, "no matches — distinct from 'matched but did not fit'"
    assert result["truncated"] is False, "nothing was cut off; nothing was there"


def test_everything_fits_is_not_marked_truncated(fake_corpus):
    fake_corpus([_doc("a", "short memo one", 0.9), _doc("b", "short memo two", 0.8)])

    result = _run(token_budget=4000)

    assert result["doc_count"] == 2
    assert result["matched_count"] == 2
    assert result["truncated"] is False


@pytest.mark.parametrize("max_tokens,expect_empty", [(0, True), (-5, True)])
def test_truncate_to_tokens_degenerate_budgets(max_tokens, expect_empty):
    assert (db._truncate_to_tokens("some text", max_tokens) == "") is expect_empty


def test_truncate_to_tokens_is_a_prefix_and_respects_the_cap():
    text = "word " * 500
    cut = db._truncate_to_tokens(text, 20)
    assert db._count_tokens(cut) <= 20
    assert text.startswith(cut)
