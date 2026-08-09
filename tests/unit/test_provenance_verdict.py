"""The provenance verdict must not get stricter the harder you sample. [002/FR-111]

`memo-verify-provenance` decides three things, and two of them used to be decided
by an extreme rather than by a rate:

    any row below MARGINAL_FLOOR          -> PROVENANCE MISMATCH   (a MIN in disguise)
    >5% of rows inside the marginal band  -> THRESHOLD MISCALIBRATED (a rate, correct)

Drawing more rows can only increase the chance of catching a low one, so the first
gate tightens as the sample grows — a healthy corpus can be failed purely for being
inspected more carefully. Two sibling services found the same shape in their own
tools on 2026-08-02, one of them while fixing its twin in the same file.

⭐ THE TESTABLE PROPERTY IS N-INVARIANCE ON HEALTHY DATA. "Avoid extreme-value
statistics" is advice and cannot fail; "the verdict for a healthy corpus is identical
at n=12 and n=1000" is a test. A suite that varies the DEFECT but never varies `n`
cannot see this class at all — which is how it survived a verdict rewrite elsewhere,
with every branch covered and all cases at n=3.

These tests import the script by path because it is a CLI tool, not a package module.
"""
from __future__ import annotations

import os
import types

import pytest

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "memo-verify-provenance")

THRESHOLD = 0.999
FLOOR = 0.995
HEALTHY = 0.99994   # what a real row measures here; drift floor is 1.18e-04
FAR_BELOW = 0.62    # unrelated-text territory; a genuinely wrong vector


@pytest.fixture(scope="module")
def prov():
    """Load the CLI script as a module without executing main()."""
    src = open(SCRIPT).read()
    mod = types.ModuleType("prov")
    mod.__dict__.update(__name__="notmain", __file__=SCRIPT)
    exec(compile(src, SCRIPT, "exec"), mod.__dict__)
    return mod


def rows(n, bad=0, marginal=0, healthy=HEALTHY):
    out = [(f"bad{i}", FAR_BELOW) for i in range(bad)]
    out += [(f"marg{i}", 0.9985) for i in range(marginal)]
    out += [(f"ok{i}", healthy) for i in range(n - bad - marginal)]
    return out


# ── the property this file exists for ────────────────────────────────────────

@pytest.mark.parametrize("n", [3, 12, 100, 300, 1000, 5000])
def test_healthy_verdict_is_invariant_in_n(prov, n):
    """A clean corpus must pass identically at every sample size.

    This is the regression guard. If someone reintroduces a bare `min()` or
    `if bad:` gate, the large-n cases fail while the small-n cases keep passing —
    which is precisely the asymmetry that let the defect survive review.
    """
    rc, msg = prov.verdict(rows(n), THRESHOLD, FLOOR)
    assert rc == 0, f"healthy corpus failed at n={n}: {msg}"
    assert "VERIFIED" in msg


@pytest.mark.parametrize("n", [100, 300, 1000])
def test_one_bad_row_stays_isolated_as_n_grows(prov, n):
    """One wrong vector is one wrong vector, whatever the denominator.

    It must still be reported (exit 2 — that row IS a provenance failure), but the
    verdict must not claim the corpus is wrong, and the claim must get *weaker* as
    n grows rather than staying constant.
    """
    rc, msg = prov.verdict(rows(n, bad=1), THRESHOLD, FLOOR)
    assert rc == 2
    assert "ISOLATED" in msg
    assert "SYSTEMATIC" not in msg
    assert "NOT evidence the corpus is wrong" in msg


def test_systematic_rate_is_called_systematic(prov):
    rc, msg = prov.verdict(rows(100, bad=20), THRESHOLD, FLOOR)
    assert rc == 2
    assert "SYSTEMATIC" in msg


def test_isolated_and_systematic_are_the_same_defect_at_different_rates(prov):
    """The boundary is a RATE, so identical defects on different n differ."""
    _, isolated = prov.verdict(rows(1000, bad=1), THRESHOLD, FLOOR)
    _, systematic = prov.verdict(rows(10, bad=1), THRESHOLD, FLOOR)
    assert "ISOLATED" in isolated
    assert "SYSTEMATIC" in systematic, (
        "1 of 10 is 10%, over the systematic bar — if this reads ISOLATED the "
        "gate has stopped being a rate")


# ── the marginal band: a row near the line is not a wrong model ──────────────

def test_single_marginal_row_does_not_fail(prov):
    """`mind` measured 1 healthy row in 100 below 0.999 on OpenRouter/3-large.

    On that stack the old `worst < threshold` logic called MISMATCH on a correct
    corpus for any sample of ~100.
    """
    rc, msg = prov.verdict(rows(100, marginal=1), THRESHOLD, FLOOR)
    assert rc == 0
    assert "MARGINAL" in msg.upper()


def test_many_marginal_rows_blame_the_threshold_not_the_corpus(prov):
    rc, msg = prov.verdict(rows(100, marginal=12), THRESHOLD, FLOOR)
    assert rc == 3, "a cluster near the line is a miscalibrated threshold, not a bad corpus"
    assert "THRESHOLD MISCALIBRATED" in msg


def test_marginal_verdict_is_also_n_invariant(prov):
    """1% marginal must read the same at every n — it is under the 5% bar throughout."""
    verdicts = {prov.verdict(rows(n, marginal=max(1, n // 100)), THRESHOLD, FLOOR)[0]
                for n in (100, 300, 1000)}
    assert verdicts == {0}


# ── classification boundaries, so the two quantities cannot be re-conflated ──

def test_far_below_is_never_marginal(prov):
    """A sibling's first fix used the negative control's MAX as the divider and
    called a 0.62 row 'marginal'. The marginal band is defined by the noise of the
    SIGNAL (how low a correct pair scores), never by the noise of the NULL."""
    assert prov.classify(FAR_BELOW, THRESHOLD, FLOOR) == "mismatch"
    assert prov.classify(0.9985, THRESHOLD, FLOOR) == "marginal"
    assert prov.classify(HEALTHY, THRESHOLD, FLOOR) == "ok"


def test_empty_input_is_not_a_pass_claim(prov):
    rc, msg = prov.verdict([], THRESHOLD, FLOOR)
    assert rc == 0
    assert "VERIFIED" not in msg, "nothing checked must not read as verified"
