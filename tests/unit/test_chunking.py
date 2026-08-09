"""The chunker, and the properties that make it safe. [002/FR-101 002/FR-102 002/FR-103 002/FR-104]

The tests that matter here assert ABSENCES: that no text is lost, that no size
threshold is configured, that no passage can exceed the provider cap. A chunker
that silently drops a span still returns plausible passages, so nothing else
would catch it.
"""
import re

import pytest

from memo import chunking
from memo.chunking import MAX_PASSAGE_TOKENS, Passage, chunk, count_tokens, reconstruct


def para(word: str, n: int) -> str:
    return " ".join([word] * n)


# --- FR-103: the single-passage invariant, and the absent threshold ---

def test_short_text_yields_exactly_one_unchanged_passage():
    """Short memos must behave EXACTLY as they do today — one vector, whole text."""
    text = "A short memo about where the spare key lives."
    out = chunk(text, target=384)
    assert len(out) == 1
    assert out[0].text == text
    assert out[0].index == 0
    assert out[0].token_start == 0


def test_there_is_no_configured_size_threshold():
    """The single-passage behaviour must be EMERGENT, not an `if tokens > N` branch.

    A configured cutoff would need its own tuning, create a discontinuity at the
    boundary, and leave two code paths to keep in agreement. Guarding it in a
    test because it is the kind of thing a later 'optimisation' reintroduces.
    """
    src = open(chunking.__file__).read()
    body = re.sub(r'""".*?"""', "", src, flags=re.S)   # strip docstrings
    body = re.sub(r"#.*", "", body)                     # strip comments
    assert not re.search(r"if\s+.*token_count\s*>", body)
    assert not re.search(r"if\s+.*len\(.*\)\s*>\s*\d{3,}", body)


def test_behaviour_is_continuous_across_the_target_boundary():
    """No cliff: one token more must not change the result by more than one passage."""
    just_under = para("alpha", 300)
    assert count_tokens(just_under) <= 384
    a = chunk(just_under, target=384)
    b = chunk(just_under + " beta gamma delta epsilon zeta", target=384)
    assert len(a) == 1
    assert len(b) - len(a) <= 1


# --- FR-101: coverage. The property that catches silent loss ---

@pytest.mark.parametrize("text", [
    "# Heading one\n\n" + para("alpha", 400) + "\n\n## Heading two\n\n" + para("beta", 400),
    para("solo", 1200),                                    # no boundaries at all
    "# A\n\n# B\n\n# C\n\n" + para("gamma", 900),           # headings, some empty
    "intro\n\n" + para("delta", 700) + "\n\n" + para("epsilon", 700),
])
def test_passages_reconstruct_the_input_exactly(text):
    """Union of passage spans, ignoring overlap, must rebuild the original.

    This is the assertion the whole module rests on. Without it a chunker that
    drops a section returns passages that look entirely reasonable.
    """
    out = chunk(text, target=200, overlap=0.0)
    assert reconstruct(out, text) == text


def test_coverage_holds_with_overlap_enabled():
    text = "# One\n\n" + para("alpha", 500) + "\n\n# Two\n\n" + para("beta", 500)
    out = chunk(text, target=200, overlap=0.15)
    # Overlap is additive context; the recorded spans still describe own-text only.
    assert reconstruct(out, text) == text


def test_spans_are_contiguous_and_non_overlapping():
    text = "# One\n\n" + para("alpha", 600) + "\n\n# Two\n\n" + para("beta", 600)
    out = chunk(text, target=200, overlap=0.0)
    for prev, cur in zip(out, out[1:]):
        assert cur.token_start == prev.token_end, "a gap here is lost text"


# --- FR-104: the provider cap ---

def test_no_passage_can_exceed_the_provider_cap():
    """A memo above the 8,192-token model cap must still index."""
    text = para("word", 9000)
    out = chunk(text, target=384)
    assert out, "an oversized memo must not silently produce nothing"
    for p in out:
        assert count_tokens(p.text) <= MAX_PASSAGE_TOKENS


