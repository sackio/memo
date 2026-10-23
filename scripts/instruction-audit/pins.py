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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import RE_FULL, RE_SHORT, is_memo_ref, live_ids, schedule_ids, confirm_dangling   # noqa: E402


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

    for o in owners:
        name = o.get("owner")
        if not name:
            continue
        try:
            d = get(f"/reminders/{name}?include_retired=1")
        except Exception as e:
            findings.append({"owner": name, "kind": "unreadable", "detail": str(e)})
            continue

        # ⛔ TRAP 1: empty + owner_known:false is NOT a clean seat.
        if d.get("owner_known") is False:
            unknown.append(name)
            continue

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
        if pv is None:
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
        print(f"⛔ owner_known:false for {len(unknown)} -- NOT audited, NOT clean: {unknown[:8]}")
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        print(f"\n--- {kind}  ({len(by_kind[kind])}) ---")
        for f in by_kind[kind][:25]:
            print(f"  {f['owner']:<24} {str(f.get('id',''))[:8]:<10} {f.get('detail','')}")
    print("\n⛔ Mechanical only. A pin whose CLAIM went stale -- the alpaca case -- is")
    print("   invisible here and needs a reader.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
