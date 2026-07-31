"""Migration backfill engine. [001/FR-038 001/FR-039 001/FR-040 001/FR-002]

Implements the per-memo pipeline in contracts/migration-cli.md:

    fetch -> classify -> retag -> provenance-link -> dedup/merge
          -> redirect -> set bi-temporal -> write -> log

Three properties hold throughout, and each is a rule the code enforces rather
than an intention:

* **v1 is read-only** (FR-038/FR-040). Only GETs go to v1. Rollback is
  therefore an operation on v2 alone.
* **Every memo produces an audit line** (FR-039), including skips. A migration
  you cannot reconstruct afterwards is one you cannot trust.
* **Dry-run is the default posture** in the CLI: rehearse, read the audit log,
  then commit.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from time import time
from typing import Any, Iterable

from memo import db, embeddings
from memo.migrate.classify import (
    canonicalize_tags,
    classify,
    injection_mode_for,
    reconstruct_provenance,
)

logger = logging.getLogger(__name__)

# C-06 duplicate detection for the migration path. Unlike the read-path dedup
# (content-word Jaccard), here we HAVE both embeddings, so R-13's rule applies:
# cosine >= 0.90 AND title 4-gram overlap >= 60%. [002/FR-114]
#
# Checked against the v2 corpus 2026-07-31 (research.md R-08) and left unchanged.
# Passage retrieval does not touch these: it adds an index over documents rather
# than replacing the document embeddings this rule compares.
#
# What the check did find: of 384 nearest-neighbour pairs, **zero** clear both
# halves and 87 clear the cosine bar alone. The 4-gram gate is not a tiebreaker
# here, it is the entire decision — so DUP_COSINE itself is effectively untested
# on this corpus, and "it collapses nothing" is not evidence that 0.90 is right.
# Calibrating it needs a must-collapse set drawn from content memos rather than
# from machine-generated logs, which this partially-migrated corpus cannot supply.
DUP_COSINE = 0.90
DUP_TITLE_NGRAM = 0.60

BACKFILL_VERSION = "0.0.0-legacy"

# Marks a fact whose provenance could not be reconstructed, so it can be
# found and re-attributed later. See the C-07 amendment in migrate_one.
PROVENANCE_PENDING_TAG = "provenance-pending"


def _ngrams(text: str, n: int = 4) -> set[str]:
    w = re.findall(r"\w+", (text or "").lower())
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


@dataclass
class AuditLine:
    v1_id: str
    action: str                       # write-new | merge | split | redirect | skip
    v2_ids: list[str] = field(default_factory=list)
    class_assigned: str | None = None
    class_source: str | None = None
    canonical_tags: list[str] = field(default_factory=list)
    provenance_reconstructed: bool = False
    provenance_source: str | None = None
    split_children: list[str] = field(default_factory=list)
    merged_into: str | None = None
    redirect_from: str | None = None
    note: str | None = None
    at: float = field(default_factory=time)

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in self.__dict__.items()})


@dataclass
class MigrationStats:
    total: int = 0
    written: int = 0
    merged: int = 0
    redirected: int = 0
    skipped: int = 0
    legacy_unattributed: int = 0
    by_class: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["legacy_pct"] = (100.0 * self.legacy_unattributed / self.total
                           if self.total else 0.0)
        return d


def _sync_write_v2(db_path: str, *, content: str, title: str | None,
                   tags: list[str], metadata: dict, cls: str, injection_mode: str,
                   scope: list[str], provenance: dict | None,
                   valid_from: float, created_at: float, updated_at: float,
                   constitution_meta: dict | None, derived_from: list[str],
                   doc_id: str, embedding: list[float]) -> str:
    """Insert a fully-formed v2 row, preserving v1 timestamps.

    Bypasses `db.store` because that stamps `created_at = now`, and a migration
    that rewrites every memo's creation date destroys the recency signal the
    whole ranking formula depends on.
    """
    conn = db._get_or_create_conn(db_path)
    conn.execute(
        "INSERT INTO documents ("
        "  id, content, title, tags, metadata, token_count, created_at, updated_at,"
        "  class, injection_mode, scope, provenance, valid_from, valid_until,"
        "  expires_at, time_scope, reopenability, derived_from, constitution_meta"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?)",
        (doc_id, content, title, json.dumps(tags), json.dumps(metadata),
         db._count_tokens(content), created_at, updated_at, cls, injection_mode,
         json.dumps(scope), json.dumps(provenance) if provenance else None,
         valid_from, json.dumps(derived_from),
         json.dumps(constitution_meta) if constitution_meta else None),
    )
    conn.execute("INSERT INTO document_embeddings (doc_id, embedding) VALUES (?, ?)",
                 (doc_id, db._serialize_vector(embedding)))
    conn.commit()
    return doc_id


def _sync_record_redirect(db_path: str, v1_id: str, canonical_v2_id: str) -> None:
    """Record `v1_id -> canonical_v2_id` so a stale id still resolves."""
    conn = db._get_or_create_conn(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migration_redirects ("
        "  v1_id TEXT PRIMARY KEY, canonical_v2_id TEXT NOT NULL, at REAL NOT NULL)")
    conn.execute(
        "INSERT INTO migration_redirects (v1_id, canonical_v2_id, at) VALUES (?,?,?) "
        "ON CONFLICT(v1_id) DO UPDATE SET canonical_v2_id=excluded.canonical_v2_id",
        (v1_id, canonical_v2_id, time()))
    conn.commit()


def _sync_lookup_redirect(db_path: str, v1_id: str) -> str | None:
    conn = db._get_or_create_conn(db_path)
    try:
        row = conn.execute(
            "SELECT canonical_v2_id FROM migration_redirects WHERE v1_id = ?",
            (v1_id,)).fetchone()
    except Exception:
        return None
    return row["canonical_v2_id"] if row else None


async def migrate_one(memo: dict[str, Any], *, embedding: list[float],
                      migrated: list[dict], dry_run: bool = True,
                      now: float | None = None,
                      backfill_ref: str = "backfill") -> AuditLine:
    """Run the pipeline for one v1 memo. Returns its audit line. [001/FR-039]

    `migrated` is the running list of already-written v2 memos, used for the
    dedup/merge step — migration dedups against ITS OWN OUTPUT, which is how a
    duplicate cluster collapses to one canonical row.
    """
    now = now if now is not None else time()
    v1_id = memo["id"]
    content = memo.get("content") or ""

    if not content.strip():
        return AuditLine(v1_id=v1_id, action="skip", note="empty content")

    cls, cls_source = classify(memo, now=now)
    tags = canonicalize_tags(memo.get("tags") or [])
    provenance, prov_source = reconstruct_provenance(memo)

    # C-07 AS AMENDED (operator decision 2026-07-30). A fact we cannot attribute
    # stays a FACT; it is tagged `provenance-pending` instead of demoted.
    #
    # The original rule demoted it to legacy-unattributed, which measured 86.8%
    # of the real v1 corpus — because that corpus records an origin KIND
    # (`assistant-sourced`, `git-sourced`) but almost never a locator. Operator:
    # "we should not be heavily penalizing the vast bulk of our corpus which is
    # actually good facts but don't have a readily known provenance because we
    # haven't done the record keeping of it yet."
    #
    # The TAG is load-bearing, not decoration: the plan is to reprovenance these
    # as they get proven out, and without a marker "the memos needing
    # provenance" is unfindable the moment they look like attributed ones.
    if provenance is None and cls == "fact":
        cls_source = "fact-provenance-pending"
        if PROVENANCE_PENDING_TAG not in tags:
            tags = tags + [PROVENANCE_PENDING_TAG]

    # --- dedup / merge against what we have already written (C-06 / R-13) ---
    title_grams = _ngrams(memo.get("title") or content[:120])
    for prior in migrated:
        # A memo can never be a duplicate OF ITSELF. If the same v1 id reaches
        # us twice the corpus read repeated it (see fetch_v1_corpus), and the
        # merge branch below would attach the memo's provenance to its own row
        # and write a self-redirect `id -> id`. Report it as an idempotent
        # re-encounter instead, so the audit log says what actually happened
        # and the stats stay meaningful.
        if prior["v2_id"] == v1_id:
            return AuditLine(v1_id=v1_id, action="skip-already-migrated",
                             v2_ids=[v1_id], class_assigned=cls,
                             class_source=cls_source, canonical_tags=tags,
                             provenance_reconstructed=provenance is not None,
                             provenance_source=prov_source,
                             note="duplicate v1 id in the corpus read")
        if _cosine(embedding, prior["embedding"]) < DUP_COSINE:
            continue
        if _jaccard(title_grams, prior["title_grams"]) < DUP_TITLE_NGRAM:
            continue
        # Duplicate: attach provenance to the canonical row rather than writing
        # a second copy, and leave a redirect so the old id still resolves.
        if not dry_run:
            await _merge_provenance(prior["v2_id"], provenance)
            import asyncio
            await asyncio.to_thread(_sync_record_redirect, db.global_path(),
                                    v1_id, prior["v2_id"])
        return AuditLine(v1_id=v1_id, action="merge", v2_ids=[prior["v2_id"]],
                         class_assigned=cls, class_source=cls_source,
                         canonical_tags=tags,
                         provenance_reconstructed=provenance is not None,
                         provenance_source=prov_source,
                         merged_into=prior["v2_id"], redirect_from=v1_id)

    scope = ["global"]
    injection_mode = injection_mode_for(cls, scope)

    constitution_meta = None
    if cls == "constitutional":
        # v1 has no ratification metadata; synthesize a legacy marker so the
        # memo is valid and visibly un-ratified. The operator can re-issue it
        # through the proposal workflow for real versioned metadata.
        constitution_meta = {
            "version": BACKFILL_VERSION,
            "ratified_at": memo.get("created_at") or now,
            "amended_at": memo.get("updated_at") or now,
            "incident_ref": backfill_ref,
        }

    # T122 / FR-002: preserve v1 bi-temporal semantics. valid_from is the v1
    # creation time, valid_until NULL — every v1 memo is current at migration.
    valid_from = memo.get("created_at") or now

    v2_id = memo["id"]        # keep the id so v1 references keep resolving
    if not dry_run:
        import asyncio
        await asyncio.to_thread(
            _sync_write_v2, db.global_path(), content=content,
            title=memo.get("title"), tags=tags, metadata=memo.get("metadata") or {},
            cls=cls, injection_mode=injection_mode, scope=scope,
            provenance=provenance, valid_from=valid_from,
            created_at=memo.get("created_at") or now,
            updated_at=memo.get("updated_at") or now,
            constitution_meta=constitution_meta, derived_from=[],
            doc_id=v2_id, embedding=embedding,
        )

    migrated.append({"v2_id": v2_id, "embedding": embedding,
                     "title_grams": title_grams})
    return AuditLine(v1_id=v1_id, action="write-new", v2_ids=[v2_id],
                     class_assigned=cls, class_source=cls_source,
                     canonical_tags=tags,
                     provenance_reconstructed=provenance is not None,
                     provenance_source=prov_source)


async def _merge_provenance(v2_id: str, provenance: dict | None) -> None:
    """Attach provenance to an existing v2 memo that lacks it."""
    if not provenance:
        return
    import asyncio

    def _sync():
        conn = db._get_or_create_conn(db.global_path())
        row = conn.execute("SELECT provenance FROM documents WHERE id = ?",
                           (v2_id,)).fetchone()
        if row is None or row["provenance"]:
            return                      # already attributed; don't overwrite
        conn.execute("UPDATE documents SET provenance = ? WHERE id = ?",
                     (json.dumps(provenance), v2_id))
        conn.commit()
    await asyncio.to_thread(_sync)


async def migrate_corpus(memos: Iterable[dict[str, Any]], *, dry_run: bool = True,
                         audit_path: str | None = None,
                         now: float | None = None,
                         embed=None) -> tuple[MigrationStats, list[AuditLine]]:
    """Migrate a whole corpus. [001/FR-039 001/FR-040]

    `embed` is injectable so a rehearsal can reuse v1's stored embeddings
    instead of paying to recompute 7,000 of them.
    """
    embed = embed or embeddings.embed
    stats = MigrationStats()
    lines: list[AuditLine] = []
    migrated: list[dict] = []

    fh = open(audit_path, "a") if audit_path else None
    try:
        for memo in memos:
            stats.total += 1
            try:
                vector = memo.get("embedding") or await embed(memo.get("content") or "")
                line = await migrate_one(memo, embedding=vector, migrated=migrated,
                                         dry_run=dry_run, now=now)
            except Exception as e:
                logger.exception("migration failed for %s", memo.get("id"))
                line = AuditLine(v1_id=memo.get("id") or "?", action="skip",
                                 note=f"error: {e}")

            lines.append(line)
            if fh:
                fh.write(line.to_json() + "\n")

            if line.action == "write-new":
                stats.written += 1
            elif line.action == "merge":
                stats.merged += 1
                stats.redirected += 1
            elif line.action == "skip":
                stats.skipped += 1
            if line.class_assigned:
                stats.by_class[line.class_assigned] = \
                    stats.by_class.get(line.class_assigned, 0) + 1
                if line.class_assigned == "legacy-unattributed":
                    stats.legacy_unattributed += 1
    finally:
        if fh:
            fh.close()
    return stats, lines


def resolve_v1_id(v1_id: str) -> str | None:
    """Resolve a v1 id through the redirect table. [001/FR-039]"""
    return _sync_lookup_redirect(db.global_path(), v1_id)


async def rollback() -> dict[str, int]:
    """Clear v2's corpus so migration can re-run clean. [001/FR-040]

    Touches ONLY v2. v1 is never written by any code path in this package, so
    reversibility is structural rather than something this function has to
    achieve.
    """
    import asyncio

    def _sync() -> dict[str, int]:
        conn = db._get_or_create_conn(db.global_path())
        n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.execute("DELETE FROM document_embeddings")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM supersede_edges")
        try:
            conn.execute("DELETE FROM migration_redirects")
        except Exception:
            pass
        conn.commit()
        return {"deleted": n}
    return await asyncio.to_thread(_sync)
