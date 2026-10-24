#!/usr/bin/env python3
"""corpus-echo — given a claim that turned out to be FALSE, find where else it lives.

⭐ WHY THIS EXISTS. Auditing pins alone does not clear the memos they came from.
   Suggested by `earnings` (thread-tradedesk-12) via `tradedesk` 2026-10-24 from a
   live case: a false guard claim sat in pin `c5774304` AND in memo `5bd57a45` for
   16 days. Correcting the pin left the memo saying it, and the memo is the thing
   a future seat will /recall.

⛔⛔ RANK 1 IS THE DOCUMENT *ABOUT* THE CLAIM, WHICH IS NOT THE SAME AS THE ONE
   THAT IS RIGHT -- AND THIS IS WHY NOTHING HERE REPORTS A TOP HIT. Querying the
   false pin line verbatim returned `d5a516ff` at 0.730 and `5bd57a45` at 0.631,
   rank 4. A document whose subject IS a claim out-scores one that merely contains
   it, so a high score tracks aboutness, never truth.
⛔⛔ MEASURED THE SAME DAY, AND IT IS THE WHOLE REASON FOR THE CAUTION: `d5a516ff`,
   the rank-1 hit, was ITSELF FALSE. It declared a pod md5 gate absent; the gate
   exists and fails closed on 8 live Deployments (`set -e`; `md5sum -c -`; `exec`),
   verified against `kubectl get deploy -n tradedesk` and 44 `git grep` hits in 16
   manifests. It has been reversed and retitled. `earnings`: "a correction
   outranking its carrier only helps when the correction is right."
   ⇒ For one day the NEWEST document and the HIGHEST-RANKED document were the same
     document, and it was the wrong one. Neither recency nor rank is evidence.
   Memo `316537b1` records the contest running the other way too (counterweight
   0.547, below the carrier at 0.632), so the order is not controllable either way.

⛔ IT PROPOSES, IT DOES NOT DECIDE. Output is candidates for a human to read. A
   document is not guilty because it ranks well against a false sentence; the
   corpus is full of memos legitimately discussing claims that are wrong.

⚠️ WHAT IT CANNOT SEE, SAID OUT LOUD. The same case had AT LEAST FOUR carriers and
   three were project NOTES files on disk, outside the corpus entirely (dated
   2026-10-08 10:25, 2026-10-23 09:03, 2026-10-23 21:30 -- the last load-bearing
   in a decision). Ordering was NOTES -> memo -> pin. ⇒ A clean result here means
   the CORPUS is clean. It is not a statement about the fleet, and the corpus is
   usually not where a claim was first written.

Usage:
    python3 corpus-echo.py "<the false claim, verbatim>"
    python3 corpus-echo.py --file old-claim.txt [--limit 8]
"""
import json
import re
import sys
import urllib.request

BASE = "http://server4:8000"
LIMIT = 8

# ⚠️ A document carrying one of these NEAR the claim is probably ABOUT the error
#    rather than committing it. That makes it lower priority to repair -- never
#    a reason to drop it, because `5bd57a45` carries a prepended CORRECTION
#    banner and the false sentence, both, in one document.
# ⛔⛔ THE MARKER MUST BE NEAR THE MATCH, NOT ANYWHERE IN THE DOCUMENT. First run
#    of this script tagged 6 of 8 hits "LIKELY A CORRECTION" because long memos
#    almost all contain the word CORRECTED somewhere -- a label that fires on 75%
#    of rows carries no information and makes every row look handled.
# ⭐ `scan.py` already carries this exact rule for citation cues ("THE CUE MUST BE
#    ADJACENT, NOT MERELY ON THE LINE", earned on `finance`'s bank-account ids on
#    2026-10-23). I wrote this file without applying it. ⇒ A rule recorded against
#    one detector is not recorded against the next one you write.
CORRECTION_WINDOW = 400
CORRECTION_MARK = re.compile(
    r'\b(?:CORRECTION|CORRECTED|RETRACT(?:ED|ION)?|SUPERSED(?:ED|ES)|'
    r'WITHDRAWN|NO LONGER TRUE|THIS WAS WRONG|REFUTED)\b')


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def search(query: str, limit: int):
    body = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        BASE + "/search", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def shared_phrase(claim: str, content: str, min_words: int = 6):
    """Longest run of >= min_words consecutive words appearing in both.

    ⭐ THIS IS A TEST-DESIGN GUARD, NOT A DETECTOR. The `earnings` pair was
       offered as the HARD case -- 'refuses to start on a mismatch' vs 'enforced
       by a startup check', no shared tokens, so only semantics could link them.
       Measured: the memo QUOTES the pin's phrase 'the build every number was
       measured with' verbatim, and an exact substring search for it returns 1
       document out of 7,376. The pair is substring-linkable and therefore does
       NOT exercise semantic retrieval at all.
    ⇒ Printing the shared phrase tells you WHICH mechanism found a hit. If every
      hit has one, this lane is doing string matching while looking semantic, and
      a reworded echo -- the case it exists for -- would be missed silently.
    """
    words = re.findall(r"\w+", claim.lower())
    low = " ".join(re.findall(r"\w+", content.lower()))
    best = ""
    for n in range(len(words), min_words - 1, -1):
        for i in range(len(words) - n + 1):
            run = " ".join(words[i:i + n])
            if run in low:
                return run
        if best:
            break
    return best


