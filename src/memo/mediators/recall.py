"""Retrieval mediator — POST /recall. [001/FR-010 001/FR-011 001/FR-012 001/FR-013 001/FR-014 001/FR-015]

Implements contracts/mediator-recall.md. Agents call this instead of raw
`POST /search`: it returns a reconciled ANSWER plus citations, not a pile of
rows for the caller to interpret.

Shape of a request:

    embed -> over-fetch candidates -> filter chain -> compose answer
          -> (LLM fallback only if ambiguous) -> audit log

Two properties worth stating up front, because they drive the whole design:

* **The happy path makes no LLM call.** Fallback fires only when the result set
  is genuinely ambiguous — more than `memo_llm_fallback_candidate_threshold`
  survivors, or top candidates that conflict. Ordinary recalls are pure
  vector + filters, which is what keeps them fast (tens of ms).
* **A missing LLM degrades, it never fails the caller** (R-17). If the
  `memo-llm` session is unavailable, the mediator still returns 200 with its
  search-only answer and an `anomalies` entry naming the degradation.
"""
from __future__ import annotations

import logging
from time import perf_counter, time

from memo import db, embeddings
from memo.config import settings
from memo.mediators import audit
from memo.mediators.filters import Candidate, FilterContext, apply_chain
from memo.models import RecallRequest, RecallResponse
from memo.providers.llm import get_llm_provider
from memo.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

MEDIATOR_VERSION = "1.0.0"

# Over-fetch relative to what the caller wants: the filter chain removes
# superseded rows and collapses duplicate clusters, so fetching exactly
# `max_results` would routinely return fewer than asked after filtering.
OVERFETCH_FACTOR = 4
OVERFETCH_MIN = 24

# Two candidates this semantically close, whose content disagrees, are treated
# as a conflict worth escalating rather than silently ranking.
CONFLICT_SIM_DELTA = 0.05

# A runner-up must score at least this fraction of the leader to be treated as
# part of the answer rather than an unrelated hit that merely ranked next.
ANSWER_SET_RATIO = 0.85


def _conflicting(top: list[Candidate]) -> bool:
    """Do the leading candidates look like disagreement rather than agreement?

    Heuristic: near-equal semantic scores but content that survived dedup — i.e.
    they are about the same thing yet were NOT judged near-identical. Dedup
    already collapsed genuine restatements, so two survivors this close are
    more likely to be two different claims about one subject.
    """
    if len(top) < 2:
        return False
    return abs(top[0].semantic - top[1].semantic) <= CONFLICT_SIM_DELTA


def _answer_set(cands: list[Candidate]) -> list[Candidate]:
    """Narrow the ranked list to the memos that actually answer the question.

    `max_results` is a CEILING, not a target. Returning everything under it
    means a query with one good hit also drags in whatever ranked 2nd and 3rd
    — observed live: "what UPS is in the office rack?" came back with the UPS
    memo *plus* an unrelated dentist appointment. That is noise the calling
    agent then has to reason around, which is exactly the work the mediator
    exists to do for it.

    So: always keep the leader, then keep a runner-up only while it stays
    within `ANSWER_SET_RATIO` of the leader's score. A genuinely close second
    is a competing claim worth showing; a distant one is a different subject.
    """
    if not cands:
        return []
    top = cands[0]
    if top.score <= 0:
        return [top]
    return [c for c in cands if c.score >= top.score * ANSWER_SET_RATIO]


def _compose_answer(cands: list[Candidate], budget_tokens: int) -> str:
    """Deterministic answer from the winning memos — no LLM involved.

    Single clear winner: its content. Several genuinely close: stitched in rank
    order under the budget, so the caller sees the competing claims rather than
    one arbitrarily-chosen row.
    """
    parts: list[str] = []
    used = 0
    for c in cands:
        body = (c.memo.get("content") or "").strip()
        if not body:
            continue
        cost = db._count_tokens(body)
        if parts and used + cost > budget_tokens:
            break
        parts.append(body)
        used += cost
    return "\n\n---\n\n".join(parts)


