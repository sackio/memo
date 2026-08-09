#!/usr/bin/env python3
"""Is the 95-in-one-band failure cluster a TIME window or a CONTENT property?

`embeddings` proposed the clock explanation and the bench's outer loop being
over BANDS makes it mechanically possible. This asks the sharper question:
WHERE in the 500-1000 band's query sequence did the failures fall?

  contiguous run  => clock artefact. The stall hit a wall-time interval and the
                     band is merely where the run happened to be standing.
  scattered       => a rate, not a window — and the clock story is WRONG despite
                     being mechanically available.

The sample order is random.sample(pool, k) under seed 7, so it is exactly
reproducible. This does not re-measure anything; it re-derives the order the
instrument used and locates the failures in it.
"""
import json, random, re, sys, urllib.request

URL = "http://localhost:8091"
BANDS = [(0, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 10**9)]
SEED = 7
PER_BAND = 20000  # the run used the full band (n= matches pool size)


def get(path, timeout=300):
    with urllib.request.urlopen(URL.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


def passage_indexed_ids():
    # mirror memo-retrieval-bench's helper
    try:
        return set(get("/passage-indexed-ids"))
    except Exception:
        rows = get("/documents?limit=20000")
        return {d["id"] for d in rows}


def main():
    txt = open(sys.argv[1]).read()
    failed8 = re.findall(r"query failed for ([0-9a-f]{8}):", txt)
    order_of_failure = {f: i for i, f in enumerate(failed8)}
    print(f"{len(failed8)} failures in the report, in occurrence order\n")

    docs = get("/documents?limit=20000")
    eligible = passage_indexed_ids()
    docs = [d for d in docs if d["id"] in eligible]
    random.seed(SEED)

    for lo, hi in BANDS:
        pool = [d for d in docs
                if lo <= (d.get("token_count") or 0) < hi
                and (d.get("title") or "").strip()]
        sample = random.sample(pool, min(PER_BAND, len(pool)))
        # ^ must be drawn for EVERY band in order: random state is shared, so
        #   skipping a band would desynchronise every later one.
        label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
        pos = [i for i, d in enumerate(sample) if d["id"][:8] in order_of_failure]
        if not pos:
            print(f"{label:>12}  n={len(sample):5}   no failures")
            continue
        span = pos[-1] - pos[0] + 1
        print(f"{label:>12}  n={len(sample):5}   failures={len(pos):4}  "
              f"first={pos[0]}  last={pos[-1]}  span={span}  "
              f"density-in-span={len(pos)/span:.1%}")
        if len(pos) > 3:
            gaps = [b - a for a, b in zip(pos, pos[1:])]
            print(f"{'':>12}  gaps: max={max(gaps)} median="
                  f"{sorted(gaps)[len(gaps)//2]}  "
                  f"(1 = perfectly contiguous)")
    print("\nA clock artefact concentrates in a SPAN with high density inside it.")
    print("A content property spreads across the whole band uniformly.")


if __name__ == "__main__":
    sys.exit(main())
