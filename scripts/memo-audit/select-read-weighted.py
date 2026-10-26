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

import refute                  # the one definition of refuted_ids()

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


def pin_cited_ids() -> set[str]:
    """8-char prefixes of every memo cited by a live ATC standing pin.

    ⭐⭐ BEN, Slack #memo 2026-10-23 10:15: "the memos that are referenced in pin
       should be a priority for sweeping and confirming when you look at them
       because if those things change we really need them to reference the new
       memo and keep the pins healthy."

    ⭐ AND IT CORRECTS A BLIND SPOT IN THE READ-WEIGHTING ABOVE, which is the
       reason it outranks `get_count` rather than merely joining it. A pin is
       PUSH: its text is spliced into a seat's session-start payload ahead of the
       first turn, so the seat consumes the claim WITHOUT ever fetching the memo.
       `get_count` therefore systematically UNDERCOUNTS exactly the memos the
       fleet acts on most directly -- a memo can be read by 40 seats a day
       through a pin and show a get_count of 2. Measured 2026-10-23: 274 distinct
       memo ids are cited across 336 live pins, and 272 resolve.

    ⛔ AN EMPTY SET HERE IS NOT "NO PINS CITE ANYTHING" -- it is almost certainly
       a broker that did not answer. Raises rather than returning empty, because
       a silent zero would drop the whole priority tier and leave the ordering
       looking untouched.
    """
    import urllib.request
    sys.path.insert(0, str(HERE.parent / "instruction-audit"))
    from scan import RE_FULL, RE_SHORT, is_memo_ref          # the precision gate

    BROKER = "http://192.168.1.168:3030"

    def get(path):
        with urllib.request.urlopen(BROKER + path, timeout=30) as r:
            return json.loads(r.read())

    owners = [o["owner"] for o in get("/reminders-owners?include_retired=1")["owners"]
              if o.get("owner")]
    if not owners:
        raise SystemExit("⛔ broker returned no pin owners -- NOT a clean result")
    cited: set[str] = set()
    unreachable = []
    for name in owners:
        try:
            d = get(f"/reminders/{name}?include_retired=1")
        except Exception as e:                                # noqa: BLE001
            unreachable.append(f"{name}: {e}")
            continue
        for r in (d.get("reminders") or []):
            if r.get("inherited") or r.get("retired"):
                continue
            body = r.get("body") or ""
            for m in RE_FULL.finditer(body):
                if is_memo_ref(body, m.start(), m.end(), m.group(0)):
                    cited.add(m.group(0)[:8])
            for m in RE_SHORT.finditer(body):
                i = m.group(1) or m.group(2)
                if i and is_memo_ref(body, m.start(), m.end(), i):
                    cited.add(i[:8])
    if unreachable:
        # ⚠️ Named, not swallowed: a seat whose pins could not be read has its
        #    cited memos missing from the priority tier, and that is a smaller
        #    queue, not a cleaner one.
        print(f"⚠️ {len(unreachable)} owner(s) unreadable, their citations are NOT in the "
              f"priority tier: {unreachable[:4]}", file=sys.stderr)
    if not cited:
        raise SystemExit("⛔ no memo citations found in any pin -- implausible, "
                         "treat as a broken read rather than a clean corpus")
    return cited


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="candidates-all.json")
    ap.add_argument("--out", default="candidates-read-weighted.json")
    ap.add_argument("--top", type=int, default=0, help="keep only the top N (0 = all)")
    a = ap.parse_args()

    cands = json.loads(Path(a.candidates).read_text())
    counts = read_counts()
    done = audited_ids()
    refuted = refute.refuted_ids()

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
    # Tier order: REFUTED (known wrong) > PIN-CITED (a seat is being handed this
    # claim unbidden) > read count > checkability. ⚠️ Still tiers, not a blend --
    # a weighted sum would let a high checkability score promote a memo nobody
    # has ever opened and no pin depends on.
    pinned = pin_cited_ids()
    n_pinned = sum(1 for c in kept if c["id"][:8] in pinned)
    print(f"pin-cited priority tier: {len(pinned)} ids cited by live pins, "
          f"{n_pinned} of them still unaudited and now at the front", file=sys.stderr)
    kept.sort(key=lambda c: (c["id"] not in refuted,
                             c["id"][:8] not in pinned,
                             -c["get_count"],
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