async def recall(req: RecallRequest, *, provider: LLMProvider | None = None) -> RecallResponse:
    """Run the retrieval mediator for one request. [001/FR-010]

    `provider` is injectable so tests can supply a stub; production takes the
    configured one (`null` until Phase 5 lands `claude_session`).
    """
    started = perf_counter()
    provider = provider or get_llm_provider()
    ctx = FilterContext(
        query=req.query,
        as_of=req.as_of,
        scope_hint=list(req.scope_hint or []),
        now=time(),
    )

    # 1. Semantic retrieval.
    embedding = await embeddings.embed(req.query)
    limit = max(OVERFETCH_MIN, req.max_results * OVERFETCH_FACTOR)
    rows = await db.search(
        db_path=None, embedding=embedding, limit=limit, min_score=None,
        tags=[], after=None, before=None, min_tokens=None, max_tokens=None,
    )
    ctx.note(f"semantic_top_k: {len(rows)}")

    candidates = [
        Candidate(memo=r["document"] if "document" in r else r,
                  semantic=float(r.get("score", 0.0)))
        for r in rows
    ]

    # 2. Filter chain (scope -> bi-temporal -> dedup -> boost).
    survivors = apply_chain(candidates, ctx)

    # 3. No results is a first-class outcome, not an error. An explicit null
    #    answer is distinguishable from a confidently wrong one.
    if not survivors:
        ctx.anomalies.append("gap: no memo covers this query")
        resp = RecallResponse(
            answer=None, citations=[], filter_chain_trace=ctx.trace,
            llm_fallback_used=False, anomalies=ctx.anomalies,
            latency_ms=int((perf_counter() - started) * 1000),
            mediator_version=MEDIATOR_VERSION,
        )
        await audit.log_recall(req, resp, ctx)
        return resp

    ranked = survivors[:req.max_results]
    top = _answer_set(ranked)
    ctx.note(f"answer_set: {len(ranked)}→{len(top)}")

    # 4. LLM fallback — ONLY when genuinely ambiguous.
    threshold = settings.memo_llm_fallback_candidate_threshold
    too_many = len(survivors) > threshold
    conflict = _conflicting(top)
    fallback_used = False

    if too_many or conflict:
        reason = "conflict" if conflict else f">{threshold} candidates"
        ctx.note(f"llm_fallback: triggered ({reason})")
        completion = await provider.complete(
            _fallback_prompt(req.query, top),
            budget_tokens=req.budget_tokens,
            timeout_s=settings.memo_llm_timeout_seconds,
        )
        if completion is None:
            # R-17 degrade path. Still a 200, with the degradation named.
            ctx.note("llm_fallback: UNAVAILABLE — degraded to search-only")
            ctx.anomalies.append(
                f"degraded: LLM unavailable ({provider.name}); "
                "answer is search-only and may not reconcile the candidates"
            )
        else:
            fallback_used = True
            ctx.note("llm_fallback: used")

        if conflict:
            ctx.anomalies.append(
                "conflict: two memos with disjoint claims, neither superseded "
                f"({top[0].id}, {top[1].id})"
            )

        answer = completion if completion is not None else _compose_answer(
            top, req.budget_tokens)
    else:
        answer = _compose_answer(top, req.budget_tokens)

    # Cluster members absorbed during dedup are surfaced as an anomaly, not
    # hidden: a caller may want to know one "fact" is stored three times.
    absorbed = [a for c in top for a in c.absorbed]
    if absorbed:
        ctx.anomalies.append(
            f"duplicates: {len(absorbed)} near-identical memo(s) collapsed "
            "into the cited canonical result"
        )

    resp = RecallResponse(
        answer=answer,
        citations=[c.id for c in top],
        filter_chain_trace=ctx.trace,
        llm_fallback_used=fallback_used,
        anomalies=ctx.anomalies,
        latency_ms=int((perf_counter() - started) * 1000),
        mediator_version=MEDIATOR_VERSION,
    )
    await audit.log_recall(req, resp, ctx)
    return resp


def _fallback_prompt(query: str, cands: list[Candidate]) -> str:
    """Prompt for the ambiguous case.

    Explicitly instructs the model to say so when the memos disagree rather
    than smoothing it over — a confidently-merged wrong answer is worse than a
    surfaced conflict, which is the entire reason the fallback exists.
    """
    blocks = "\n\n".join(
        f"[{i + 1}] id={c.id} (score {c.semantic:.3f}, "
        f"tags={c.memo.get('tags') or []})\n{(c.memo.get('content') or '').strip()}"
        for i, c in enumerate(cands)
    )
    return (
        "You are reconciling stored memos to answer one question.\n\n"
        f"QUESTION: {query}\n\nCANDIDATE MEMOS:\n{blocks}\n\n"
        "Answer the question from these memos only. If they DISAGREE, say so "
        "explicitly and give both claims with their ids — do not silently pick "
        "one. If none of them answer the question, say so plainly. Be concise."
    )
