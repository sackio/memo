#!/usr/bin/env python3
"""Diff a pre-run snapshot against the live corpus and report WHAT THE BACKLOG DID.

⛔ THIS EXISTS BECAUSE THE WORKER'S OWN REPORT IS NOT EVIDENCE. A worker that
believes it edited a memo and a worker that actually did are byte-identical in
their summary text. `69f9b27f` records the measured case; run.py's own docstring
records a run that printed "0/4 completed" over twelve documents it had just
rewritten. ⇒ The corpus is the witness. Diff it.

⛔ AND REPORT THE BACKLOG, NEVER "N MEMOS AUDITED". Measured 2026-10-19: the
fleet WRITES ~98 docs/day and the audit processes ~96/day at $28, so a run can
audit 120 memos while the backlog grows. "120 audited" reads as progress during
a period of exactly zero. A burn-down is only meaningful against INFLOW.
"""
from __future__ import annotations

import argparse, json, urllib.request
from pathlib import Path

MEMO = "http://server4:8000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--rundir", required=True, help="runs/<ts> for this run")
    a = ap.parse_args()

    snap = json.loads(Path(a.snapshot).read_text())
    before = snap["docs"]

    docs = json.load(urllib.request.urlopen(f"{MEMO}/documents?limit=20000", timeout=180))
    now = {d["id"]: d for d in docs}
    live_n = sum(1 for d in docs if d.get("valid_until") is None)

    changed, retired, vanished, untouched = [], [], [], []
    for did, b in before.items():
        d = now.get(did)
        if d is None:
            vanished.append(did)                     # ⚠️ gone entirely, not merely retired
            continue
        if d.get("valid_until") is not None:
            retired.append(did)
            continue
        delta = len(d.get("content") or "") - b["chars"]
        if delta or (d.get("updated_at") != b["updated_at"]) \
                or sorted(d.get("tags") or []) != b["tags"]:
            changed.append((did, delta, b["title"]))
        else:
            untouched.append(did)

    # Cost and completion come from the batch records, which land on disk inside
    # run_batch BEFORE anything that can raise -- not from the run's stdout.
    recs = [json.loads(f.read_text())
            for f in sorted(Path(a.rundir).glob("batch-*.json")) if ".raw." not in f.name]
    spend = sum(float(r.get("cost_usd") or 0.0) for r in recs)
    errs = [r["batch"] for r in recs if r.get("is_error")]
    skipped = [r["batch"] for r in recs if r.get("skipped")]

    print(f"batches on disk {len(recs)} · errored {len(errs)} · skipped {len(skipped)} "
          f"· ${spend:.4f}")
    if errs:    print(f"  ⛔ errored batches: {errs}")
    if skipped: print(f"  ⚠️ skipped (guard): {skipped}")

    print(f"\ntargets {len(before)} → changed {len(changed)} · retired {len(retired)} "
          f"· untouched {len(untouched)} · VANISHED {len(vanished)}")
    if vanished:
        print(f"  ⛔ ids absent from /documents entirely: {vanished[:10]}")
    for did, delta, title in sorted(changed, key=lambda x: -abs(x[1]))[:15]:
        print(f"  {did[:8]} {delta:+6d} chars  {(title or '')[:70]}")

    # ⭐ THE HEADLINE. Backlog, against inflow -- not a count of work done.
    print(f"\ncorpus {snap['corpus']} → {len(docs)}  ({len(docs) - snap['corpus']:+d})")
    print(f"live   {snap['live']} → {live_n}  ({live_n - snap['live']:+d})")
    print(f"⇒ backlog was {snap['backlog']} at snapshot. Re-run select-read-weighted.py "
          f"for the new figure: that is the only number that says whether this run "
          f"moved anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
