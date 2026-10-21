#!/usr/bin/env python3
"""Freeze the memos a run is about to touch, so `verify-run.py` has a witness.

⛔ WHY THIS IS A FILE AND NOT A SESSION SNIPPET. The 2026-10-20 snapshot was
built ad hoc in a session and never committed, so the only artefact proving
what that run started from is a JSON blob nobody can regenerate or audit. That
is the identical gap `select-dup-titles.py` was written to close, and the whole
point of `verify-run.py`: the worker's own report is not evidence. A snapshot
that cannot be reproduced is not evidence either.

⛔ TAKE IT BEFORE THE RUN, NOT AFTER. `verify-run.py` diffs the live corpus
against this file; a snapshot taken afterwards shows every target untouched and
reads exactly like a run that changed nothing.

⚠️ `found` CAN BE LESS THAN `n_targets`, AND THAT IS INFORMATION, NOT AN ERROR.
A candidate id that no longer resolves was retired or deleted between ranking
and snapshotting. Recorded rather than silently dropped, because a target that
vanished before the run and one the run deleted are different findings and the
diff cannot tell them apart without this number.
"""
from __future__ import annotations

import argparse, json, time, urllib.request
from pathlib import Path

MEMO = "http://server4:8000"
HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="candidates-read-weighted.json")
    ap.add_argument("--n", type=int, required=True,
                    help="how many from the head of the candidate file this run will touch")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cands = json.loads((HERE / a.candidates).read_text())
    targets = cands[a.offset:a.offset + a.n]
    want = {c["id"] for c in targets}

    docs = json.load(urllib.request.urlopen(f"{MEMO}/documents?limit=20000", timeout=180))
    live_n = sum(1 for d in docs if d.get("valid_until") is None)

    frozen = {}
    for d in docs:
        if d["id"] not in want:
            continue
        frozen[d["id"]] = {
            "title": d.get("title"),
            "chars": len(d.get("content") or ""),
            "updated_at": d.get("updated_at"),
            "tags": sorted(d.get("tags") or []),
        }

    snap = {
        "taken_at": time.time(),
        "corpus": len(docs),
        "live": live_n,
        # The backlog is what the run has to move. Reported here so the diff can
        # quote the figure it started from rather than re-deriving it later
        # against a corpus the run itself has changed.
        "backlog": len(cands),
        "n_targets": len(targets),
        "found": len(frozen),
        "docs": frozen,
    }
    out = Path(a.out) if a.out else HERE / "runs" / f"pre-snapshot-{time.strftime('%Y%m%dT%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1))
    print(f"snapshot -> {out}")
    print(f"corpus {len(docs)} · live {live_n} · backlog {len(cands)} · "
          f"targets {len(targets)} · frozen {len(frozen)}")
    if len(frozen) != len(targets):
        missing = sorted(want - set(frozen))
        print(f"⚠️ {len(targets) - len(frozen)} target(s) did not resolve and are NOT in the "
              f"snapshot — retired or deleted since ranking: {missing[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
