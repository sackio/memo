#!/usr/bin/env python3
"""Find VOLATILE CLAIMS in instruction files that carry no memo id.

⭐⭐ WHY THIS IS A DIFFERENT SCAN FROM `scan.py`, AND WHY THAT ONE CANNOT SEE
   THIS CLASS. scan.py checks that the refs a file DOES carry still resolve. A
   claim with no ref at all has nothing to dangle, so it passes every check
   this lane had until 2026-10-23 and rots in total silence.

⭐ THE MEASURED CASE IT WAS BUILT FROM. `~/.claude/skills/massive-live/SKILL.md`
   told every reader the Massive live feed "drops 62-72% of trades at the open
   and carries conflicting duplicates on 23 big names". True when written
   2026-09-22; FIXED 2026-09-22 and 2026-09-30; still on the page a month
   later. On 2026-10-23 `alpaca` designed its paper-engine fill pipeline around
   the dead fault -- routing fills through REST instead of the live feed -- and
   Ben escalated to Massive over a bug that no longer existed, then: "Jesus
   that crap needs to come out of the skills page" (#alpaca.paper-engine).
   ⇒ The page was not WRONG when written. It was UNANCHORED, so nothing and
   nobody was ever prompted to look at it again.

⛔ DATING A CLAIM DOES NOT MAKE ANYONE RECHECK IT. The stale block carried its
   date in plain sight ("quantum-engine measured this 09-22") and `alpaca` read
   the date and believed it anyway. A date is provenance; only a memo id is a
   HANDLE something else can pull on. That is the whole point of Ben's
   2026-10-23 10:03 "yes" in #memo: owner puts an id on the claim, this seat
   reverifies it nightly and routes the drift back.

⚠️⚠️ THIS IS A HEURISTIC AND ITS DENOMINATOR IS NOT "ALL VOLATILE CLAIMS".
   "Volatile" is not mechanically decidable, so a section is flagged only when
   it carries ALL THREE of a measurement cue, a date, and a quantity. That is
   deliberately narrow: a quantitative measured claim is the kind that both
   rots and misleads, and it is the kind a reader trusts hardest. A volatile
   claim with no number ("the vendor replays on reconnect") is INVISIBLE here.
   ⇒ Report the counts, never "no unanchored claims".
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scan import collect, owner_of, RE_FULL, RE_SHORT, is_memo_ref   # noqa: E402

HEADING = re.compile(r'^(#{1,6})\s+\S')

# Someone went and LOOKED. Opinion and design intent are not claims that rot.
CUE = re.compile(r'\b(measured|verified|confirmed|reproduced|observed|'
                 r'benchmark(?:ed)?|timed|still open|as of|on the wire)\b', re.I)
# ⛔⛔ CUE MATCHES THE WORD, NOT ITS GRAMMATICAL ROLE -- AND THAT WAS INVISIBLE
#    UNTIL THE CUE WAS PRINTED BESIDE THE LINE. MEASURED 2026-10-24 on the 14
#    pin lines queued for DMs that night: roughly half were not observations at
#    all. "cost ... must be MEASURED AT THE CLOSE" is an imperative,
#    "Authority = OBSERVED BEHAVIOUR OF THE LIVE SYSTEM" is a definition,
#    '"measured and rejected" is for a structure that LOST' is a quoted term,
#    and "THE SHAPES THAT ACTUALLY BITE -- all measured, all mine:" is a list
#    header. None of them declares that anyone went and looked.
# ⭐ SAME ADJACENCY RULE corpus-echo.py and scan.py already carry: the marker
#    must be read IN CONTEXT, not merely found in the string. Applied here to
#    the 20 characters in front of the cue.
# ⚠️ USED BY THE PIN LANE ONLY SO FAR (pins.is_claim). unanchored.scan_file
#    still takes a bare CUE hit, deliberately: the false-positive rate above was
#    measured on PIN lines and has not been measured on SKILL.md prose, and this
#    lane DMs 132 seats. Measure there before adopting it -- do not assume a
#    rule earned on one surface transfers to the next.
NOT_DECLARATION = re.compile(
    r"(?:\b(?:must|should|shall|can|could|to|be|being|been|get|gets|"
    r"always|never)\s+(?:be\s+)?$"      # imperative: "must be measured"
    r"|[=≡:]\s*(?:\*\*)?$"         # definition: "Authority = OBSERVED ..."
    r"|[\"“‘'`]$)",           # a quoted term being discussed
    re.I)


# \u26a0\ufe0f ONE KNOWN MISS, RECORDED AND DELIBERATELY NOT ACTED ON. `quantum-data`
#    2026-10-24 22:13: a flagged line's "measured" belonged to a TASK THEY OWE
#    (\u201csend GB/day after day 1\u201d), not to an observation. NOT_DECLARATION looks at
#    the 20 chars before the cue for an imperative, a definition or a quote, and
#    an obligation phrased as a plain future clears all three. They reworded the
#    line before I could read it, so what I have is their paraphrase of a line I
#    never saw -- and widening a detector on a paraphrase is how a wrong rule
#    gets in. ONE instance is not a pattern: left alone on purpose, so a SECOND
#    one is visible as the second rather than as the first.
# \u2b50 AND IT IS NOW THE SECOND, FOUND THE SAME NIGHT BY CHECKING INSTEAD OF
#    ASSUMING: `apis` is holder-exempted for "PROJECT: apis \u2014 DONE = every BUILD
#    item ... passes Apify's daily auto-check, verified ..." \u2014 reason recorded
#    2026-10-23, "a target, not an observation". Same category as quantum-data's:
#    THE CUE ATTACHES TO A GOAL OR AN OWED DELIVERABLE, NOT TO SOMETHING SOMEONE
#    WENT AND LOOKED AT. NOT_DECLARATION already carries a definition guard
#    (`DONE =`), and it misses this because the cue sits far past the 20-char
#    lookback, deep in the clause the definition introduces.
# \u26d4 STILL NOT WIDENING IT, AND THIS IS THE REASON RATHER THAN CAUTION: both
#    instances were resolved AT THE HOLDER -- one exemption, one reword -- which
#    is what the three-option DM is for. Widening the regex to swallow a whole
#    clause after `=` buys one fewer DM per seat and pays for it in false
#    NEGATIVES, which are silent, permanent, and the failure this lane exists to
#    prevent. A third instance that the holder does NOT resolve is what should
#    change the code.
# ⭐ A CUE GOVERNED BY A PREPOSITION IS AN ADJECTIVE ON THE NEXT NOUN, NOT A
#    REPORT. "after MEASURED slippage", "vs measured peaks", "on OBSERVED rain"
#    name a KIND of slippage/peak/rain; nobody is saying they went and looked.
#    A real report puts the cue ahead of its preposition ("measured ON the
#    production DB"), so this cannot swallow one.
GOVERNED = re.compile(r'\b(?:after|on|vs\.?|versus|with|from|than|per|against|'
                      r'using|of|into|about)\s+$', re.I)


def _inside_quote(t: str, pos: int) -> bool:
    """Is `pos` inside an open double-quoted span?

    ⛔ NOT THE SAME TEST AS NOT_DECLARATION's TRAILING-QUOTE GUARD, WHICH IS WHY
       THAT GUARD MISSED THE CASE THAT PROMPTED THIS. That one asks whether a
       quote sits in the 20 chars IMMEDIATELY BEFORE the cue, so it catches a
       quoted TERM (`"measured and rejected" is for ...`) and is blind to a cue
       buried mid-quotation: `"absent as of T, re-check at T+n"` opens its quote
       seven characters too early. Counting delimiters answers the question the
       lookback only approximates.
    """
    seg = t[:pos]
    return (seg.count('"') + seg.count('\u201c') - seg.count('\u201d')) % 2 == 1


def declared_observation(t: str):
    """The CUE match only where it reads as "someone went and LOOKED".

    Returns the match object, or None when the cue is an imperative, a
    definition, a quoted term, a modifier governed by a preposition, or sits
    inside a quotation -- or when the line is a bare header ending in `:`.

    ⛔⛔ EVERY TEST HERE REJECTS ONE CUE AND KEEPS SCANNING. It must never
       reject the LINE: a line can carry an adjectival cue AND a real one
       ("confirmed on measured data that p99 is 40ms"), and a line-level veto
       would silently drop the claim because of the decoration beside it. That
       is a FALSE NEGATIVE, which is the failure this lane exists to prevent,
       and it is invisible. The first draft of the two tests below was written
       as a line-level filter and had exactly this hole.
    """
    # ⚠️ A header introducing a list is not a claim; its items are. Checked
    #    before the cue so "-- all measured, all mine:" cannot slip through.
    if t.rstrip().endswith(":"):
        return None
    for m in CUE.finditer(t):
        before = t[max(0, m.start() - 20):m.start()]
        if NOT_DECLARATION.search(before) or GOVERNED.search(before):
            continue
        if _inside_quote(t, m.start()):
            continue
        return m
    return None


# A date in a form that pins the claim to an instant.
DATE = re.compile(r'\b20\d{2}-\d{2}(?:-\d{2})?\b')
# A magnitude. This is what makes it checkable -- and what makes a reader act.
QTY = re.compile(r'\d+(?:\.\d+)?\s*(?:%|bps|ms|µs|s\b|sec|GB|MB|KB|x\b|times|'
                 r'per day|/day|per second|/s\b)', re.I)


def sections(text):
    """-> [(heading, start_line, [lines])]. No headings => the file is one."""
    lines = text.splitlines()
    marks = [i for i, l in enumerate(lines) if HEADING.match(l)]
    if not marks:
        return [("(file)", 1, lines)]
    out = []
    if marks[0] > 0:
        out.append(("(preamble)", 1, lines[:marks[0]]))
    for n, i in enumerate(marks):
        end = marks[n + 1] if n + 1 < len(marks) else len(lines)
        out.append((lines[i].lstrip("# ").strip(), i + 1, lines[i:end]))
    return out


def has_memo_ref(body: str) -> bool:
    """A VALIDATED ref, by scan.py's own precision gate.

    ⛔ Not a bare 32-hex match: this fleet writes session ids, image digests and
       beacon ids in the same shape, and skill files quote bad ids on purpose.
       Counting one of those as an anchor would mark a rotting claim as safe --
       the failure direction that produces a clean report and no protection.
    """
    for m in RE_FULL.finditer(body):
        if is_memo_ref(body, m.start(), m.end(), m.group(0)):
            return True
    for m in RE_SHORT.finditer(body):
        ident = m.group(1) or m.group(2)
        if ident and is_memo_ref(body, m.start(), m.end(), ident):
            return True
    return False


def newest_date(body: str):
    """The most recent date the section cites, as (iso, days_old).

    ⭐ AGE IS THE ONLY PRIORITY SIGNAL THIS SCAN HONESTLY HAS. It cannot tell a
       claim that is still true from one that is false -- only a reverification
       does that. What it CAN say is how long nobody has looked, and the case
       this was built from had gone 31 days. ⚠️ Ordering by age is NOT evidence
       that the oldest are wrong; it is where to spend the first reads.
    ⚠️ Takes the NEWEST date in the section, not the oldest. A block that was
       revisited last week is anchored in time even if it also narrates a
       September incident -- scoring it by the September date would push a
       maintained section to the top of the queue.
    """
    best = None
    for m in DATE.finditer(body):
        t = m.group(0)
        try:
            d = date.fromisoformat(t if len(t) == 10 else t + "-01")
        except ValueError:
            continue
        if best is None or d > best:
            best = d
    if best is None:
        return None, None
    return best.isoformat(), (date.today() - best).days


def scan_file(p: Path):
    try:
        text = p.read_text(errors="replace")
    except OSError as e:
        return [{"file": str(p), "owner": owner_of(p), "kind": "unreadable",
                 "detail": str(e)}], 0, 0
    found, n_sec, n_cand = [], 0, 0
    for title, start, lines in sections(text):
        n_sec += 1
        body = "\n".join(lines)
        if not (CUE.search(body) and DATE.search(body) and QTY.search(body)):
            continue
        n_cand += 1
        if has_memo_ref(body):
            continue
        # name the line a human should look at: the one carrying the date
        trigger, tline = "", start
        for off, l in enumerate(lines):
            if DATE.search(l) and (CUE.search(l) or QTY.search(l)):
                trigger, tline = l.strip()[:150], start + off
                break
        newest, age = newest_date(body)
        found.append({"file": str(p), "owner": owner_of(p),
                      "kind": "unanchored-volatile-claim",
                      "section": title[:70], "line": tline,
                      "dated": newest, "age_days": age,
                      "detail": trigger or title[:70]})
    return found, n_sec, n_cand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="*", default=["~/.claude"])
    ap.add_argument("--json", metavar="FILE")
    ap.add_argument("--maxdepth", type=int, default=3)
    ap.add_argument("--min-age-days", type=int, default=0,
                    help="only report claims whose newest date is at least this old")
    a = ap.parse_args()

    files = collect(a.roots or ["~/.claude"], maxdepth=a.maxdepth)
    findings, n_sec, n_cand = [], 0, 0
    for p in files:
        f, s, c = scan_file(p)
        findings += f; n_sec += s; n_cand += c

    unanch = [f for f in findings if f["kind"] == "unanchored-volatile-claim"]
    anchored = n_cand - len(unanch)
    if a.min_age_days:
        keep = [f for f in unanch if (f.get("age_days") or 0) >= a.min_age_days]
        findings = [f for f in findings if f in keep or
                    f["kind"] != "unanchored-volatile-claim"]
    findings.sort(key=lambda f: -(f.get("age_days") or 0))
    by_owner = {}
    for f in findings:
        by_owner.setdefault(f["owner"], []).append(f)

    print("\n=== unanchored volatile claims ===")
    print(f"files scanned      : {len(files)}")
    print(f"sections           : {n_sec}")
    print(f"quantitative dated : {n_cand}   <- the only population this can judge")
    print(f"  anchored to memo : {anchored}")
    print(f"  UNANCHORED       : {n_cand - anchored}  across {len(by_owner)} owner(s)")
    if a.min_age_days:
        print(f"  shown            : {len(unanch and [f for f in findings])} "
              f"at >= {a.min_age_days} days old")
    ages = sorted((f["age_days"] for f in unanch if f.get("age_days")), reverse=True)
    if ages:
        print(f"  age of the claim : oldest {ages[0]}d · median {ages[len(ages)//2]}d "
              f"· newest {ages[-1]}d   (31d is what the massive-live case cost)")
    for owner in sorted(by_owner, key=lambda o: -len(by_owner[o])):
        print(f"\n--- {owner}  ({len(by_owner[owner])}) ---")
        for f in by_owner[owner][:25]:
            age = f.get("age_days")
            tag = f"{age}d" if age is not None else "undated"
            _p = Path(f['file'])
            _loc = (f"{_p.parent.name}/{_p.name}"
                    if _p.name in {"SKILL.md", "CLAUDE.md", "AGENTS.md", "README.md", "index.md"}
                    and _p.parent.name else _p.name)
            print(f"  {tag:>7}  {_loc}:{f.get('line','?')}  [{f.get('section','')}]")
            print(f"      {f.get('detail','')}")
    print("\n⚠️ A claim with no number or no date is INVISIBLE here. This counts"
          "\n   the quantitative dated population only -- never read a 0 as"
          "\n   'nothing in these files will rot'.")
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"files": len(files), "sections": n_sec, "candidates": n_cand,
             "anchored": anchored, "findings": findings}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
