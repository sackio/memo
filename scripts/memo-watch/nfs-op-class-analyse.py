#!/usr/bin/env python3
"""Compare NFS operation classes from nfs-op-class-probe logs.

Usage: nfs-op-class-analyse.py <log> [<log> ...]
Rows: <iso8601> <host> <op> <rc> <elapsed_ms>

⛔ DO NOT COMPUTE EPISODES PER OP. The ops are interleaved, so consecutive samples of the
   same op are a full round apart and an "episode" in that subsequence is an artifact of
   the round length. Episodes are defined on a single-op series; this file has four.

⭐ THE PAIRED TEST IS THE POINT. /mnt/nas stalls are strongly clustered in time, so the
   marginal stall rate of two ops measured over the same hours still shares most of its
   variance. Within one ROUND the ops are 2s apart and see the same conditions, so
   McNemar on discordant rounds -- rounds where exactly one of the two ops stalled -- is
   the comparison that is not dominated by episode clustering.
"""
import sys, math
from collections import defaultdict
from datetime import datetime

THRESH_MS = 1000
OPS = ["setattr_hot", "setattr_cold", "create_own", "create_shared", "datawrite"]


def quantile(v, q):
    if not v:
        return float("nan")
    s = sorted(v); k = (len(s) - 1) * q; f, c = math.floor(k), math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def main(paths):
    rows = []
    for p in paths:
        n = 0
        for ln in open(p):
            if ln.startswith("#"):
                continue
            q = ln.split()
            if len(q) != 5:
                continue
            try:
                rows.append((datetime.fromisoformat(q[0]), q[1], q[2], int(q[3]), int(q[4])))
                n += 1
            except ValueError:
                pass
        if n == 0:
            print("⛔ %s: 0 parsed rows -- NOT a zero, named and excluded" % p)
    if not rows:
        return 1
    rows.sort()
    hosts = sorted({r[1] for r in rows})
    print("samples=%d hosts=%d (%s)\n" % (len(rows), len(hosts), " ".join(hosts)))

    print("%-14s %7s %8s %8s %8s %9s %9s %9s" % (
        "op", "n", "p50ms", "p95ms", "maxms", "%>1s", "%ofwall", "rc!=0"))
    bucket = defaultdict(list)
    for _, _, op, rc, ms in rows:
        bucket[op].append((rc, ms))
    span_s = (rows[-1][0] - rows[0][0]).total_seconds() * len(hosts)
    # ⛔ %ofwall IS EACH OP'S SHARE OF THE WHOLE WINDOW, NOT ITS OWN STALL RATE.
    #    The ops are serial on one timeline, so they divide one wall clock between them
    #    and these four figures SUM to the window's total stalled fraction. Dividing by
    #    4 to give each op "its own" quarter produced 262% on the first run -- a stalled
    #    op consumes wall clock the other three never got.
    for op in OPS:
        v = bucket.get(op) or []
        if not v:
            continue
        lat = [ms for _, ms in v]
        st = [ms for ms in lat if ms > THRESH_MS]
        print("%-14s %7d %8.0f %8.0f %8.0f %8.1f%% %8.1f%% %9d" % (
            op, len(v), quantile(lat, .5), quantile(lat, .95), max(lat),
            100 * len(st) / len(v), 100 * sum(st) / 1000.0 / span_s,
            sum(1 for rc, _ in v if rc != 0)))

    # pair ops by (host, round): a round is one pass of the round-robin
    rounds = defaultdict(dict)
    seq = defaultdict(int)
    for t, host, op, rc, ms in rows:
        if op == OPS[0]:
            seq[host] += 1
        rounds[(host, seq[host])][op] = ms
    print("\nPAIRED, within a round (McNemar on discordant rounds, stall = >%dms):" % THRESH_MS)
    print("%-32s %8s %8s %8s %9s %8s" % ("A vs B", "rounds", "A only", "B only", "chi2", "p"))
    for a, b in (("setattr_cold", "create_own"),      # the restated discriminator
                 ("create_own", "create_shared"),      # directory contention across hosts
                 ("setattr_hot", "setattr_cold"),      # hot inode vs cold inode
                 ("setattr_cold", "datawrite"),        # is there a data-write term at all
                 ("create_own", "datawrite")):
        n01 = n10 = n = 0
        for v in rounds.values():
            if a in v and b in v:
                n += 1
                sa, sb = v[a] > THRESH_MS, v[b] > THRESH_MS
                if sa and not sb:
                    n10 += 1
                elif sb and not sa:
                    n01 += 1
        if n01 + n10 == 0:
            print("%-32s %8d %8d %8d %9s %8s" % (f"{a} vs {b}", n, n10, n01, "-", "-")); continue
        chi2 = (abs(n10 - n01) - 1) ** 2 / (n10 + n01)
        # upper tail of chi2 with 1 df == 2*(1-Phi(sqrt(chi2)))
        p = math.erfc(math.sqrt(chi2 / 2.0))
        print("%-32s %8d %8d %8d %9.2f %8.4f" % (f"{a} vs {b}", n, n10, n01, chi2, p))
    print("\n⚠️ Magnitudes are FLOORS: timeout cannot interrupt uninterruptible sleep.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
