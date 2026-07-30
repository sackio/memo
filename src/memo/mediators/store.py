"""Storage mediator — POST /store. [001/FR-015a 001/FR-015b 001/FR-015c 001/FR-015d 001/FR-015e 001/FR-015f 001/FR-015g]

Implements contracts/mediator-store.md. Every write goes through here, so the
corpus stops accumulating the duplicate/contradictory sprawl v1 collected.

Reconcile-before-write picks one of six actions:

    merge | supersede | split | reject | clarify | write-new

Two rules govern everything below, and they pull in opposite directions:

* **Never lose an agent's memo.** A mediator that drops writes is worse than
  the mess it was built to prevent. So every uncertain path — LLM down,
  reconcile inconclusive — falls through to `write-new` plus an auditor flag,
  never to an error.
* **Never let a fact be silently refuted.** A write that contradicts a
  `class=fact` memo needs operator authority (FR-015c).

Where they collide, the first wins: a refutation we could not *detect* (because
inference was unavailable) must not become a spurious 403. Only a DETECTED
refutation gates.
"""
from __future__ import annotations

import logging
from time import perf_counter

from memo import clarify, db, embeddings
from memo.config import settings
from memo.mediators import audit
from memo.models import MediatorStoreRequest, MediatorStoreResponse
from memo.providers.llm import get_llm_provider
from memo.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Reconcile candidate window. Small on purpose: this runs on EVERY write, so it
# must stay cheap.
RECONCILE_LIMIT = 8

# Near-identical -> merge without asking. Set high; a wrong auto-merge silently
# destroys a distinct memo, which is the expensive mistake here.
MERGE_SIM = 0.94
# Related enough to be worth reconciling, but not obviously the same claim.
RECONCILE_SIM = 0.82

# C44 canonical tag vocabulary: collapse the hard-rule fragmentation.
CANONICAL_TAGS = {
    "hard-rule": "behavioral-rule",
    "ben-hard-rule": "behavioral-rule",
    "behavioural-rule": "behavioral-rule",
    "operator-rule": "behavioral-rule",
}


