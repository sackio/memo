#!/usr/bin/env python3
"""Audit ATC standing pins -- the highest-leverage instruction surface we have.

⭐ WHY PINS OUTRANK SKILL FILES (Ben, Slack #memo 2026-10-23 06:04: "also need
   to audit atc pins"). An `as:"inline"` reminder is spliced into the
   session-start payload AHEAD OF THE FIRST TURN. A seat reads it as
   established fact with no memory to question it, and never had to ask.
   Measured on the broker 2026-10-23: 135 owners, 322 active, 18 retired.

⛔⛔ THE 10,000-CHARACTER CLIFF IS THE REASON THIS SCRIPT EXISTS.
   Crossing the session-start budget is NOT a trim: the whole payload is
   replaced by a ~2KB preview and NOTHING TELLS THE SEAT ITS TAIL IS GONE.
   A seat that believes it is carrying standing constraints, and is not, is
   strictly more dangerous than one carrying none -- it will act confidently
   on protections that were silently dropped. `pin` refuses an over-budget
   append for exactly this reason, but a pin can also go over when the
   FLEET-STATE board term grows because of somebody else's seat. Nobody
   watches that. This does.

⛔ TRAPS, from `atc` 2026-10-23 (both are empty-result traps):
   1. `/reminders/<x>` answers 200 with an EMPTY LIST for a name that does not
      exist, and only `owner_known:false` distinguishes that from a genuinely
      clean seat. ⇒ Never read an empty list as clean without checking it.
   2. Per-owner `count` means OWNED. A pane also RECEIVES inherited pins; those
      appear in `inherited[]`/`delivered_count`. Audit each pin once, under its
      owner, or every inherited pin is counted twice.
   3. 12 owners have no subscriber record -- down seats that still carry pins.
      ⛔ Do NOT filter them out: a down seat's stale pin fires when it returns.
"""
import json, re, sys, urllib.request
from pathlib import Path

BROKER = "http://192.168.1.168:3030"
BUDGET = 10000                      # session-start payload cliff

# ⛔⛔ A PIN THIS SIZE IS NOT STANDING CONTEXT, IT IS A LOG WITH A TRIGGER ON IT.
#    Measured on the broker 2026-10-23: `as:"dm"` pins hold 1,157,982 chars
#    across 107 records against 606,623 inline across 223 -- the UNGUARDED
#    carrier is now the bigger one. The 10,000-char cliff guard only ever
#    watched `inline`, and `as:"dm"` records are deliberately excluded from
#    that sum (reminders.ts:5562), so growth went where nothing refuses it.
#    ⭐ MY READ, NOT A MEASUREMENT: the guard CAUSED this. A seat that cannot
#       append to its inline pin still has something to keep, and `as:"dm"`
#       accepts any length.
#    ⚠️ AND SINCE 2026-10-11 THOSE RECORDS FIRE AT SESSION START TOO
#       (reminders.ts:4563) -- before that only a cadence partner delivered
#       them. Nothing in reminders.ts truncates a body (:1879), so `cluster`'s
#       712,925-char record is delivered WHOLE on every compaction and every
#       startup: ~178k tokens, against a permanent token embargo.
DM_PIN_OVERSIZE = 20000

# A pin over that size is excluded from the claim-anchor lane and reported as
# ONE finding instead. ⛔ NEVER SILENTLY: a 712,925-char record contributed
# 1,503 of the fleet's 2,552 unanchored claims, so dripping its lines at
# `cluster` would bury the 132 other seats in one outlier's rolling ledger.
# Its defect is that it exists at this size, not that line 900 lacks an id.


# ⛔ OUT OF SCOPE BY THE PIN-AUDIT SKILL'S OWN RULE. A prohibition is the one line
#    class that is never the problem, and only its owner knows which one is
#    load-bearing.
PROHIBITION = re.compile(r"\b(never|do not|don't|must not|cannot|banned|"
                         r"prohibited|refused|forbidden)\b", re.I)

