#!/usr/bin/env python3
"""Rank the WHOLE live corpus by how worth auditing each memo is.

Ben, Slack #memo 2026-10-19 11:31 EDT: memo should be "really pristine tip top
and like making sure by auditing things what the truth is". So the unit of
selection is not "whose tag is on it" but "does this memo assert something we can
go and check, and how long since anyone checked it".

⚠️ THIS IS A CANDIDATE GENERATOR, NOT A FINDING. Measured 2026-10-19 on this same
corpus: a regex for "daily digest" matched 61 live memos and nearly all were
unrelated; "7200" matched 26, mostly ATC schedules legitimately using 7200s;
"office:8000" matched 12, almost all of them memos correctly DOCUMENTING that
endpoint's retirement. The highest-scoring document for a stale-claim pattern was
the memo written to CORRECT that claim, because a correction quotes what it
corrects. ⇒ A high score here means "worth a look", never "this is wrong".
"""
from __future__ import annotations

import argparse, json, re, sys, urllib.request
from datetime import datetime, timezone

MEMO = "http://127.0.0.1:8000"

# Things that can be checked against the world, weighted by how checkable.
SIGNALS = [
    (3.0, re.compile(r":\d{4,5}\b")),                       # a port
    (3.0, re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),      # an IP
    (2.5, re.compile(r"/(?:mnt|home|var|etc|tmp)/[\w./-]+")),  # a path
    (2.5, re.compile(r"\b(crontab|cron entry|cron line|schedule[sd]?)\b", re.I)),
    (2.0, re.compile(r"\b(sole|only one|all \d+|every|canonical)\b", re.I)),
    (2.0, re.compile(r"\b(image|branch|commit|deployed|container)\b", re.I)),
    (1.5, re.compile(r"\b(endpoint|replicas?|pod|deployment|namespace)\b", re.I)),
    (1.5, re.compile(r"\b[0-9a-f]{7,40}\b")),               # a sha or short id
    (1.0, re.compile(r"\b(runs|serves|listens|lives at|located)\b", re.I)),
]
# Claims that are rulings, not facts about the world. AGE IS NEVER SUPERSESSION,
# so these are ranked DOWN rather than excluded -- a ruling only dies by a newer
# ruling, and that is a judgement an auditor should be slow to make.
RULING = re.compile(r"\b(ben (said|ruled|decided|authoriz)|operator (said|ruled)|"
                    r"closed by ben|do not re-?raise)\b", re.I)


def main() -> int:
    # ⛔ PARSE BEFORE THE CORPUS FETCH. This script used to ignore its arguments
    # entirely, --help included: the daily cycle passed `--out
    # candidates-all.json` and it worked only because that equalled the
    # hardcoded path. A script that accepts a flag and discards it is worse than
    # one that rejects it -- the caller reads the flag back as configuration and
    # nothing ever says otherwise. Parsing first also means `--help` answers
    # instantly instead of pulling 11,800 documents to say nothing.
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    ap.add_argument("--out", default="candidates-all.json")
    out = ap.parse_args().out

    import corpus as _corpus
    import refute                  # refuted_ids(): the one definition
    docs = _corpus.load(api=MEMO)      # ~78 MB per full fetch; share one copy
    now = datetime.now(timezone.utc).timestamp()
    live = [d for d in docs if d.get("valid_until") is None]

    # ⛔⛔ A MEMO A PROBE HAS DISPROVED SKIPS THESE THREE FILTERS. They are
    #    HEURISTICS estimating "is this worth a look": too short to audit, too
    #    fresh to have drifted, nothing checkable in it. A failed probe is
    #    DIRECT EVIDENCE answering that same question, and it beats every
    #    estimate of it.
    # ⭐⭐ MEASURED 2026-10-26, AND THIS IS WHY IT MATTERS: of 39 refuted memos,
    #    7 were queued for the paid audit and ALL 7 were dropped right here by
    #    `age_d < 2` -- ages 0.05 to 1.07 days. The queue reached the audit with
    #    ZERO entries. The exclusion is SELF-REINFORCING: a refutation gets the
    #    memo edited (by its holder, answering the DM route.py sends, or by the
    #    audit itself), the edit refreshes `updated_at`, and the refreshed memo
    #    is then "too fresh to have drifted" -- so the very response to a
    #    refutation is what hides it from the lane meant to resolve it.
    # ⚠️ select-read-weighted.py ALREADY carries the intended escape, and says
    #    so: "A REFUTED PROBE RE-ENTERS THE BACKLOG EVEN IF IT WAS AUDITED
    #    BEFORE... drift is exactly what the grant should be buying." That
    #    escape runs one stage too late -- it re-admits ids past the
    #    audited-ids ledger, in a pool THIS script has already dropped them
    #    from. An escape downstream of the filter it is meant to undo is not an
    #    escape.
    refuted = refute.refuted_ids()
    rows = []
    for d in live:
        body = (d.get("title") or "") + "\n" + (d.get("content") or "")
        spared = d["id"] in refuted
        if len(body) < 120 and not spared:
            continue                       # nothing to audit
        age_d = (now - (d.get("updated_at") or now)) / 86400.0
        if age_d < 2 and not spared:
            continue                       # written today; nothing has drifted yet
        check = sum(w * min(3, len(rx.findall(body))) for w, rx in SIGNALS)
        if check <= 0 and not spared:
            continue
        score = check * (1.0 + min(age_d, 180) / 60.0)
        if RULING.search(body):
            score *= 0.45
        rows.append({"id": d["id"], "title": d.get("title") or "",
                     "chars": len(d.get("content") or ""),
                     "updated_at": d.get("updated_at"),
                     "tags": d.get("tags") or [],
                     "age_days": round(age_d, 1),
                     "checkability": round(check, 1), "score": round(score, 1)})

    rows.sort(key=lambda r: -r["score"])

    json.dump(rows, open(out, "w"), indent=1)
    print(f"corpus {len(docs)} · live {len(live)} · ranked candidates {len(rows)}")
    print(f"total chars {sum(r['chars'] for r in rows):,} "
          f"(~{sum(r['chars'] for r in rows)//4:,} tokens)")
    print("\ntop 12 by score:")
    for r in rows[:12]:
        print(f"  {r['score']:>7.1f}  chk={r['checkability']:>5.1f} "
              f"age={r['age_days']:>6.1f}d  {r['id'][:8]}  {r['title'][:58]}")
    print("\n⚠️ score = worth a look, NOT evidence of error. See the module docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
