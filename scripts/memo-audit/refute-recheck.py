#!/usr/bin/env python3
"""Recheck every memo that carries a REFUTE probe. No LLM, no spend.

⭐ THE POINT. A paid audit costs ~$0.41/memo because an LLM re-establishes the
claim from nothing. The probe it leaves behind answers the same question for the
cost of one request, so freshness becomes a cron job and the grant is spent on
memos that have actually MOVED instead of on re-reading memos that have not.

⛔ A `refuted` VERDICT IS A FLAG, NOT A JUDGEMENT. This script never edits,
retires or deletes a memo. It queues it for the next paid audit, which is the
thing that can read the room -- the probe knows one observation, not whether the
memo's point survives it.

⭐⭐ `inconclusive` IS REPORTED SEPARATELY AND IS NEVER FOLDED INTO EITHER SIDE.
A probe that could not run -- host down, /mnt/nas stalled, binary gone -- tells
you nothing about the memo. Counting it as `holds` manufactures freshness;
counting it as `refuted` burns the grant on memos nobody has shown are wrong. If
the inconclusive rate jumps, the PROBES are broken, not the corpus.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refute                                           # noqa: E402

HERE = Path(__file__).resolve().parent
QUEUE = HERE / "refute-queue.json"


def corpus(api: str, limit: int = 20000) -> list[dict]:
    """⛔ SCAN /documents. `/search` ranks by relevance and silence is not absence."""
    with urllib.request.urlopen(f"{api}/documents?limit={limit}", timeout=300) as r:
        docs = json.loads(r.read().decode())
    if not isinstance(docs, list):
        docs = docs.get("documents") or docs.get("items") or []
    return docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=refute.API)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--report", default=str(HERE / "refute-report.json"))
    ap.add_argument("--queue", action="store_true",
                    help="write the refuted ids where the next audit run reads them")
    a = ap.parse_args()

    docs = corpus(a.api)
    probed = [(d, refute.probe_of(d)) for d in docs]
    probed = [(d, p) for d, p in probed if p]
    print(f"corpus {len(docs)} · carrying a probe {len(probed)} "
          f"({len(probed) / max(1, len(docs)):.1%})")
    if not probed:
        print("nothing to recheck — no memo carries a probe yet")
        return 0

    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        results = list(pool.map(lambda dp: (dp[0], refute.run(dp[1])), probed))

    tally = Counter(r.verdict for _, r in results)
    rows = [{"id": d["id"], "title": (d.get("title") or "")[:120],
             "verdict": r.verdict, "detail": r.detail[:300]}
            for d, r in results]
    report = {"at": datetime.now(timezone.utc).isoformat(),
              "corpus": len(docs), "probed": len(probed),
              "tally": dict(tally), "rows": rows}
    Path(a.report).write_text(json.dumps(report, indent=1))

    print(f"holds {tally['holds']} · refuted {tally['refuted']} · "
          f"inconclusive {tally['inconclusive']}")
    for row in rows:
        if row["verdict"] != "holds":
            print(f"  {row['verdict']:13s} {row['id'][:8]} {row['title'][:70]}")
            print(f"                {row['detail'][:150]}")

    if a.queue:
        ids = [r["id"] for r in rows if r["verdict"] == "refuted"]
        QUEUE.write_text(json.dumps({"at": report["at"], "ids": ids}, indent=1))
        print(f"queued {len(ids)} refuted memo(s) for the next paid audit → {QUEUE}")

    # ⚠️ exit 0 even with refutations: a refuted memo is the system WORKING.
    # Only a probe set that cannot run is an operational failure.
    if tally["inconclusive"] > max(3, 0.25 * len(probed)):
        print("⛔ more than a quarter of probes could not RUN — probes are broken, "
              "not the corpus")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
