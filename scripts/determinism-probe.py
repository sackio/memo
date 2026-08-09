#!/usr/bin/env python3
"""Is passage retrieval actually deterministic? Measure the flip rate.

`memo-bench-repair` justifies patching failed queries on three premises, the
first being "retrieval is DETERMINISTIC". Two identical repair runs disagreed on
rank-1 by 1 of 95 — so the premise is approximately true, which is not the same
claim and does not warrant the same confidence.

⚠️ This was found BY ACCIDENT: the first run crashed writing its JSON, and only
because of that did a replicate exist at all. A premise that is asserted in a
docstring and never replicated is indistinguishable from one that is true.

Asks each title REPS times and reports how often the returned rank changes.
"""
import json, random, sys, urllib.request
from collections import Counter

URL = "http://localhost:8091"
REPS = 5
N = 25


def get(path, timeout=300):
    with urllib.request.urlopen(URL.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


def search(query, path, limit=10, timeout=90):
    req = urllib.request.Request(
        URL.rstrip("/") + ("/search-passages" if path == "passages"
                           else "/search-documents"),
        data=json.dumps({"query": query, "limit": limit}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "passages"
    docs = [d for d in get("/documents?limit=20000") if (d.get("title") or "").strip()]
    random.seed(11)          # a DIFFERENT seed from the bench's 7 — these must
    sample = random.sample(docs, N)   # not be the same queries the bench used

    print(f"path={path}  n={N} titles x {REPS} reps  (limit 10)\n")
    unstable = 0
    id_unstable = 0
    for d in sample:
        ranks, idsets = [], []
        for _ in range(REPS):
            try:
                hits = search((d.get("title") or "")[:200], path)
            except Exception as e:
                ranks.append(f"ERR")
                continue
            ids = [h.get("document", h)["id"] for h in hits]
            idsets.append(tuple(ids))
            ranks.append(ids.index(d["id"]) if d["id"] in ids else None)
        c = Counter(ranks)
        stable = len(c) == 1
        ids_stable = len(set(idsets)) <= 1
        if not stable:
            unstable += 1
        if not ids_stable:
            id_unstable += 1
        flag = "" if stable else "  <- RANK VARIES"
        if stable and not ids_stable:
            flag = "  <- rank same, ID LIST varies"
        print(f"  {d['id'][:8]} tok={str(d.get('token_count')):>5} "
              f"ranks={ranks}{flag}")

    print(f"\nrank varied on   {unstable}/{N} titles")
    print(f"id-list varied on {id_unstable}/{N} titles")
    print("\nA nonzero rate means repaired figures carry a replication error "
          "term,\nand that 'retrieval is deterministic' cannot be stated flatly.")


if __name__ == "__main__":
    sys.exit(main())