# ⛔⛔ AN OPERATOR DIRECTIVE IS NOT A ROTTING MEASUREMENT, AND ASKING A SEAT TO GO
#    MEMO-IFY ONE IS BOTH WRONG AND IRRITATING. Ben's standing order is "do not
#    re-litigate a decision he has made", and an attributed, dated quote already
#    carries its own handle -- the attribution. Measured on the dry-run: lines
#    like `Ben 2026-09-15: "you're not gatekeeping"` and `SUPPORT seat. NOT a
#    trader (Ben, 2026-08-21)` were being routed as unanchored claims.
DIRECTIVE = re.compile(r"\b(ben|operator|laura)\b\s*[,:(]|\(\s*(ben|operator)\b|"
                       r"\bben (?:said|says|authoris|authoriz|ruled|decided|scoped)", re.I)

# ⛔ PIN HEADERS AND SELF-DESCRIPTION. `PIN alpaca v144 — 2026-10-15.`,
#    `STANDING CONTEXT — aloha (server3). Rev 2026-10-18.`, `This is REMINDER
#    <uuid> — NOT a zone pin`. All dated, none a claim about the world.
META = re.compile(r"^(standing context|pin\b|this is reminder|⏰|retires? when)|"
                  r"\brev \d|\bv\d+\s*[—-]|anything true beyond this session|"
                  r"context past \d+%", re.I)


def is_claim(t: str) -> bool:
    """⭐ ONE DEFINITION OF "CLAIM", used by the drip AND by the headline count.

    ⛔ THEY WERE TWO FUNCTIONS FOR ONE RUN AND THE NUMBERS DISAGREED. The count
       was still accepting QTY-alone after the drip had stopped, so the report
       said "1,002 claims, 50 anchored (5.0%)" over a lane that had picked 409 --
       a denominator that answers a question nobody asked, printed next to the
       lane it does not describe. A percentage is only meaningful against a
       stated population, and the population must be the one being acted on.
    """
    if len(t) < 25 or META.search(t) or PROHIBITION.search(t) or DIRECTIVE.search(t):
        return False
    # ⭐⭐ A VERIFICATION CUE, NOT A BARE DATE. Dropping DATE-alone was the single
    #    biggest precision win: a date attaches to pin headers, operator quotes,
    #    role definitions and delivery receipts just as readily as to a
    #    measurement. A line that says "measured/verified/confirmed/as of" has
    #    declared itself an observation of the world -- that is the population
    #    that rots, and the population a memo id can serve.
    return bool(CUE.search(t))