def test_a_single_giant_paragraph_is_hard_wrapped():
    """No headings, no blank lines — the fallback must still bound each passage."""
    text = para("unbroken", 3000)
    out = chunk(text, target=300, overlap=0.0)
    assert len(out) > 1
    assert reconstruct(out, text) == text


# --- FR-102: structure-first splitting ---

def test_headings_are_preferred_as_boundaries():
    text = ("# Alpha section\n\n" + para("aaa", 300)
            + "\n\n# Beta section\n\n" + para("bbb", 300))
    out = chunk(text, target=320, overlap=0.0)
    assert len(out) >= 2
    # The second passage should START at a heading, not mid-paragraph.
    assert out[1].text.lstrip().startswith("#")


def test_a_fence_that_fits_is_kept_whole():
    """The guarantee is "don't split a fence WHEN AVOIDABLE".

    A fence smaller than target must survive intact — splitting it would turn
    one table or command into two unusable halves.
    """
    fence = "```\n" + "\n".join(f"row {i} | value {i}" for i in range(8)) + "\n```"
    text = "# Intro\n\n" + para("intro", 400) + "\n\n# Table\n\n" + fence
    out = chunk(text, target=300, overlap=0.0)
    whole = [p.text for p in out if "```" in p.text]
    assert whole, "the fence must land in some passage"
    assert any(p.count("```") == 2 for p in whole), \
        "a fence that fits must not be split across passages"


def test_a_fence_larger_than_target_is_split_rather_than_unbounded():
    """The unavoidable case, stated explicitly rather than left to luck.

    A fence bigger than the target CANNOT be kept whole without blowing the
    passage cap. It gets hard-wrapped — the cap wins, because an unbounded
    passage cannot be embedded at all. Documented here so nobody "fixes" it.
    """
    fence = "```\n" + "\n".join(f"row {i} | value {i}" for i in range(60)) + "\n```"
    text = "# Table\n\n" + fence
    out = chunk(text, target=300, overlap=0.0)
    assert len(out) > 1, "an oversized fence must be split, not emitted whole"
    for p in out:
        assert count_tokens(p.text) <= MAX_PASSAGE_TOKENS
    assert reconstruct(out, text) == text, "splitting must not lose any of it"


# --- edge cases ---

@pytest.mark.parametrize("text", ["", "   ", "\n\n\t\n"])
def test_empty_input_yields_no_passages(text):
    assert chunk(text) == []


def test_heading_with_no_body():
    text = "# Just a heading\n\n# Another\n\n# Third"
    out = chunk(text, target=384)
    assert len(out) == 1 and out[0].text == text


def test_exactly_target_tokens_is_one_passage():
    text = para("tok", 100)
    n = count_tokens(text)
    out = chunk(text, target=n)
    assert len(out) == 1


def test_passages_are_indexed_in_order():
    text = "# A\n\n" + para("alpha", 500) + "\n\n# B\n\n" + para("beta", 500)
    out = chunk(text, target=200, overlap=0.0)
    assert [p.index for p in out] == list(range(len(out)))


def test_a_heading_never_becomes_a_stub_passage_on_its_own():
    """A passage containing only "# Alpha" can never match anything.

    Regression: a section whose only paragraph break sits immediately after its
    heading produced a 3-token passage of pure heading, and pushed the section's
    real content into an unbounded hard-wrap. Found 2026-07-30 by a passage-
    search test that expected 3 passages and got 9.
    """
    text = "# Alpha\n\n" + para("alpha", 250) + "\n\n# Beta\n\n" + para("beta", 250)
    out = chunk(text, target=200, overlap=0.0)
    for p in out:
        stripped = p.text.strip()
        assert not (stripped.startswith("#") and "\n" not in stripped), \
            f"stub passage containing only a heading: {stripped!r}"
        assert count_tokens(p.text) >= 10 or p is out[-1], \
            f"passage too small to be worth indexing: {p.text!r}"
