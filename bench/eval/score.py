#!/usr/bin/env python3
"""Score memo's retrieval against the eval set. See README.md for why this exists.

Usage (inside the container, or anywhere that can reach the store):
    python3 bench/eval/score.py [--k 10] [--limit 50] [--base http://server4:8000]

⛔ Reports the DENOMINATOR and the DATE on every run. The corpus changes daily —
~141 new documents on 2026-09-10 alone — so a recall figure without them cannot
be compared to the next run, and two numbers from different days are not a trend.
"""
import argparse, json, sys, time, urllib.request, urllib.error
from pathlib import Path

def search(base, query, limit):
    body = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(f"{base}/search", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    # ⚠️ /search returns a TOP-LEVEL ARRAY, not {"results": [...]}. Writing
    # `.get("results", [])` here raises AttributeError on a healthy response, and
    # inside a try/except it silently scores every query as a miss — which would
    # make retrieval look broken when nothing is. Measured 2026-08-22.
    rows = d if isinstance(d, list) else d.get("results", [])
    return [x["document"]["id"] for x in rows]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--base", default="http://server4:8000")
    ap.add_argument("--queries", default=str(Path(__file__).parent / "queries.jsonl"))
    a = ap.parse_args()

    p = Path(a.queries)
    if not p.exists() or not p.read_text().strip():
        # ⛔ An empty eval set is NOT a passing eval set. Exit non-zero and say so,
        # rather than printing "recall@10 = 0/0" and letting a caller read a green
        # run. That is the reassuring-direction failure this whole file exists to
        # avoid committing itself.
        print(f"⛔ {p} is empty or missing — NOTHING WAS MEASURED.", file=sys.stderr)
        print("   This is not a score of 0 and not a score of 1: no query ran.",
              file=sys.stderr)
        return 2

    qs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    hits_at_k = 0
    rr_total = 0.0
    misses = []
    errors = []
    for q in qs:
        want = set(q["expect_ids"])
        try:
            got = search(a.base, q["query"], a.limit)
        except Exception as e:
            # ⛔ An error is NOT a miss. Counting it as one understates retrieval
            # and hides an outage as a quality problem.
            errors.append((q["id"], type(e).__name__))
            continue
        topk = got[:a.k]
        if want & set(topk):
            hits_at_k += 1
        else:
            misses.append((q["id"], q["query"][:60]))
        for rank, doc_id in enumerate(got, 1):
            if doc_id in want:
                rr_total += 1.0 / rank
                break

    scored = len(qs) - len(errors)
    print(f"date         : {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"store        : {a.base}")
    print(f"queries      : {len(qs)} in file | {scored} scored | {len(errors)} errored")
    if scored:
        print(f"recall@{a.k:<6}: {hits_at_k}/{scored} = {hits_at_k/scored:.3f}")
        print(f"MRR (@{a.limit})   : {rr_total/scored:.3f}")
    if errors:
        print(f"⚠️ ERRORED (not counted as misses): {errors}")
    if misses:
        print(f"\nmisses ({len(misses)}) — these are the reranker's whole case:")
        for qid, text in misses:
            print(f"  {qid}  {text}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