def main() -> int:
    argv = sys.argv[1:]
    limit = LIMIT
    if "--limit" in argv:
        i = argv.index("--limit")
        limit = int(argv[i + 1])
        del argv[i:i + 2]
    if argv and argv[0] == "--file":
        claim = open(argv[1], encoding="utf-8").read().strip()
    elif argv:
        claim = " ".join(argv).strip()
    else:
        print(__doc__)
        return 2
    if len(claim) < 25:
        print("⛔ claim is too short to search on; give the sentence verbatim")
        return 2

    hits = search(claim, limit)
    print(f"query ({len(claim)} chars): {claim[:120]}...")
    print(f"{len(hits)} candidate(s). ⛔ A correction normally outranks its "
          f"carrier -- read past rank 1.\n")

    for i, h in enumerate(hits, 1):
        doc = h.get("document", h)
        did, title = doc.get("id", "?"), (doc.get("title") or "")
        try:
            content = get("/documents/" + did).get("content", "")
        except Exception as exc:                       # noqa: BLE001
            print(f"  {i}. {h.get('score',0):.3f} {did[:8]}  ⚠️ unreadable: {exc}")
            continue
        phrase = shared_phrase(claim, content)
        # Locate the match before judging it. With no shared phrase there is no
        # region to inspect, so the honest answer is UNLOCATED -- not "carrier".
        marked = None
        if phrase:
            low = content.lower()
            at = low.find(phrase.split()[0])
            if at >= 0:
                lo = max(0, at - CORRECTION_WINDOW)
                marked = bool(CORRECTION_MARK.search(content[lo:at + CORRECTION_WINDOW]))
        # ⛔ DESCRIPTIVE, NOT A VERDICT -- AND THE SECOND TRY AT THIS WAS ALSO
        #    WRONG. Scoping the marker to the match fixed the 75% false-positive
        #    rate and promptly mislabelled `d5a516ff` -- a memo whose TITLE is
        #    "md5 is NOT enforced by any live pod" -- as an uncorrected carrier,
        #    because a refutation need not contain the word CORRECTION. Marker
        #    absent does NOT mean uncorrected, so this no longer says so.
        # ⭐ "Claim text present" is the only strong objective signal here; the
        #    marker is a weak hint in one direction only.
        kind = ("claim text PRESENT · marker nearby" if marked else
                "claim text PRESENT · no marker (may still refute — read it)"
                if marked is False else "semantic only — read it")
        how = f'substring: "{phrase}"' if phrase else "semantic only (no shared phrase)"
        print(f"  {i}. {h.get('score',0):.3f} {did[:8]}  [{kind}]  {title[:62]}")
        print(f"        matched by {how}")
        if marked:
            print("        ⚠️ a correction BANNER does not mean the false text was "
                  "removed -- a prepend leaves the body intact. Read the body.")
    print("\n⛔ Candidates only. Read each before editing anything, and remember "
          "a clean result here says nothing about NOTES files or code comments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
