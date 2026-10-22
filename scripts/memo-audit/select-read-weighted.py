#!/usr/bin/env python3
"""Re-rank the audit candidates by how often a memo is actually READ.

⛔ WHY THIS EXISTS. The uniform sweep does not finish. Measured 2026-10-19 from
this directory's own batch records: $0.291 per unit over 225 units, against
9,851 remaining candidates = ~$2,870 and ~100 days at the agreed $28/day pace.
That is ~3.6 months of the ENTIRE $800/mo fleet grant pool spent on one audit,
and the find rate has not plateaued, so nothing stops it on its own.

⭐ THE FACT THAT MAKES IT AVOIDABLE: the median live memo has been fetched TWICE,
EVER (mean 5.1, measured over 11,105 live docs with an access row). The top
2,000 carry 60% of all reads. Auditing in read order buys 60% of the real
consultation traffic for ~$580 instead of ~$2,870. Correctness in a memo nobody
opens is worth less than correctness in one the fleet reads weekly.

⚠️⚠️ `get_count` IS TRAFFIC, NOT HUMAN INTEREST, AND THE AUDIT INFLATES IT.
Every memo this pipeline audits gets a GET from the worker; memo-minder and
memo-watch also read. So the counter is contaminated by automation, and today's
own run pushed 698 live docs' `last_fetched_at` into today. This is the same
shape as the consolidation's citation screen, which counted a back-pointer the
merge itself had just written: A RANKING SIGNAL MUST NOT BE ONE THE RANKED
PROCESS WRITES. It is used here anyway, deliberately, because it is still a far
better prior than uniform order -- but it must never be reported as "what the
fleet cares about", and already-audited ids are excluded below so the pass
cannot keep re-ranking its own footprints to the top.

⛔ SERVER4-ONLY. The counts come from the sqlite file inside the memo-v2
container.

✅ BOTH ENDPOINT BUGS THIS NOTE USED TO DESCRIBE ARE FIXED AND DEPLOYED
2026-10-20 (memo-v2 `5bd5e2f`), so the sqlite read is now a CHOICE, not a
workaround: `GET /admin/access-stats` honours `limit` on `hot_last_30d` and
returns the applied bound as `hot_limit`, and `coverage_pct` counts live docs on
both sides (verified 100.0 post-deploy, was 141.7), with reads against deleted
memos reported separately as `reads_on_deleted_docs`. Reading sqlite directly is
still preferred here -- it is one query for the whole corpus with no HTTP page
size to reason about -- but a future maintainer should know the endpoint is no
longer the reason.
"""
from __future__ import annotations

import argparse, base64, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTAINER = "memo-v2"

# Read the counter out of the container. `docker exec` writes 0 bytes to a
# REDIRECT, so this uses capture_output (a pipe) rather than a shell redirect.
PROBE = """
import sqlite3, json
c = sqlite3.connect('file:/data/memo.db?mode=ro', uri=True)
out = {r[0]: r[1] or 0 for r in c.execute(
    "SELECT a.doc_id, a.get_count FROM doc_access a "
    "JOIN documents d ON d.id = a.doc_id WHERE d.valid_until IS NULL")}
print(json.dumps(out))
"""


def read_counts() -> dict[str, int]:
    b64 = base64.b64encode(PROBE.encode()).decode()
    p = subprocess.run(
        ["docker", "exec", CONTAINER, "python3", "-c",
         f"import base64;exec(base64.b64decode('{b64}'))"],
        capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit(f"⛔ could not read doc_access: {p.stderr.strip()[:400]}")
    return json.loads(p.stdout)


def audited_ids() -> set[str]:
    """8-char prefixes this pipeline has already spent money on.

    ⚠ A SKIPPED BATCH WRITES A FULL RECORD -- same filename, `n` set, `skipped`
    set, `cost_usd: 0`. Measured 2026-10-22 on run 20261022T120910 (125 records,
    9 of them RAN): those records carry `ids: []`, so nothing has in fact been
    retired unspent. This filter is the invariant made explicit, NOT a repair:
    the day a refusal records which memos it declined, unioning blindly would
    retire them permanently on zero work. A record of an ATTEMPT is not a record
    of WORK -- filter on the outcome, not on the file's existence.
    """
    done: set[str] = set()
    for f in (HERE / "runs").glob("*/batch-*.json"):
        if ".raw." in f.name:
            continue
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError):
            continue                      # a torn record is not an empty one
        if d.get("skipped") or d.get("is_error"):
            continue                      # attempted, refused or failed != audited
        done.update(d.get("ids") or [])
    return done


def refuted_ids() -> set[str]:
    """Memos the free nightly recheck disproved since the last run.

    ⚠ ABSENT IS NOT EMPTY. No queue file means the recheck has never run, not
    that every probe holds -- so this returns an empty set either way and the
    CALLER must not read a zero here as evidence of corpus health.
    """
    q = HERE / "refute-queue.json"
    try:
        return set(json.loads(q.read_text()).get("ids") or [])
    except (OSError, ValueError):
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="candidates-all.json")
    ap.add_argument("--out", default="candidates-read-weighted.json")
    ap.add_argument("--top", type=int, default=0, help="keep only the top N (0 = all)")
    a = ap.parse_args()

    cands = json.loads(Path(a.candidates).read_text())
    counts = read_counts()
    done = audited_ids()
    refuted = refuted_ids()

    # ⭐ A REFUTED PROBE RE-ENTERS THE BACKLOG EVEN IF IT WAS AUDITED BEFORE.
    # That is the point of the probe loop: the free nightly recheck found the
    # world moved under a memo we already paid for, and the audited-ids ledger
    # would otherwise keep it retired forever. Drift is exactly what the grant
    # should be buying.
    kept = [c for c in cands if c["id"][:8] not in done or c["id"] in refuted]
    for c in kept:
        c["get_count"] = counts.get(c["id"], 0)

    # Reads first, then the existing checkability score as the tie-break: among
    # memos read equally often, audit the one whose claims can actually be
    # checked. ⚠️ Not a blend -- a weighted sum would let a high checkability
    # score promote a memo nobody has ever opened, which is the whole thing
    # this ordering exists to stop.
    # Refuted first -- a memo a probe has disproved is known-wrong, where
    # everything else in this list is only unverified.
    kept.sort(key=lambda c: (c["id"] not in refuted, -c["get_count"],
                             -(c.get("score") or 0)))
    if a.top:
        kept = kept[:a.top]

    Path(a.out).write_text(json.dumps(kept, indent=1))
    tot = sum(c["get_count"] for c in kept)
    never = sum(1 for c in kept if c["get_count"] == 0)
    print(f"candidates {len(cands)} · already audited {len(done)} · "
          f"refuted by probe {len(refuted & {c['id'] for c in kept})} (first) · "
          f"kept {len(kept)}")
    print(f"{len(kept)} -> {a.out} · {tot} reads covered · {never} never read")
    if kept:
        print(f"top get_count {kept[0]['get_count']}, "
              f"median {kept[len(kept)//2]['get_count']}, floor {kept[-1]['get_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
