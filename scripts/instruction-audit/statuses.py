#!/usr/bin/env python3
"""Audit the ATC STATUS BOARD -- the third push surface, and the unwatched one.

⭐ WHY THIS EXISTS. Ben, Slack #memo 2026-10-23 09:06: the maintenance sweep must
   cover "reminders and statuses in ATC ... finding anything that needs to move to
   memo and that it should reference memo and whether things are correct or not".
   Pins had `pins.py`; instruction files had `scan.py`/`unanchored.py`. The status
   board had nothing, and it is a PUSH surface exactly like a pin: a status is
   rendered into other seats' session-start payload, so a seat reads it as current
   fleet state with no memory to question it.

⛔⛔ THE STRUCTURAL DEFECT, MEASURED 2026-10-23: A STATUS WITHOUT A TTL NEVER
   RETIRES, AND ALMOST NONE HAVE ONE. `/statuses?active=1` returned all 94 rows
   -- i.e. nothing had aged out -- and 82 of the 94 carried neither `ttl_seconds`
   nor `expires_at`. 64 were per-cycle attestations from `quantum-data-guardian`
   and `quantum-library-guardian` dated 89-93 days earlier, and three were the
   literal string "hello world" from a smoke test 70 days ago. The board is not
   a log; every row on it is a live assertion about now.

⛔ THE SHARPEST CASE, and the reason this is not merely untidy: `scripts` holds
   BOTH "TAKING server5 %61 (quantum-engine) -- Do not converge" AND its own
   later "CORRECTION ... RELEASED, no action was needed", 54.9 days old, live
   together with no ordering cue a reader is obliged to notice. A seat that acts
   on the first and misses the second does the wrong thing from a true record.
   ⇒ A CORRECTION THAT DOES NOT RETIRE WHAT IT CORRECTS IS A SECOND CLAIM, NOT A
     FIX. That is the finding `contradicted-by-later` exists to catch.

⚠️ WHAT THIS CANNOT SEE. It judges STRUCTURE -- age, TTL, contradiction, churn,
   dead memo ids. A status whose CLAIM quietly went false while looking fresh is
   invisible here and needs a reader, same caveat as pins.py.
"""
import argparse, json, re, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BROKER = "http://192.168.1.168:3030"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan import RE_FULL, RE_SHORT, is_memo_ref, live_ids, schedule_ids, confirm_dangling  # noqa: E402

CORRECTION = re.compile(r"\b(CORRECTION|RETRACT(?:ED|ION)?|SUPERSED(?:ED|ES)|RELEASED|"
                        r"NO LONGER|DISREGARD|IGNORE THE ABOVE|CANCELLED)\b", re.I)

# ⛔⛔ A CORRECTION WORD ALONE IS NOT A REFERENCE, AND PAIRING ON IT ALONE MAKES A
#    FALSE-POSITIVE ENGINE. First cut of this detector flagged EVERY row from
#    `quantum-data-guardian` as contradicted, because one later status happened to
#    say "my F2 SUPERSEDED" about something unrelated -- 105 findings of which
#    essentially all were wrong. A detector that cries wolf is worse than none:
#    the recipient learns to filter this sender, and the one real case goes with it.
# ⇒ The later status must actually POINT AT the earlier one: by citing its id, or
#   by naming the same concrete RESOURCE. Both real cases satisfy this --
#   `unibrowse` wrote "CORRECTION to my :9010 restart heads-up (54a3a6d8)" (id AND
#   port), and `scripts` wrote "CORRECTION on server5 %61" against "TAKING server5
#   %61" (resource).
# ⚠️ NO BARE HOSTNAMES HERE. `server3`/`worker18`/`gpu1` are ambient on this fleet
#    -- nearly every status names one -- so pairing on them matched 8 unrelated
#    attestations to one later post. A shared token only means something if it is
#    SPECIFIC: a port, a slot, a section number, a path, a filename.
RESOURCE = re.compile(r"(%\d+|:\d{4,5}\b|"
                      r"§[\d.]+|/[A-Za-z][A-Za-z0-9_./-]{5,}|\b[a-z0-9-]+\.(?:py|sh|md|json|ya?ml)\b)")
# ⚠️ The path alternative requires a LETTER after the slash. Without it, `/145/169m`
#    inside a prose figure matched as a shared "path" and manufactured a pairing.


def points_at(later: str, earlier: str, earlier_id: str) -> str | None:
    """Does `later` actually refer to `earlier`? -> the evidence, or None."""
    if earlier_id and earlier_id[:8] in later:
        return f"cites {earlier_id[:8]}"
    shared = {m.group(0) for m in RESOURCE.finditer(later)} & \
             {m.group(0) for m in RESOURCE.finditer(earlier)}
    # ⚠️ Require a SHARED CONCRETE RESOURCE, not a shared word. Prose overlap
    #    between two posts by the same seat is the normal case, not a signal.
    return f"both name {sorted(shared)[0]}" if shared else None


def get(path):
    with urllib.request.urlopen(BROKER + path, timeout=25) as r:
        return json.loads(r.read())


def age_days(s, now):
    """⚠️ `created_at` is ISO here but `expires_at` is EPOCH MILLISECONDS on the
    same object. Reading the second with fromisoformat throws, and a caller that
    swallows it counts every row as 'no expiry' -- which is what I did on the
    first pass, turning 12 TTLs into 0 and making the board look worse than it is.
    """
    try:
        t = datetime.fromisoformat(str(s.get("created_at")).replace("Z", "+00:00"))
    except Exception:
        return None
    return (now - t).total_seconds() / 86400.0