def claim_lines(body: str, min_age_days: int = 0):
    """Lines that ASSERT something checkable and cite no memo id.

    ⭐ THE SAME DETECTOR ALREADY RUN OVER CLAUDE.md AND SKILL FILES, POINTED AT
       THE PUSH SURFACE WITH THE SHORTEST PATH TO A SEAT'S BELIEFS. A skill file
       is PULL -- a seat has to open it. A pin is spliced in ahead of the first
       turn, so a stale line there is read as established fact by a seat with no
       memory to question it. Ben, Slack #memo 2026-10-23 09:50: "these seats
       build up these pins and start building up scar tissue around different
       stuff they think happened or happened once and it's been fixed".

    ⛔ A DATE IS NOT AN ANCHOR. Only a memo id is a handle: it gives a reader
       somewhere to check and gives the fleet ONE place to correct. Measured
       2026-10-23: of 2,627 checkable claims in fleet pin bodies, 75 cite a memo
       id (2.9%) -- so 2,552 claims can only be corrected by finding every copy.

    ⚠️ Per-LINE, not per-section. A pin has no headings; its unit is the bullet.
    """
    out = []
    for line in body.splitlines():
        t = line.strip()
        if len(t) < 25:
            continue
        # ⛔⛔ CUE OR DATE, **NOT** QTY ALONE -- A NUMBER IS NOT A CLAIM. First cut
        #    accepted any line carrying a quantity, and the first dry-run DM to
        #    `cluster` was half instructions: "⚠️ CONTEXT PAST 60% -- bank it
        #    before you lose it", "⛔⛔ NO quantum-feed SECRET DELETION -- 5 exist
        #    only in-cluster". Both carry a figure; neither asserts anything a
        #    memo could reverify. ⇒ A DETECTOR THAT CRIES WOLF IS WORSE THAN NONE:
        #    the recipient learns to filter the sender and the one real finding
        #    goes with it -- and this lane's first contact is 132 seats at once.
        if not is_claim(t):
            continue
        if has_memo_ref(t):
            continue
        iso, age = newest_date(t)
        # ⚠️ An UNDATED claim is not a young one -- it is one nobody can date at
        #    all, which is worse. It passes the age gate and sorts as 0 so it
        #    cannot outrank a measured 326-day claim (route.py's `oldest`).
        if age is not None and age < min_age_days:
            continue
        out.append((age, t))
    return out

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import RE_FULL, RE_SHORT, is_memo_ref, live_ids, schedule_ids, confirm_dangling   # noqa: E402
from unanchored import CUE, DATE, QTY, has_memo_ref, newest_date   # noqa: E402


def get(path):
    with urllib.request.urlopen(BROKER + path, timeout=25) as r:
        return json.loads(r.read())


def payload_verdict(owner):
    """The broker's OWN verdict for this seat. Returns (verdict, body, max, shed).

    ⛔⛔⛔ FOURTH AND FINAL CORRECTION. Every earlier version computed this and
       every one was wrong. The endpoint says so in its own text:
         "Do not compute your own headroom from a body figure -- re-query and
          read the verdict."
         "DO NOT SUBTRACT ANYTHING FROM 10,000 YOURSELF -- read max_body_chars,
          which has already done it."
       The budget is NOT body-vs-10000. It is body + a per-item WRAPPER (743
       chars for a 1-item pin, far more for many) against a per-seat
       `max_body_chars`, with a 717-char fleet-state board reserve held back
       that ANOTHER seat's status post can spend while you do nothing.

    ⇒ The error was not conservative. Measured 2026-10-23: `unibrowse` 9,622,
      `alpaca` 9,264 and `quantum-navigator` 9,263 all read as merely "at risk"
      against 10,000, and are OVER -- their real ceilings are 7,925 / 7,480 /
      7,830. I told `thread-alpaca-1` to shed 543 when the true figure was
      2,469, so a seat acting on my number would still have been truncated and
      would have believed it was fixed.

    ⚠️ The verdict lives under the PER-TRIGGER keys `compact` and `startup`,
       not at the top level. A top-level read returns None for every seat and
       looks like "no seat has a verdict" -- a clean sweep that is really a
       wrong lookup.
    """
    try:
        d = get(f"/session-payload/{owner}")
    except Exception:
        return None
    if d.get("owner_known") is False:
        return None
    worst = None
    for trig in ("compact", "startup"):
        c = d.get(trig) or {}
        v = c.get("verdict")
        if not v:
            continue
        rank = {"under": 0, "at_risk": 1, "over": 2}.get(v, 0)
        if worst is None or rank > worst[0]:
            worst = (rank, v, c.get("body_chars"), d.get("max_body_chars"), d.get("shed_chars"))
    return worst[1:] if worst else None


