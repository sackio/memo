#!/usr/bin/env python3
"""Sample N memos per rank band, to measure where auditing stops paying.

⭐ WHY THIS EXISTS AND WHY IT IS NOT "THE NEXT BLOCK". Stage 2 (ranks 1-24) and
stage 3 (ranks 25-72) changed 23/24 and 44/48 -- a ~94% hit rate. Extrapolating
that to the remaining ~10,000 memos would authorise roughly $3,000 of spend on
the strength of a sample drawn entirely from the TOP of the ranking, which is
precisely where the ranker claims the hits are. A high hit rate at rank 20 is
evidence the ranker works, not evidence that rank 5,000 is worth auditing.

⇒ Sample ACROSS the ranking, audit the sample, and read the change rate per
band. That turns the stop point into a measurement. At the measured rate the
experiment costs ~$19 and prices a $3,000 decision.

⚠️ The seed is fixed so the sample is reproducible and so a second run does not
quietly draw a different set and get compared to the first.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANDS = [(200, 500), (1000, 2500), (2500, 5000), (5000, 10123)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(HERE / "candidates-all.json"))
    ap.add_argument("--per-band", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--exclude-snapshots", nargs="*",
                    default=["stage2-before", "stage3-before"],
                    help="snapshot dirs whose memos were already audited")
    ap.add_argument("--exclude-candidates", nargs="*",
                    default=["candidates-credentials.json"])
    ap.add_argument("--out", default=str(HERE / "candidates-stratified.json"))
    a = ap.parse_args()

    random.seed(a.seed)
    allc = json.loads(Path(a.candidates).read_text())

    # ⛔ Two memos must never be assigned to concurrent passes -- one document
    # with two writers is how an append-retry duplicates and a compare-and-set
    # starts failing for reasons nobody can reproduce.
    skip: set[str] = set()
    for d in a.exclude_snapshots:
        p = HERE / d
        if not p.is_dir():
            continue
        for fn in os.listdir(p):
            if fn.endswith(".json"):
                skip.add(json.loads((p / fn).read_text())["id"])
    for f in a.exclude_candidates:
        p = Path(f) if os.path.isabs(f) else HERE / f
        if p.exists():
            skip |= {c["id"] for c in json.loads(p.read_text())}

    sel = []
    for lo, hi in BANDS:
        pool = [m for m in allc[lo:hi] if m["id"] not in skip]
        if len(pool) < a.per_band:
            print(f"  ⚠️ band {lo}-{hi}: only {len(pool)} available, taking all")
        pick = random.sample(pool, min(a.per_band, len(pool)))
        for m in pick:
            m["_band"] = f"{lo}-{hi}"
        sel += pick
        s = [m["score"] for m in pick]
        print(f"  band {lo}-{hi}: {len(pick)} picked, score {max(s):.1f}..{min(s):.1f}, "
              f"mean {sum(m['chars'] for m in pick) // len(pick)} chars")

    Path(a.out).write_text(json.dumps(sel, indent=1))
    print(f"\n{len(sel)} memos, {sum(m['chars'] for m in sel)} chars -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
