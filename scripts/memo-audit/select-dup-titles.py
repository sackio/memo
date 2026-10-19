#!/usr/bin/env python3
"""Emit the duplicate-title sets for the consolidation pass -- reproducibly.

⛔ WHY THIS FILE EXISTS. The first `dup-title-sets.json` was built ad hoc in a
session and never committed as code, so the population it described could not be
re-derived or audited. That is the same gap `select-stratified.py` was written to
close: a candidate list nobody can regenerate is a measurement nobody can check.

⛔ RETIRED DOCUMENTS MUST BE EXCLUDED, AND THIS IS THE WHOLE CORRECTNESS ISSUE.
A merge retires the loser with `/supersede-link`, which sets `valid_until` and
leaves the TITLE untouched -- deliberately, so the old id stays readable. So
every set this pass consolidates STILL shares a title afterwards. A detector
that groups on title alone re-flags all 82 of them forever, and each re-flag
costs a worker invocation to conclude "already merged". Group only over docs
with `valid_until is None`, exactly as `select-all.py` does.

⚠️ EXACT TITLE MATCH ONLY. Measured 2026-10-19: all 85 sets in the corpus were
exact pairs and ZERO were byte-identical in content, so there is no mechanical
merge available and no near-match heuristic is needed yet. If that stops being
true, a near-match detector is a different script with a different error mode --
it can group documents that merely look alike, which this one cannot.
"""
from __future__ import annotations

import argparse, json, urllib.request
from collections import defaultdict
from pathlib import Path

MEMO = "http://server4:8000"
HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dup-title-sets.json")
    ap.add_argument("--exclude-snapshots", nargs="*",
                    default=["stage5-before", "stage6-before"],
                    help="snapshot dirs whose ids are already in flight or done")
    a = ap.parse_args()

    docs = json.load(urllib.request.urlopen(f"{MEMO}/documents?limit=20000", timeout=180))
    live = [d for d in docs if d.get("valid_until") is None]

    done: set[str] = set()
    for name in a.exclude_snapshots:
        for f in (HERE / name).glob("*.json"):
            try:
                done.add(json.loads(f.read_text())["id"])
            except Exception:                              # noqa: BLE001
                pass

    by_title: dict[str, list] = defaultdict(list)
    for d in live:
        t = (d.get("title") or "").strip()
        if t:
            by_title[t].append(d)

    sets = []
    for t, group in by_title.items():
        if len(group) < 2:
            continue
        if any(d["id"] in done for d in group):
            continue                       # already consolidated or in flight
        group.sort(key=lambda d: -(len(d.get("content") or "")))
        sets.append({"title": t,
                     "ids": [d["id"] for d in group],
                     "chars": [len(d.get("content") or "") for d in group]})

    sets.sort(key=lambda s: (-len(s["ids"]), -sum(s["chars"])))
    Path(a.out).write_text(json.dumps(sets, indent=1))
    docs_n = sum(len(s["ids"]) for s in sets)
    print(f"corpus {len(docs)} · live {len(live)} · excluded {len(done)} already-handled ids")
    print(f"{len(sets)} duplicate-title sets · {docs_n} documents -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
