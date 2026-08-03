#!/usr/bin/env python3
"""How many distinct stall EPISODES, and how big is each?

The contiguity probe showed median gap 1 but span ~= the whole band: failures
arrive in tight runs separated by healthy stretches. That is neither "one
70-minute window" (my account) nor "a scattered rate" (the content account).
This counts the episodes.
"""
import json, random, re, sys, urllib.request

URL = "http://localhost:8091"
BANDS = [(0, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 10**9)]


def get(path, timeout=300):
    with urllib.request.urlopen(URL.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


txt = open(sys.argv[1]).read()
failed8 = set(re.findall(r"query failed for ([0-9a-f]{8}):", txt))
GAP = int(sys.argv[2]) if len(sys.argv) > 2 else 3  # positions apart still "same episode"

docs = get("/documents?limit=20000")
try:
    eligible = set(get("/passage-indexed-ids"))
except Exception:
    eligible = {d["id"] for d in docs}
docs = [d for d in docs if d["id"] in eligible]
random.seed(7)

print(f"episode = failures no more than {GAP} queries apart\n")
for lo, hi in BANDS:
    pool = [d for d in docs
            if lo <= (d.get("token_count") or 0) < hi
            and (d.get("title") or "").strip()]
    sample = random.sample(pool, min(20000, len(pool)))
    label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
    pos = [i for i, d in enumerate(sample) if d["id"][:8] in failed8]
    if not pos:
        continue
    eps, cur = [], [pos[0]]
    for p in pos[1:]:
        if p - cur[-1] <= GAP:
            cur.append(p)
        else:
            eps.append(cur); cur = [p]
    eps.append(cur)
    print(f"{label}: {len(pos)} failures in {len(eps)} episode(s), band n={len(sample)}")
    for e in eps:
        width = e[-1] - e[0] + 1
        print(f"    pos {e[0]:5}-{e[-1]:5}  len={len(e):3}  width={width:3}  "
              f"{'SOLID' if len(e) == width else f'{len(e)/width:.0%} dense'}")
    print()
