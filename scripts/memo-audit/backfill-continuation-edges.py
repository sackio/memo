#!/usr/bin/env python3
"""Reclassify the supersede edges that are really CONTINUATIONS. [migration 014]

AUTHORISED by Ben, Slack #memo 2026-10-24 ("go"), following the 2026-10-23 11:05
approval to clear `valid_until` on the same 52 memos. That clearing made them
readable again; this makes the RELATION true. Until it runs, all 52 parents
still report `superseded_by: <child>` — the corpus still says they were
replaced, which is the claim that was wrong in the first place.

⛔⛔ THIS IS THE LAST TIME PROSE IS THE SOURCE OF TRUTH FOR A CONTINUATION, AND
   THAT IS THE WHOLE REASON THE SCHEMA CHANGED. These 52 edges carry no record
   of what they meant, so the only evidence available is the wording of the
   memos ("PART n", "continues <id>"). A regex over prose is exactly the
   guessing that migration 014 exists to retire; it is acceptable here because
   the set is fixed, finite, already reviewed, and every row is printed before
   anything is written. New links declare their own kind and need no detector.

⛔ WHY IT WRITES SQL AND NOT HTTP. There is no API that mutates an edge, and
   adding one would create a route that can retire or un-retire a memo by
   changing a single field — a destructive surface built for a one-off. The
   write is one UPDATE over a listed set of rowids, inside a transaction, with
   the prior state dumped to a file first.

⚠️ IT DOES NOT TOUCH `valid_until`. Those were already cleared on 2026-10-24
   and all 52 are live; a parent that is NOT live is reported and skipped,
   because that combination means something changed since and the operator
   should look rather than have this script decide.

Usage (runs inside the memo-v2 container, where /data/memo.db lives):
    docker exec -i memo-v2 python - < backfill-continuation-edges.py            # dry run
    docker exec -i memo-v2 python - < backfill-continuation-edges.py -- --apply
"""
import json
import os
import re
import sqlite3
import sys
import time

DB = os.environ.get("MEMO_DB", "/data/memo.db")
CONT = re.compile(r"\bPART\s*\d|\bcontinues\b|\bcontinuation\b|\(continued\b|\bwhich is FULL\b", re.I)
DUMP = "/tmp/continuation-edges-before-%d.json" % int(time.time())


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")

    docs = {r["id"]: r for r in conn.execute(
        "SELECT id, title, content, valid_until FROM documents")}
    edges = conn.execute(
        "SELECT id, old_id, new_id, kind FROM supersede_edges WHERE kind = 'supersede'"
    ).fetchall()

    targets, skipped = [], []
    for e in edges:
        parent, child = docs.get(e["old_id"]), docs.get(e["new_id"])
        if parent is None or child is None:
            continue
        blob = (child["title"] or "") + " " + (child["content"] or "")[:400]
        if not (CONT.search(blob) or CONT.search(parent["title"] or "")):
            continue
        # A parent that is NOT live means the 2026-10-24 clearing did not cover
        # this edge, or something has changed since. Report, do not decide.
        if parent["valid_until"] is not None:
            skipped.append(e)
            continue
        targets.append(e)

    print(f"supersede edges that look like continuations: {len(targets)}")
    if skipped:
        print(f"⚠️ SKIPPED {len(skipped)} whose parent is still RETIRED — these need a "
              f"human: clearing the expiry is a separate, already-authorised act.")
        for e in skipped[:10]:
            print(f"   edge {e['id']}  {e['old_id'][:8]} -> {e['new_id'][:8]}")

    for e in targets[:60]:
        print(f"   edge {e['id']:4d}  {e['old_id'][:8]} -> {e['new_id'][:8]}  "
              f"{(docs[e['new_id']]['title'] or '')[:66]}")

    if not apply:
        print("\n(dry run — nothing written. Re-run with --apply)")
        return 0
    if not targets:
        print("nothing to do")
        return 0

    with open(DUMP, "w") as fh:
        json.dump([dict(e) for e in targets], fh, indent=1)
    print(f"\nprior state dumped to {DUMP} ({len(targets)} rows)")

    ids = [e["id"] for e in targets]
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany("UPDATE supersede_edges SET kind = 'continuation' WHERE id = ?",
                     [(i,) for i in ids])
    conn.commit()

    # ⛔ READ BACK. A committed UPDATE is not a verified one, and this file's
    # sibling script exists because 52 PATCHes returned 200 and changed nothing.
    back = {r["id"]: r["kind"] for r in conn.execute(
        "SELECT id, kind FROM supersede_edges WHERE id IN (%s)"
        % ",".join("?" * len(ids)), ids)}
    ok = sum(1 for i in ids if back.get(i) == "continuation")
    still_retired = conn.execute(
        "SELECT COUNT(*) FROM supersede_edges e JOIN documents d ON d.id = e.old_id "
        "WHERE e.kind = 'continuation' AND d.valid_until IS NOT NULL").fetchone()[0]
    print(f"reclassified (read-back): {ok}   failed: {len(ids) - ok}   of {len(ids)}")
    print(f"continuation edges whose parent is retired (must be 0): {still_retired}")
    return 0 if ok == len(ids) and still_retired == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
