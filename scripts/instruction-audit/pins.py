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


def payload_exposure(owner):
    """What a pane ACTUALLY receives at session start, per the broker's own rule.

    ⛔⛔ THREE WRONG ANSWERS CAME BEFORE THIS ONE, each a plausible aggregate:
       1. sum of every live inline body -> ignores SCOPE. `tradedesk` read 258%
          over; 16 of its 19 pins are scoped to other conversations and never
          arrive at that wire.
       2. the per-owner `delivered_chars` -> includes `as:dm` bodies, which are
          delivered separately and never touch this cliff. `cluster` holds one
          712,925-char dm pin and read 7,534% over.
       3. the per-item `over_soft_budget` flag -> that is a 2,000-char PER-ITEM
          advisory, not the 10,000 payload cliff. 102 pins trip it; it means
          nothing about truncation.

    The rule, quoted from /session-payload's own response: "Each pane receives
    the (unscoped) group PLUS its own conversation's group -- never the sum of
    all groups. inline_chars is the only figure the SessionStart cliff governs;
    as:dm bodies are delivered separately and are excluded."

    ⭐ THE GENERAL LESSON, which cost four iterations: when the system publishes
       a verdict, recomputing it yourself is not independent verification -- it
       is a second chance to be wrong, with the authority of your own arithmetic.
    """
    try:
        d = get(f"/session-payload/{owner}")
    except Exception:
        return None
    if d.get("owner_known") is False:
        return None
    unscoped, convs = 0, [0]
    for k, v in (d.get("scopes") or {}).items():
        n = v.get("inline_chars") or 0
        if k.startswith("(unscoped"):
            unscoped += n
        else:
            convs.append(n)
    return unscoped + max(convs)       # worst-case pane, not the sum


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

        worst = payload_exposure(name)
        if worst is None:
            findings.append({"owner": name, "kind": "budget-unmeasured", "id": "-",
                             "detail": "no session-payload answer -- NOT a clean result"})
        else:
            pct = 100.0 * worst / BUDGET
            if worst > BUDGET:
                findings.append({"owner": name, "kind": "OVER-CLIFF", "id": "-",
                                 "detail": f"{worst} utf16 units = {pct:.0f}% of {BUDGET}"
                                           f" -- payload replaced by a ~2KB preview, SILENTLY"})
            elif pct >= 85:
                findings.append({"owner": name, "kind": "at-risk-cliff", "id": "-",
                                 "detail": f"{worst} utf16 units = {pct:.0f}%"})

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