def main():
    owners_doc = get("/reminders-owners?include_retired=1")
    owners = owners_doc.get("owners", [])
    unregistered = set(owners_doc.get("unregistered", []) or [])
    print(f"owners: {len(owners)}  ({len(unregistered)} with no subscriber record -- kept)",
          file=sys.stderr)

    full, pref = live_ids()
    sched = schedule_ids()

    findings, n_pins, n_inline, unknown = [], 0, 0, []
    n_claims = n_anchored = n_skipped_claims = 0
    oversize = []

    for o in owners:
        name = o.get("owner")
        if not name:
            continue
        try:
            d = get(f"/reminders/{name}?include_retired=1")
        except Exception as e:
            findings.append({"owner": name, "kind": "unreadable", "detail": str(e)})
            continue

        # ⛔⛔ TRAP 1, AND I GOT IT EXACTLY BACKWARDS FOR A DAY.
        #    The rule is "an empty list from an unknown owner is not a clean
        #    seat". I implemented it as `continue`, which skipped the owner
        #    ENTIRELY -- so instead of not trusting an empty list, I never looked
        #    at a FULL one. MEASURED 2026-10-23 after `atc` pushed back: all 12
        #    of these owners return their pins under `reminders[]`, 16 pins in
        #    total, and I had audited none of them while reporting them as
        #    "unaudited, not clean" -- which sounds like a known gap and was
        #    really a self-inflicted one I could have closed by reading the body
        #    I had already fetched.
        # ⭐ A CAUTION THAT SAYS "DO NOT TRUST X" MUST NOT BE CODED AS "DO NOT
        #    READ X". The first keeps the data and withholds the conclusion; the
        #    second discards the data and reports the withholding as a finding.
        unreg = d.get("owner_known") is False
        if unreg:
            unknown.append(name)

        items = d.get("reminders", d.get("items", [])) or []
        # ⛔ TRAP 2: audit only OWNED pins; inherited ones belong to their owner.
        items = [r for r in items if not r.get("inherited")]

        for r in items:
            n_pins += 1
            body = r.get("body") or ""
            rid = r.get("id", "?")
            retired = bool(r.get("retired"))
            as_ = (r.get("as") or "").lower()
            trigs = {t.get("kind") for t in (r.get("triggers") or [])}

            if as_ == "inline" and not retired:
                n_inline += 1
                # an inline pin with no session-start trigger can never arrive
                if not (trigs & {"compact", "startup"}):
                    findings.append({"owner": name, "kind": "inline-never-fires",
                                     "id": rid, "detail": f"triggers={sorted(trigs)}"})

            if not retired and not (r.get("retire_when") or "").strip():
                findings.append({"owner": name, "kind": "no-retire-when", "id": rid,
                                 "detail": body[:70].replace("\n", " ")})

            # ── the carrier nobody guards ──────────────────────────────
            if not retired and len(body) > DM_PIN_OVERSIZE:
                fires = sorted(trigs & {"compact", "startup", "cadence", "context"})
                oversize.append((len(body), name, as_, rid))
                findings.append({"owner": name, "kind": "dm-pin-oversize", "id": rid,
                                 "detail": f"{len(body):,} chars as={as_ or '?'} "
                                           f"fires={fires} (~{len(body)//4:,} tokens per "
                                           f"delivery; the broker truncates nothing)"})

            # ── checkable claims with no memo id ───────────────────────────
            if not retired:
                if len(body) > DM_PIN_OVERSIZE:
                    n_skipped_claims += len(claim_lines(body))
                else:
                    for line in body.splitlines():
                        t = line.strip()
                        if is_claim(t):
                            n_claims += 1
                            if has_memo_ref(t):
                                n_anchored += 1
                    for age, t in claim_lines(body, min_age_days=0):
                        findings.append({"owner": name, "kind": "unanchored-pin-claim",
                                         "id": rid,
                                         "detail": f"{age if age is not None else '?'}d  "
                                                   f"{t[:110]}"})

            # dangling memo references inside the pin body
            for m in RE_FULL.finditer(body):
                if sched and m.group(0)[:8] in sched:
                    continue
                if is_memo_ref(body, m.start(), m.end(), m.group(0)) and m.group(0) not in full:
                    why = confirm_dangling(m.group(0))
                    if why:
                        findings.append({"owner": name, "kind": "dangling-memo-ref",
                                         "id": rid, "detail": f"{m.group(0)} [{why}]"})
            for m in RE_SHORT.finditer(body):
                sid = m.group(1) or m.group(2)
                if not sid or (sched and sid in sched):
                    continue
                if is_memo_ref(body, m.start(), m.end(), sid) and sid not in pref:
                    why = confirm_dangling(sid)
                    if why:
                        findings.append({"owner": name, "kind": "dangling-memo-ref",
                                         "id": rid, "detail": f"{sid} [{why}]"})

        pv = payload_verdict(name)
        if pv is None and unreg:
            # ⚠️ EXPLAINED, SO NOT A DEFECT. A name with no subscriber record has
            #    no session-start payload to be over budget, and there is no seat
            #    to DM about it. Emitting `budget-unmeasured` here would route 12
            #    undeliverable DMs a night and bury the real ones. Its PINS are
            #    still audited above -- that is the part that matters, because
            #    they fire if the seat ever comes back.
            pass
        elif pv is None:
            findings.append({"owner": name, "kind": "budget-unmeasured", "id": "-",
                             "detail": "no session-payload verdict -- NOT a clean result"})
        else:
            verdict, body, mx, shed = pv
            if verdict == "over":
                findings.append({"owner": name, "kind": "OVER-CLIFF", "id": "-",
                                 "detail": f"body {body} / max {mx} -- SHED {shed}"
                                           f" (payload replaced by a ~2KB preview, SILENTLY)"})
            elif verdict == "at_risk":
                findings.append({"owner": name, "kind": "at-risk-cliff", "id": "-",
                                 "detail": f"body {body} / max {mx} -- shed {shed} to be safe"
                                           f" on any board"})

    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)

    print("\n=== ATC pin audit ===")
    print(f"owners audited : {len(owners) - len(unknown)}")
    print(f"pins audited   : {n_pins}  ({n_inline} inline, counted once under their owner)")
    print(f"findings       : {len(findings)}")
    if unknown:
        print(f"⚠️ no subscriber record for {len(unknown)} owner(s) -- their PINS ARE "
              f"audited above; only their session-start budget is unmeasurable, and "
              f"there is no seat to DM. {unknown[:8]}")
        print("   ⛔ Not retired: a stale pin on a down seat fires when it returns.")
        print("   ⇒ (a) retired seat, delete the pins, vs (b) live seat under another "
              "name: the broker cannot tell these apart (atc, 2026-10-23). Check the "
              "`agents` roster or whether the seat's cwd still exists.")
    print(f"claims in pins : {n_claims}  ({n_anchored} cite a memo id, "
          f"{(100.0*n_anchored/n_claims if n_claims else 0):.1f}%)")
    if n_skipped_claims:
        # ⛔ NEVER A SILENT EXCLUSION. Say how many claims the oversize pins hold,
        #    so the headline percentage is read against a stated denominator.
        print(f"               + {n_skipped_claims} more inside {len(oversize)} pin(s) over "
              f"{DM_PIN_OVERSIZE:,} chars -- counted, NOT dripped (reported as "
              f"dm-pin-oversize instead)")
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        print(f"\n--- {kind}  ({len(by_kind[kind])}) ---")
        # ⛔ THE DRIP KIND MUST NOT BE TRUNCATED. route.py consumes this stdout and
        #    picks 3 seats a night from it; a 25-line display cap would hide most
        #    owners from the picker and look exactly like a short queue.
        cap = None if kind == "unanchored-pin-claim" else 25
        for f in by_kind[kind][:cap]:
            print(f"  {f['owner']:<24} {str(f.get('id',''))[:8]:<10} {f.get('detail','')}")
    print("\n⛔ Mechanical only. A pin whose CLAIM went stale -- the alpaca case -- is")
    print("   invisible here and needs a reader.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