def has_expiry(s, now):
    if s.get("ttl_seconds"):
        return True
    e = s.get("expires_at")
    if e is None:
        return False
    if isinstance(e, (int, float)):
        return e > now.timestamp() * 1000
    try:
        return datetime.fromisoformat(str(e).replace("Z", "+00:00")) > now
    except Exception:
        return False


def resolve_owner(agent_id, known):
    """agent_id -> ATC subscriber id, or None.

    ⛔ VERIFIED, NOT GUESSED. Statuses are posted as `claude-<seat>` by seats and
       as a bare name by bots (`lan-scanner`, `zfs-backup-watchdog`, `smoke`).
       Stripping the prefix is only safe because the result is then CHECKED
       against the broker's live subscriber set; an unresolvable name goes to the
       digest rather than earning a DM to a seat that may not exist. A DM to a
       guessed seat is worse than none -- the right owner still never hears.
    """
    for cand in ((agent_id[len("claude-"):],) if agent_id.startswith("claude-") else ()) + (agent_id,):
        if cand in known:
            return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-age-days", type=float, default=7.0,
                    help="a status younger than this is current business, not rot")
    ap.add_argument("--churn-min", type=int, default=3,
                    help="N near-identical rows from one agent = posting instead of replacing")
    ap.add_argument("--json", metavar="FILE")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)

    rows = get("/statuses?active=1")
    known = {s.get("id") for s in get("/sessions")} | \
            {o.get("owner") for o in get("/reminders-owners?include_retired=1").get("owners", [])}
    full, pref = live_ids()
    sched = schedule_ids()

    by_agent = {}
    for s in rows:
        by_agent.setdefault(s.get("agent_id", "?"), []).append(s)
    for v in by_agent.values():
        v.sort(key=lambda s: s.get("created_at") or "")

    findings, unresolved = [], set()
    for agent, items in by_agent.items():
        owner = resolve_owner(agent, known)
        if owner is None:
            unresolved.add(agent)
        key = owner or f"unresolved:{agent}"

        # churn: same opening line again and again, digits normalised away
        seen_head = {}
        for s in items:
            head = re.sub(r"\d+", "#", (s.get("content") or "").strip().split("\n")[0])[:60]
            seen_head.setdefault(head, []).append(s)

        later_correction = [s for s in items if CORRECTION.search(s.get("content") or "")]

        for s in items:
            age = age_days(s, now)
            body = s.get("content") or ""
            sid = str(s.get("id", "?"))[:8]
            head = body.strip().split("\n")[0][:70].replace("\n", " ")

            if age is not None and age >= a.min_age_days and not has_expiry(s, now):
                findings.append({"owner": key, "kind": "no-ttl-never-retires", "id": sid,
                                 "detail": f"{age:.0f}d  {head}"})

            # a later status from the SAME agent that corrects/retracts, while this
            # one is still live. ⚠️ Only flags the EARLIER row -- the correction
            # itself is not the defect.
            if age is not None and not CORRECTION.search(body):
                for c in later_correction:
                    if (c.get("created_at") or "") <= (s.get("created_at") or ""):
                        continue
                    why = points_at(c.get("content") or "", body, str(s.get("id", "")))
                    if why:
                        findings.append({"owner": key, "kind": "contradicted-by-later", "id": sid,
                                         "detail": f"{age:.0f}d  {head}  <- {str(c['id'])[:8]} "
                                                   f"corrects this ({why}), both still live"})
                        break

            for m in RE_FULL.finditer(body):
                if sched and m.group(0)[:8] in sched:
                    continue
                if is_memo_ref(body, m.start(), m.end(), m.group(0)) and m.group(0) not in full:
                    if why := confirm_dangling(m.group(0)):
                        findings.append({"owner": key, "kind": "dangling-memo-ref", "id": sid,
                                         "detail": f"{m.group(0)} [{why}]"})
            for m in RE_SHORT.finditer(body):
                short = m.group(1) or m.group(2)
                if not short or (sched and short in sched):
                    continue
                if is_memo_ref(body, m.start(), m.end(), short) and short not in pref:
                    if why := confirm_dangling(short):
                        findings.append({"owner": key, "kind": "dangling-memo-ref", "id": sid,
                                         "detail": f"{short} [{why}]"})

        for head, group in seen_head.items():
            if len(group) >= a.churn_min:
                findings.append({"owner": key, "kind": "churn-posting-not-replacing", "id": "-",
                                 "detail": f"{len(group)}x near-identical: {head}"})

    by_owner = {}
    for f in findings:
        by_owner.setdefault(f["owner"], []).append(f)

    no_ttl = sum(1 for s in rows if not has_expiry(s, now))
    print("\n=== ATC status board audit ===")
    print(f"statuses live      : {len(rows)}   (every row here is DELIVERED, not archived)")
    print(f"  carry no expiry  : {no_ttl}  <- these never retire on their own")
    print(f"agents posting     : {len(by_agent)}")
    print(f"findings           : {len(findings)} across {len(by_owner)} owner(s)")
    if unresolved:
        print(f"⚠️ {len(unresolved)} agent_id(s) do not resolve to a live subscriber: "
              f"{sorted(unresolved)[:8]}")
        print("   Reported, never DMed -- an unresolvable name is not a seat to tell.")
    for owner in sorted(by_owner, key=lambda o: -len(by_owner[o])):
        print(f"\n--- {owner}  ({len(by_owner[owner])}) ---")
        for f in by_owner[owner][:20]:
            print(f"  {f['kind']:<28} {f['id']:<9} {f['detail']}")
    print("\n⛔ Structure only. A status whose CLAIM went false while looking fresh is")
    print("   invisible here and needs a reader.")
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"statuses": len(rows), "no_expiry": no_ttl, "findings": findings}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
