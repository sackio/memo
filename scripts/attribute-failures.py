#!/usr/bin/env python3
"""Attribute each failure episode to a CAUSE by timestamping it.

Three accounts were live and all three are incomplete:
  mine        "~79 in a 70-minute near-stall"     — one window
  embeddings  "the 20:20-21:30 hole, backup cron" — one window, named+fixed
  neither     32 failures sit outside any single window

The bench queries bands in order, so a query's ordinal position is a clock. The
container access log gives requests-per-minute. Cumulative requests therefore
map position -> wall time, and every episode gets a timestamp and a suspect.

⚠️ The mapping is approximate at the minute and DEGRADES INSIDE A STALL: during
a hole no requests complete, so cumulative count is flat and any position
landing there maps to the whole hole rather than an instant. That is the right
failure mode here — it is exactly the interval we want to name — but it means
this must NOT be used to order events finely inside a gap.
"""
import re, subprocess, sys
from datetime import datetime, timedelta

# band sizes as the run reported them, in the order the bench queries them
BAND_N = [("0-200", 1216), ("200-500", 2293), ("500-1000", 2007),
          ("1000-2000", 1306), ("2000+", 399)]

EPISODES = {  # band -> [(start_pos, end_pos, count)] from stall-clusters.py
    "0-200":     [(0, 1, 2)],
    "500-1000":  [(14, 15, 2), (276, 281, 6), (466, 537, 71), (1160, 1168, 9),
                  (1284, 1285, 2), (1301, 1301, 1), (1320, 1320, 1),
                  (1388, 1388, 1), (1853, 1853, 1), (1958, 1958, 1)],
    "1000-2000": [(169, 169, 1), (259, 259, 1), (318, 319, 2), (918, 918, 1),
                  (1102, 1102, 1)],
}

CENSUS_END = datetime(2026, 8, 3, 1, 0, 6)  # EDT, from the artifact mtime


def per_minute():
    out = subprocess.run(
        ["docker", "compose", "logs", "-t", "--no-color", "--since", "24h",
         "memo-v2"], cwd="/mnt/nas/data/code/memo-v2",
        capture_output=True, text=True).stdout
    counts = {}
    for line in out.splitlines():
        if "search-passages" not in line:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", line)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M") - timedelta(hours=4)
        if t > CENSUS_END:      # exclude the repair runs and my probes
            continue
        counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items())


def main():
    rows = per_minute()
    total = sum(c for _, c in rows)
    print(f"access log: {total} completed passage requests, "
          f"{rows[0][0]:%H:%M} - {rows[-1][0]:%H:%M} EDT\n")

    # cumulative -> time
    cum, timeline = 0, []
    for t, c in rows:
        timeline.append((cum, t))
        cum += c

    def when(pos):
        best = timeline[0][1]
        for c, t in timeline:
            if c <= pos:
                best = t
            else:
                break
        return best

    offset = {}
    run = 0
    for name, n in BAND_N:
        offset[name] = run
        run += n

    print(f"{'band':>11} {'pos':>11} {'n':>4}   {'window (EDT)':<15} suspect")
    print("-" * 74)
    flat = []
    for band, eps in EPISODES.items():
        for a, b, n in eps:
            flat.append((offset[band] + a, offset[band] + b, n, band, a, b))
    for absa, absb, n, band, a, b in sorted(flat):
        t0, t1 = when(absa), when(absb)
        w = f"{t0:%H:%M}-{t1:%H:%M}"
        print(f"{band:>11} {f'{a}-{b}':>11} {n:4}   {w:<15}")
    print("\n⚠️ Positions inside a hole all map to the hole's edge — see docstring.")


if __name__ == "__main__":
    sys.exit(main())
