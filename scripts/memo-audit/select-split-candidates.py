#!/usr/bin/env python3
"""Rank the memos worth SPLITTING, and exclude the two populations that are not.

Ben, Slack #memo 2026-10-20 19:35 EDT: the memo maintainer should be "splitting
up big memos into smaller more precise memos". This emits the candidate list for
that, in the same shape as the other selectors, so it feeds the normal audit run
rather than a separate sweep.

⛔⛔ SIZE IS THE TRIGGER TO LOOK, NEVER THE REASON TO CUT. This file ranks; the
worker decides. A big memo making ONE argument is correct as it stands, and the
worker is told to return CURRENT for it. Nothing here should be read as "these
93 memos need splitting" -- it is "these are the ones worth a look, in this
order".

⛔ THREE POPULATIONS, AND ONLY ONE SHOULD BE SPLIT. Measured 2026-10-20 over all
93 live memos at >=14,000 qwen3 tokens:

    generated digest   n=9    21 lifetime reads total  (2.3 each, ever)
    append chain       n=33   2,048                    (62 each)
    everything else    n=51   3,184                    (62 each)

⭐ A LEDGER IS NOT AN ARGUMENT. The 33 chain memos -- `PART 3`, `ARCHIVE III`,
`continues 6623f9d5`, `FULL since 2026-10-03` -- are dated running logs their
owning seats ALREADY cut by hand at each 413. Their entries are events, not
claims. Re-cutting them by topic destroys the only ordering they have, and
"more precise" does not mean anything for a log. They are excluded.

⭐ The 9 generated digests are write-only: 21 reads between them, ever, against
62 EACH for the other two populations. Splitting a digest nothing reads is
paying to reformat exhaust. They are excluded and belong to git-pulse, as a
generation-time fix.

⚠️ THE EXCLUSIONS ARE TITLE REGEXES AND SO THEY WILL MISCLASSIFY. A chain memo
whose title does not say so reads as a split candidate; the worker's own
instructions carry the same taxonomy as a second gate, which is the point --
this is a candidate generator, not a finding.

⛔⛔ NEGATIVE RESULT, MEASURED 2026-10-20 -- DO NOT REBUILD THIS. A content-based
ledger detector was tried, on the obvious signal: the density of dated entries
over section headings. It does NOT separate. Across 26 of these candidates,
hand-labelled ledgers scored 0.42 to 0.83 and hand-labelled arguments scored
0.31 to 0.67 -- overlapping across most of both ranges, with a known ledger
(373eaeeb, 0.67) and a known argument (98ec5993, 0.67) landing on the same
value. No threshold exists.
⭐ WHY, AND IT IS THE INTERESTING PART: this corpus's house style is to date
every claim -- "measured 2026-10-19", "Ben ruled 2026-10-20". The provenance
convention that makes the memos trustworthy is the same convention that erases
the structural difference between a log of events and an argument about them.
⇒ Ledger-vs-argument is a SEMANTIC judgement here, and the worker makes it. A
worker reporting "this is a running log, not splitting it" is a cheap and
correct answer, not a wasted invocation.

⭐ RANK ON `token_count_qwen3`, NOT `token_count`. The stored qwen3 count is
exact: verified against the live tokenizer at :31536 on 2026-10-20, 10 of 10
documents, spanning 3 to 16,358 tokens. The cl100k `token_count` cannot rank
cap-proximity at all -- it read 14,283 for a document the embedder measured at
16,119, which is the difference between "room to spare" and 265 tokens short of
a refusal.
"""
from __future__ import annotations

import argparse, base64, json, re, subprocess, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTAINER = "memo-v2"
MEMO = "http://server4:8000"
QWEN3_CAP = 16384

GENERATED = re.compile(r"^\s*(Git pulse index|Backfill checkpoint)\b", re.I)
# ⛔ SELF-DECLARATIONS ONLY. Every alternative below is a phrase in which the
# memo states its own role -- "PART 3", "continues <id>", "DO NOT APPEND". That
# keeps precision high, because a memo saying it is part of a chain is evidence
# about itself. It also keeps recall LOW, deliberately: see the negative result
# recorded in the module docstring. A ledger that never declares itself (a
# weigh-in log, a fee history) reaches the worker, and the worker is the gate.
CHAIN = re.compile(
    r"(\bPART \d|\bARCHIVE [IVX]+\b|\bARCHIVE \d|\bcontinues\b|\bcontinuation\b"
    r"|live continuation|\bFULL since|\bsuccessor to\b|\bappend target\b"
    r"|DO NOT APPEND|⛔ FULL|\bFULL \(|\bHISTORICAL\b|\bfrozen at\b)", re.I)

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


def classify(title: str) -> str:
    if GENERATED.search(title or ""):
        return "generated"
    if CHAIN.search(title or ""):
        return "chain"
    return "candidate"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-tokens", type=int, default=12000,
                    help="qwen3 tokens; the trigger to LOOK, not to cut")
    ap.add_argument("--out", default="candidates-split.json")
    ap.add_argument("--top", type=int, default=0, help="keep only the top N (0 = all)")
    ap.add_argument("--include-excluded", action="store_true",
                    help="emit the generated/chain memos too, tagged, for review")
    a = ap.parse_args()

    with urllib.request.urlopen(f"{MEMO}/documents?limit=20000", timeout=120) as r:
        docs = json.load(r)
    live = [d for d in docs if d.get("valid_until") is None]
    counts = read_counts()

    # ⛔ A doc with no token_count_qwen3 is UNMEASURED, not small. Report it
    # separately rather than letting a null sort to the bottom as if it were 0.
    unmeasured = [d for d in live if not d.get("token_count_qwen3")]
    sized = [d for d in live if d.get("token_count_qwen3")]
    big = [d for d in sized if d["token_count_qwen3"] >= a.min_tokens]

    out, counted = [], {"generated": 0, "chain": 0, "candidate": 0}
    reads = {"generated": 0, "chain": 0, "candidate": 0}
    for d in big:
        kind = classify(d.get("title") or "")
        g = counts.get(d["id"], 0)
        counted[kind] += 1
        reads[kind] += g
        if kind != "candidate" and not a.include_excluded:
            continue
        out.append({
            "id": d["id"],
            "title": d.get("title"),
            "tags": d.get("tags") or [],
            "tokens_qwen3": d["token_count_qwen3"],
            "headroom": QWEN3_CAP - d["token_count_qwen3"],
            "get_count": g,
            "population": kind,
        })

    # Reads first, size as the tie-break. ⚠️ Deliberately NOT a product: size x
    # reads lets a 16k-token memo nobody opens outrank a well-read one, and the
    # whole reason to order by reads is that correctness in a memo nobody opens
    # is worth less than correctness in one the fleet reads weekly.
    out.sort(key=lambda c: (-c["get_count"], -c["tokens_qwen3"]))
    if a.top:
        out = out[:a.top]

    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"live {len(live)} · unmeasured {len(unmeasured)} · "
          f">={a.min_tokens} qwen3 tok: {len(big)}")
    for k in ("generated", "chain", "candidate"):
        per = reads[k] / counted[k] if counted[k] else 0
        print(f"  {k:<10} n={counted[k]:<4} reads={reads[k]:<6} ({per:.1f} each)")
    print(f"{len(out)} -> {a.out}")
    if out:
        print(f"  top: get_count {out[0]['get_count']}, "
              f"{out[0]['tokens_qwen3']} tok, {out[0]['id'][:8]}")
    if unmeasured:
        print(f"⚠️ {len(unmeasured)} live docs have no token_count_qwen3 — "
              f"UNMEASURED, not small; they cannot be ranked here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
