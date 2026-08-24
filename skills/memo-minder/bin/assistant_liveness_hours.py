#!/usr/bin/env python3
"""Hours containing an actual `assistant`-seat turn, UNIONED ACROSS ALL FOUR HOSTS.

⛔ THIS SCRIPT WAS SINGLE-HOST UNTIL 2026-08-23 and contradicted the SKILL.md text
that calls it ("unioned across hosts"). Run on server4 it reported a last-active hour
of 2026-08-03 — a phantom 20-day gap — because the `assistant` seat MIGRATED TO
server3 on 2026-08-10 and a migrated seat leaves its old transcript frozen forever.
A liveness claim derived from one host is not a liveness claim.

⚠️ A host that could not be searched is NOT a host with no activity. Those are printed
as distinct outcomes and a caller must not collapse them: an ssh failure is a HOST
problem, not evidence the seat is dead.
"""
import json, subprocess, sys

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-15"
HOSTS = ["office", "server4", "server5", "server3"]

PROBE = r'''
import json, glob
hours=set()
for d in ("-mnt-nas-data-code-assistant","-code-assistant"):
    for p in glob.glob(f"/home/ben/.claude/projects/{d}/*.jsonl"):
        try:
            for line in open(p, errors="replace"):
                try: t=json.loads(line).get("timestamp")
                except Exception: continue
                if t and t >= "%s": hours.add(t[:13])
        except OSError: continue
print(json.dumps(sorted(hours)))
''' % SINCE

union, per_host, unsearched = set(), {}, []
for h in HOSTS:
    try:
        # ⛔ Pipe the probe on STDIN. Passing it as `ssh ... python3 -c <src>` FAILS
        # (rc=2): ssh joins argv into one remote shell string with no quoting, so the
        # newlines and quotes in the source are re-parsed by the remote shell.
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-p", "4999",
             f"ben@{h}", "python3", "-"],
            input=PROBE, capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            per_host[h] = "NOT SEARCHED (ssh rc=%d)" % out.returncode
            unsearched.append(h); continue
        hrs = json.loads(out.stdout)
    except Exception as e:
        per_host[h] = "NOT SEARCHED (%s)" % type(e).__name__
        unsearched.append(h); continue
    per_host[h] = "%d hrs%s" % (len(hrs), (" last=%s" % max(hrs)) if hrs else " (none)")
    union |= set(hrs)

hrs = sorted(union)
gaps = []
if hrs:
    import datetime
    fmt = "%Y-%m-%dT%H"
    for a, b in zip(hrs, hrs[1:]):
        d = (datetime.datetime.strptime(b, fmt) - datetime.datetime.strptime(a, fmt)).total_seconds() / 3600
        if d > 12:
            gaps.append([a, b, int(d)])

print(json.dumps({"since": SINCE, "per_host": per_host, "unsearched": unsearched,
                  "union_hours": len(hrs), "last": hrs[-1] if hrs else None,
                  "gaps_over_12h": gaps}, indent=2))
if unsearched:
    print("⚠️ %s NOT SEARCHED — its silence is not evidence of absence; do not read this "
          "as a liveness result for that host." % ", ".join(unsearched), file=sys.stderr)
