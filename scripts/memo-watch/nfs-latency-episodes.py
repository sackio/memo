#!/usr/bin/env python3
"""Episode analysis for nfs-latency-probe logs.

Usage: nfs-latency-episodes.py <log> [<log> ...]

Log format, one row per probe:  <iso8601> <host> <rc> <elapsed_ms>
'#' lines are the arm header and the ENDED marker.

⛔⛔ DO NOT REPORT A PROBE-LEVEL STALL RATE. IT IS PERVERSELY SIGNED.
A serial sampler records about ONE stalled sample per episode however long the episode
lasts (measured 1.17-1.33 per episode on every host, both arms, 2026-10-22). During a 30s
stall it logs one sample, not fifteen, so a LONGER stall yields FEWER stalled samples per
minute and the rate FALLS as the problem WORSENS. Measured that day: probe-level gave
stopped 10.75% vs running 17.22%, p=0.012 -- an artifact of the instrument, reported as a
finding before `cluster` caught the autocorrelation.

⛔⛔ AND NEITHER OF THE REPLACEMENTS POOLS ACROSS INSTRUMENTS OF DIFFERENT CADENCE.
MEASURED 2026-10-22 by downsampling THIS dataset (--cadence-control), so it is a property
of the quantities, not of any one sampler:

  EPISODES/MIN collapses 2.68 -> 0.22 (12x) when the same 2s series is thinned to one
  sample per minute. An episode rate is CEILINGED BY CADENCE: a per-minute sampler needs a
  clean sample between two episodes, so it can never report above 0.5/min, and the arm A
  median episode is 5.6s -- an order of magnitude below its resolution.

  PERCENT OF MINUTES CONTAINING A STALL moves the OTHER way: 95-96% at 2s cadence against
  `cluster`'s 87.3% at 1/min, because more samples per minute means more chances to mark
  the minute. Both headline numbers are cadence-sensitive, in opposite directions.

⭐ THE ONLY CADENCE-ROBUST QUANTITY IS THE TIME FRACTION -- sum of stalled sample durations
  over the window span -- because it is a ratio of durations, not a count and not an
  indicator. Pool that; never pool the other two. ⚠️ It is still a FLOOR: SIGTERM cannot
  interrupt uninterruptible sleep, so `timeout 60` returned samples of 155s on every host.
"""
import sys, os, math
from datetime import datetime

THRESH_MS = 1000


def quantile(v, q):
    if not v:
        return float("nan")
    s = sorted(v)
    k = (len(s) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def read(path):
    rows = []
    for ln in open(path):
        if ln.startswith("#"):
            continue
        p = ln.split()
        if len(p) != 4:
            continue
        try:
            rows.append((datetime.fromisoformat(p[0]), p[1], int(p[2]), int(p[3])))
        except ValueError:
            pass
    rows.sort()
    return rows


def episodes(latencies):
    """Maximal runs of consecutive samples over THRESH_MS. Returns (count, [durations_ms])."""
    n, inep, cur, out = 0, False, 0, []
    for ms in latencies:
        if ms > THRESH_MS:
            cur += ms
            if not inep:
                n += 1
                inep = True
        else:
            if inep:
                out.append(cur)
                cur = 0
            inep = False
    if inep:
        out.append(cur)
    return n, out


def main(paths, control=False):
    per = {}
    for p in paths:
        rows = read(p)
        if len(rows) < 2:
            # ⛔ A SHORT OR MISSING LOG IS NOT A ZERO.
            print("⛔ %s: %d parsed rows -- NOT a zero, EXCLUDED and named" % (p, len(rows)))
            continue
        host = rows[0][1]
        span_s = (rows[-1][0] - rows[0][0]).total_seconds()
        lat = [r[3] for r in rows]
        n_ep, ep_ms = episodes(lat)
        per[host] = dict(
            rows=rows, n=len(rows), span_m=span_s / 60.0, eps=n_ep, ep_ms=ep_ms,
            stalled_s=sum(ms for ms in lat if ms > THRESH_MS) / 1000.0,
            p50=quantile(lat, .50), p95=quantile(lat, .95), mx=max(lat),
            timedout=sum(1 for r in rows if r[2] != 0))
    if not per:
        print("no usable logs"); return 1

    print("%-9s %7s %7s %9s %8s %8s %7s %8s %8s %8s %9s" % (
        "host", "probes", "span_m", "EPISODES", "ep/min", "%wall", "p50ms", "p95ms",
        "maxms", "meanEPs", "timedout"))
    for h, v in sorted(per.items()):
        print("%-9s %7d %7.1f %9d %8.2f %8.1f %7.0f %8.0f %8.0f %8.1f %9d" % (
            h, v["n"], v["span_m"], v["eps"], v["eps"] / v["span_m"],
            100 * v["stalled_s"] / (v["span_m"] * 60), v["p50"], v["p95"], v["mx"],
            sum(v["ep_ms"]) / len(v["ep_ms"]) / 1000.0 if v["ep_ms"] else 0.0,
            v["timedout"]))

    E = sum(v["eps"] for v in per.values())
    T = sum(v["span_m"] for v in per.values())
    S = sum(v["stalled_s"] for v in per.values())
    allep = [x for v in per.values() for x in v["ep_ms"]]
    print("\nPOOLED  hosts=%d  episodes=%d  host-minutes=%.1f  rate=%.2f/min"
          "  TIME FRACTION STALLED %.1f%% (the poolable one)" % (
              len(per), E, T, E / T, 100 * S / (T * 60)))
    if allep:
        print("  episode duration s: p50=%.1f p95=%.1f max=%.1f mean=%.1f (n=%d)" % (
            quantile(allep, .5) / 1000, quantile(allep, .95) / 1000, max(allep) / 1000,
            sum(allep) / len(allep) / 1000, len(allep)))

    if control:
        print("\n--- CADENCE CONTROL: the same series thinned to one sample per minute ---")
        print("%-9s %12s %12s %8s %14s %14s" % (
            "host", "ep/min 2s", "ep/min 1min", "ratio", "%minutes 2s", "%minutes 1min"))
        for h, v in sorted(per.items()):
            seen, ds = set(), []
            minute_any, minute_ds = {}, {}
            for t, _, _, ms in v["rows"]:
                k = t.replace(second=0, microsecond=0)
                minute_any[k] = minute_any.get(k, False) or (ms > THRESH_MS)
                if k not in seen:
                    seen.add(k); ds.append(ms); minute_ds[k] = ms > THRESH_MS
            d_ep, _ = episodes(ds)
            full_r = v["eps"] / v["span_m"]
            ds_r = d_ep / v["span_m"]
            print("%-9s %12.2f %12.2f %7.1fx %13.1f%% %13.1f%%" % (
                h, full_r, ds_r, full_r / max(ds_r, 1e-9),
                100 * sum(minute_any.values()) / len(minute_any),
                100 * sum(minute_ds.values()) / len(minute_ds)))
        print("⇒ a 1/min sampler cannot exceed 0.50 episodes/min by construction.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cadence-control"]
    sys.exit(main(args, control="--cadence-control" in sys.argv[1:]))