def canonicalize_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Map tags onto the canonical vocabulary (C44). Returns (tags, changed)."""
    out, changed = [], []
    for t in tags or []:
        c = CANONICAL_TAGS.get(t.strip().lower(), t)
        if c != t:
            changed.append(f"{t}->{c}")
        if c not in out:
            out.append(c)
    return out, changed


def infer_class(req: MediatorStoreRequest) -> str:
    """Best-effort class inference when the caller omits it (FR-015b).

    Deliberately conservative — it never infers `constitutional` (that requires
    ratification metadata and operator intent, and guessing it would forcibly
    inject the memo into every session) and never `legacy-unattributed` (that
    is a migration outcome, not something a live write should claim).
    """
    if req.class_:
        return req.class_
    tags = {t.lower() for t in (req.tags or [])}
    if req.time_scope is not None:
        return "time-scoped"
    if req.reopenability is not None or "decision" in tags:
        return "decision-in-progress"
    if tags & {"behavioral-rule", "hard-rule", "ben-hard-rule"}:
        return "behavioral"
    if tags & {"goal", "objective"}:
        return "goal"
    return "fact"


def _looks_compound(content: str) -> bool:
    """Cheap pre-check for FR-015g split candidates.

    Only a TRIAGE signal deciding whether the question is worth an LLM call —
    never the split decision itself. Splitting on a regex would shatter ordinary
    multi-sentence prose into fragments.
    """
    paras = [p for p in (content or "").split("\n\n") if p.strip()]
    return len(paras) >= 3 or len(content or "") > 1500


def _resp(action: str, started: float, **kw) -> MediatorStoreResponse:
    return MediatorStoreResponse(
        action=action, latency_ms=int((perf_counter() - started) * 1000), **kw
    )


async def _reconcile_candidates(content: str, embedding: list[float]) -> list[dict]:
    """Current memos semantically near the incoming content.

    Bi-temporal filtered here rather than after: reconciling against a
    superseded memo would resurrect a decision that was already made.
    """
    rows = await db.search(
        db_path=None, embedding=embedding, limit=RECONCILE_LIMIT, min_score=None,
        tags=[], after=None, before=None, min_tokens=None, max_tokens=None,
    )
    return [
        {"doc": r["document"], "score": float(r.get("score", 0.0))}
        for r in rows
        if r["document"].get("valid_until") is None
    ]


async def _detect_refutation(incoming: str, candidate: dict,
                             provider: LLMProvider) -> bool | None:
    """Does `incoming` CONTRADICT this `class=fact` memo? [001/FR-015c]

    Returns True/False, or **None when inference was unavailable** — the caller
    must treat None as "undetermined" and fall through to write-new, NOT as a
    refusal. Guessing True offline would 403 innocent writes; guessing False
    would silently admit contradictions. Neither is acceptable, so the
    uncertainty is propagated honestly.
    """
    completion = await provider.complete(
        "Two statements about the same subject.\n\n"
        f"STORED: {candidate['doc'].get('content', '')}\n\n"
        f"INCOMING: {incoming}\n\n"
        "Does INCOMING contradict STORED — i.e. assert something that cannot be "
        "true at the same time? An update to a changed value IS a contradiction. "
        "Extra detail that coexists is NOT. Answer exactly CONTRADICTS or COMPATIBLE.",
        budget_tokens=16,
        timeout_s=settings.memo_llm_timeout_seconds,
    )
    if completion is None:
        return None
    return "CONTRADICT" in completion.upper()


async def store(req: MediatorStoreRequest, *,
                provider: LLMProvider | None = None) -> MediatorStoreResponse:
    """Run the storage mediator for one write. [001/FR-015a 001/FR-015b]"""
    started = perf_counter()
    provider = provider or get_llm_provider()
    anomalies: list[str] = []
    trace: list[str] = []
    rounds = 0

    # A retry answering a clarification carries its token. Resolve against the
    # SNAPSHOT so the agent cannot change the content while answering.
    resolved = None
    if req.clarification_token:
        resolved = clarify.resolve(req.clarification_token, session_id=req.session_id)
        if resolved is None:
            resp = _resp("reject", started,
                         reason="clarification token unknown, expired, or not yours; "
                                "re-submit the write to start a fresh clarification")
            await audit.log_store(req, resp, filters=trace, anomalies=anomalies)
            return resp
        rounds = resolved.rounds
        trace.append(f"clarification: resolved token (round {rounds})")

    tags, tag_changes = canonicalize_tags(req.tags)
    if tag_changes:
        trace.append(f"canonical_tags: {', '.join(tag_changes)}")
    inferred = infer_class(req)

    memo_payload = {
        "content": req.content,
        "title": req.title,
        "tags": tags,
        "metadata": {},
        "class": inferred,
        "scope": req.scope,
        "provenance": req.provenance.model_dump() if req.provenance else None,
        "time_scope": req.time_scope.model_dump() if req.time_scope else None,
        "reopenability": req.reopenability.model_dump() if req.reopenability else None,
    }

    embedding = await embeddings.embed(req.content)

    # bypass_mediator skips reconcile entirely (operator-authorized escape
    # hatch, e.g. bulk migration).
    if req.bypass_mediator:
        trace.append("bypass_mediator: reconcile skipped")
        return await _write_new(req, memo_payload, embedding, started, inferred,
                                tags, trace, anomalies, rounds)

    candidates = await _reconcile_candidates(req.content, embedding)
    trace.append(f"reconcile: {len(candidates)} current candidate(s)")

    # Similarity bands, and why the refutation gate only looks at the middle one:
    #
    #   score >= MERGE_SIM      -> a RESTATEMENT. At this similarity the text is
    #                              near-identical, so there is nothing to
    #                              contradict; merging needs no inference.
    #   RECONCILE_SIM..MERGE_SIM -> the ambiguous band. Same subject, different
    #                              wording — this is where a contradiction can
    #                              actually hide, so this is what costs an LLM
    #                              call.
    #   below RECONCILE_SIM     -> unrelated. Ignored.
    #
    # Checking refutation across the whole >=RECONCILE_SIM range was the first
    # cut and it was wrong: with the null provider every fact-adjacent write
    # came back undetermined, which suppressed merge universally and turned
    # even a byte-identical re-store into a duplicate. Caught live on the
    # mediated /store path.
    dupes_band = [c for c in candidates if c["score"] >= MERGE_SIM]
    ambiguous_band = [c for c in candidates
                      if RECONCILE_SIM <= c["score"] < MERGE_SIM]

    # Set when a refutation check in the ambiguous band could not be completed.
    # Suppresses merge — see the comment there.
    refutation_undetermined = False

    # --- FR-015c: refutation gate, only against class=fact memos ---
    for c in ambiguous_band:
        if c["doc"].get("class") != "fact":
            continue
        verdict = await _detect_refutation(req.content, c, provider)

        if verdict is None:
            # Degraded. Per R-17 + the contract: fall through to write-new and
            # flag it, never a spurious 403.
            anomalies.append(
                f"degraded: could not check refutation against {c['doc']['id']} "
                f"(LLM unavailable via {provider.name}); written unreconciled "
                "for auditor review"
            )
            trace.append("refutation_check: UNAVAILABLE — degraded")
            refutation_undetermined = True
            break

        if not verdict:
            continue

        # A detected refutation.
        if req.operator_directive_ref is not None:
            # Authorized: this is a supersession, not a new parallel fact.
            trace.append(f"refutation: authorized, superseding {c['doc']['id']}")
            result = await db.supersede(
                None, c["doc"]["id"], memo_payload, embedding,
                actor=f"mediator:{req.session_id}",
                reason="authorized refutation via storage mediator",
                operator_directive_ref=req.operator_directive_ref,
            )
            if result is None:
                anomalies.append(
                    f"supersede of {c['doc']['id']} failed (already superseded?) "
                    "— wrote as new instead"
                )
                break
            resp = _resp("supersede", started, memo_id=result["new_id"],
                         superseded=result["old_id"],
                         supersede_edge_id=result["edge_id"],
                         class_inferred=inferred)
            await audit.log_store(req, resp, clarification_rounds=rounds,
                                  filters=trace, anomalies=anomalies)
            return resp

        # Unauthorized. 409 CLARIFY first; 403 only once they've been asked
        # and come back without authority (operator ruling 2026-07-29).
        if resolved is None:
            pending = clarify.open_clarification(
                session_id=req.session_id,
                prompt=(
                    f"This contradicts memo {c['doc']['id']} "
                    f"({(c['doc'].get('content') or '')[:120]!r}). Who is "
                    "authorizing the refutation? Retry with operator_directive_ref "
                    "set, or confirm this is a separate fact rather than a "
                    "correction."
                ),
                conflicting_memo_id=c["doc"]["id"],
                request_snapshot={"content": req.content, "tags": tags},
            )
            resp = _resp("clarify", started,
                         conflicting_memo_id=c["doc"]["id"],
                         prompt=pending.prompt,
                         resolve_via="POST /store with clarification_response + clarification_token",
                         clarification_token=pending.token,
                         expires_in=int(pending.ttl_s))
            await audit.log_store(req, resp, clarification_rounds=rounds + 1,
                                  filters=trace, anomalies=anomalies)
            return resp

        resp = _resp("reject", started,
                     reason=f"would refute fact memo {c['doc']['id']} without operator authority",
                     conflicting_memo_id=c["doc"]["id"],
                     how_to_authorize=("obtain operator directive (Ben DM), then retry "
                                       "with operator_directive_ref set"))
        await audit.log_store(req, resp, clarification_rounds=rounds,
                              filters=trace, anomalies=anomalies)
        return resp

    # --- FR-015b: near-identical -> merge ---
    #
    # Suppressed when a refutation check came back undetermined. Merge asserts
    # "these say the SAME thing" — precisely what we just failed to verify. And
    # merge only unions tags, leaving the stored content untouched, so merging a
    # contradicting update would silently DISCARD the new fact: "the UPS is now
    # an APC" would vanish into the CyberPower memo with no error and no new
    # revision. Writing a second memo is recoverable (the auditor can merge or
    # supersede later); a swallowed update is not.
    dupes = [] if refutation_undetermined else dupes_band
    if refutation_undetermined:
        trace.append("merge: suppressed (refutation undetermined)")
    if dupes:
        target = dupes[0]["doc"]
        trace.append(f"merge: into {target['id']} (score {dupes[0]['score']:.3f})")
        merged_tags = list(dict.fromkeys((target.get("tags") or []) + tags))
        added_provenance = (
            target.get("provenance") is None and memo_payload["provenance"] is not None
        )
        await db.update(
            db_path=None, doc_id=target["id"], content=None, title=None,
            tags=merged_tags, metadata=None, embedding=None,
        )
        resp = _resp("merge", started, memo_id=target["id"],
                     merged_into=[target["id"]], provenance_added=added_provenance)
        await audit.log_store(req, resp, clarification_rounds=rounds,
                              filters=trace, anomalies=anomalies)
        return resp

    # --- FR-015g: compound content -> ask whether to split ---
    if _looks_compound(req.content) and resolved is None:
        completion = await provider.complete(
            "Is the following memo a single coherent fact, or several distinct "
            "facts that should be stored separately?\n\n"
            f"{req.content}\n\n"
            "Answer SINGLE or SPLIT followed by a one-line reason.",
            budget_tokens=64, timeout_s=settings.memo_llm_timeout_seconds,
        )
        if completion is None:
            trace.append("split_check: UNAVAILABLE — stored whole")
            anomalies.append(
                f"degraded: compound-content check skipped (LLM unavailable via "
                f"{provider.name}); stored as one memo, auditor may split later"
            )
        elif completion.strip().upper().startswith("SPLIT"):
            pending = clarify.open_clarification(
                session_id=req.session_id,
                prompt=("This looks like several distinct facts: "
                        f"{completion.strip()}. Split into separate memos, or "
                        "store as one? Retry with clarification_response."),
                request_snapshot={"content": req.content, "tags": tags},
            )
            resp = _resp("clarify", started, prompt=pending.prompt,
                         resolve_via="POST /store with clarification_response + clarification_token",
                         clarification_token=pending.token,
                         expires_in=int(pending.ttl_s))
            await audit.log_store(req, resp, clarification_rounds=rounds + 1,
                                  filters=trace, anomalies=anomalies)
            return resp
        else:
            trace.append("split_check: single")

    return await _write_new(req, memo_payload, embedding, started, inferred,
                            tags, trace, anomalies, rounds)


async def _write_new(req: MediatorStoreRequest, payload: dict, embedding: list[float],
                     started: float, inferred: str, tags: list[str],
                     trace: list[str], anomalies: list[str],
                     rounds: int) -> MediatorStoreResponse:
    """Persist as a new memo — the terminal fall-through for every uncertain path."""
    if payload["provenance"] is None and inferred == "fact":
        # C-07 AS AMENDED (operator decision 2026-07-30): an unattributed fact
        # stays a FACT and is TAGGED, not demoted. Demoting measured 86.8% of
        # the real v1 corpus, which is "good facts, poor record-keeping" rather
        # than junk.
        #
        # Applied to live writes too, not just migration, so we don't
        # re-accumulate the same problem under a different name. The tag is
        # what makes later reprovenancing a query instead of a guess.
        from memo.migrate.backfill import PROVENANCE_PENDING_TAG
        if PROVENANCE_PENDING_TAG not in tags:
            tags.append(PROVENANCE_PENDING_TAG)
        anomalies.append("no provenance on a class=fact write — "
                         "tagged provenance-pending for later attribution")
        trace.append("provenance: missing -> tagged provenance-pending")

    doc_id = await db.store(
        db_path=None, content=req.content, title=req.title, tags=tags,
        metadata={}, embedding=embedding,
    )
    await _apply_v2_columns(doc_id, payload)

    resp = _resp("write-new", started, memo_id=doc_id,
                 class_inferred=payload["class"], canonical_tags_applied=tags)
    await audit.log_store(req, resp, clarification_rounds=rounds,
                          filters=trace, anomalies=anomalies)
    return resp


async def _apply_v2_columns(doc_id: str, payload: dict) -> None:
    """Stamp the v2 columns `db.store` doesn't know about.

    `db.store` is the v1 write path and only handles v1 columns. Rather than
    fork it (and risk drifting from the path v1 clients still use), the
    mediator writes its v2 fields immediately afterwards.
    """
    import asyncio
    import json

    def _sync() -> None:
        conn = db._get_or_create_conn(db.global_path())
        conn.execute(
            "UPDATE documents SET class=?, injection_mode=?, scope=?, provenance=?, "
            "time_scope=?, reopenability=? WHERE id=?",
            (
                payload["class"],
                "forcible-constitutional" if payload["class"] == "constitutional" else "on-recall",
                json.dumps(payload.get("scope") or ["global"]),
                json.dumps(payload["provenance"]) if payload.get("provenance") else None,
                json.dumps(payload["time_scope"]) if payload.get("time_scope") else None,
                json.dumps(payload["reopenability"]) if payload.get("reopenability") else None,
                doc_id,
            ),
        )
        conn.commit()

    await asyncio.to_thread(_sync)
