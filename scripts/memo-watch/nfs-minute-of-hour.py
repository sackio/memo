#!/usr/bin/env python3
# Is a stall pattern clustered by TIME OF DAY (minute of the hour)?
#
# WHY IT EXISTS: 2026-10-22, all 6 of that day's memo-watch heartbeat UNREADABLE
# alerts landed at :20 past the hour, against a check that runs :00/:20/:40.
# That looks like a scheduled job and it is worth exactly one test, because the
# pattern was found by LOOKING AT THE DATA -- a p computed on a pattern you went
# hunting for, over an unbounded set of patterns you might have noticed instead,
# is not a p. Run it on an INDEPENDENT log before believing anything.
#
# ⛔ EPISODE LEVEL, NOT SAMPLE LEVEL. Stalls on this mount arrive in episodes
#    (measured: a sample next to a stalled one is ~1.3-1.7x likelier to stall),
#    so 37k samples are nowhere near 37k independent observations and a naive
#    chi-square over rows manufactures significance. The unit here is one binary
#    per (host, hour, minute) cell: "did this cell contain a stall at all".
#
# ⭐ READ THE RANK, NOT THE p. "minute 20 ranks 24 of 60" is the whole answer;
#    with 60 candidate minutes, something always looks like the winner.
#
# VERDICT 2026-10-22 on armA-a31-running (37,637 samples, 6 hosts, 09:29-16:00):
#    minute 20 ranked 24/60 at >1s and 28/60 at >10s, chi2 1.84 and 0.22.
#    THE :20 PATTERN IS NOT REAL. Do not go looking for a cron at :20.
#
# usage: python3 -I nfs-minute-of-hour.py <dir-of-logs>
#        log rows: <iso8601> <host> <rc> <elapsed_ms>   (# comments skipped)

import sys, glob, os
from collections import defaultdict

D = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(D, "*.log"))):
    for ln in open(f):
        if ln.startswith("#"): continue
        p = ln.split()
        if len(p) != 4: continue
        ts, host, rc, ms = p
        # 2026-10-22T09:29:00-04:00
        hh = int(ts[11:13]); mm = int(ts[14:16])
        rows.append((host, hh, mm, int(ms)))

print(f"samples={len(rows)} hosts={len(set(r[0] for r in rows))}")

for THRESH in (1000, 10000):
    # --- descriptive: naive per-sample rate by minute ---
    n_by_m = defaultdict(int); s_by_m = defaultdict(int)
    for host, hh, mm, ms in rows:
        n_by_m[mm] += 1
        if ms > THRESH: s_by_m[mm] += 1

    # --- inferential: episode level, one binary per (host, hour, minute) ---
    cells = defaultdict(bool)
    seen  = set()
    for host, hh, mm, ms in rows:
        seen.add((host, hh, mm))
        if ms > THRESH: cells[(host, hh, mm)] = True

    hit = defaultdict(int); tot = defaultdict(int)
    for k in seen:
        tot[k[2]] += 1
        if cells[k]: hit[k[2]] += 1

    print(f"\n===== stall threshold >{THRESH}ms =====")
    print("minute-of-hour profile (episode level: cells with >=1 stall / cells):")
    line = []
    for m in range(60):
        if tot[m]:
            line.append(f"{m:02d}:{hit[m]}/{tot[m]}")
    print("  " + "  ".join(line))

    # target = minute 20 vs rest
    a = hit[20]; b = tot[20] - hit[20]
    c = sum(hit[m] for m in range(60) if m != 20)
    d = sum(tot[m] for m in range(60) if m != 20) - c
    print(f"\n  minute 20 : {a}/{a+b} cells stalled = {a/(a+b)*100:.1f}%")
    print(f"  all others: {c}/{c+d} cells stalled = {c/(c+d)*100:.1f}%")
    try:
        from scipy.stats import fisher_exact
        orr, p = fisher_exact([[a, b], [c, d]])
        print(f"  Fisher exact: OR={orr:.2f} p={p:.4f}")
    except Exception as e:
        # chi2 by hand
        N = a+b+c+d
        import math
        e_a = (a+b)*(a+c)/N
        if e_a > 0:
            chi2 = sum((o-e)**2/e for o,e in [
                (a,(a+b)*(a+c)/N),(b,(a+b)*(b+d)/N),(c,(c+d)*(a+c)/N),(d,(c+d)*(b+d)/N)])
            print(f"  chi2={chi2:.2f} (1 df; 3.84=p.05, 10.8=p.001)  [scipy absent: {type(e).__name__}]")

    # how many minutes beat minute 20? -- the multiple-comparisons reality check
    rates = sorted(((hit[m]/tot[m], m) for m in range(60) if tot[m] >= 5), reverse=True)
    print("  top 6 minutes by episode stall rate:", [(f"{m:02d}", f"{r*100:.0f}%") for r, m in rates[:6]])
    rank = [m for _, m in rates].index(20) + 1 if any(m == 20 for _, m in rates) else None
    print(f"  minute 20 ranks {rank} of {len(rates)} minutes")
