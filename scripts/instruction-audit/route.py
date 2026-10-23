#!/usr/bin/env python3
"""DM each owning seat the findings that are THEIRS, once.

⛔⛔ WITHOUT THIS, THE NIGHTLY PHASES ARE NOT MAINTENANCE. scan.py and pins.py
   print to stdout, and in cron nobody reads stdout. A detector wired to a job
   whose output no one reads is indistinguishable from no detector, and it is
   WORSE than none, because the cycle log shows a phase running green.

⛔ ONLY NEW FINDINGS ARE SENT. A nightly repeat of the same finding is noise,
   and noise is how a real finding gets filtered out by the recipient. State
   lives in seen.json keyed on (owner, kind, ref); anything already sent is
   silent until it changes. ⚠️ That means a finding someone IGNORED also goes
   quiet -- so the digest to `memo` always carries the FULL current set, not
   just the new ones, and that is the copy that notices a finding rotting.

⛔ NOT A BROADCAST. One DM per seat that actually has a finding -- 4 seats on
   the first real run, not 135. Ben authorised routing to owning seats in
   #memo 2026-10-23 ("owner makes the edit, I detect and reverify nightly").
   MAX_SEATS caps a runaway: if a corpus incident ever dangles hundreds of
   refs at once, this stops and tells `memo` instead of DMing the fleet.
"""
import json, re, subprocess, sys, urllib.request
from pathlib import Path

BROKER = "http://192.168.1.168:3030"


def get(path):
    with urllib.request.urlopen(BROKER + path, timeout=25) as r:
        return json.loads(r.read())

STATE = Path.home() / ".memo-instruction-audit" / "seen.json"
MAX_SEATS = 12

# ⛔ SEVERITY SPLIT. Only a DEFECT earns a DM. Sitting at 86% of the session-start
#    budget is a fact about headroom, not something broken, and DMing 12 seats
#    about headroom every night is exactly how the 2 seats that are genuinely
#    TRUNCATED get filtered out as noise. Advisories go to the digest only.
ACTIONABLE = {"OVER-CLIFF", "dangling-memo-ref", "unreadable", "budget-unmeasured"}
HERE = Path(__file__).resolve().parent

# skill directory -> the seat that has DEMONSTRATED ownership by acting on a
# finding in it. ⛔ Earn an entry; do not add one from a plausible name match.
HOSTLOCAL_OWNERS = {
    "docker-heal": "system",      # fixed 3 dead ids 2026-10-23, all four hosts
}


def owner_for_hostlocal(owner: str):
    """`host-local:~/.claude/skills/<dir>@<host>` -> (seat, host), or None.

    ⛔ THE HOST IS HALF THE FINDING AND REMAPPING THE OWNER DESTROYS IT. Once the
       group becomes `system`, nothing in the finding line says WHICH machine is
       wrong -- and these files differ per host by definition, which is the whole
       reason they are reported per host. A DM reading "docker-heal:177 cites a
       dead id" sends the seat to look at the copy in front of them, which may be
       the one that is already fixed. Return both and keep the host in the text.
    """
    m = re.search(r"/skills/([^/@]+)", owner)
    if not m:
        return None
    seat = HOSTLOCAL_OWNERS.get(m.group(1))
    if not seat:
        return None
    return seat, (owner.rpartition("@")[2] if "@" in owner else "unknown-host")


def run(script, *args):
    p = subprocess.run([sys.executable, str(HERE / script), *args],
                       capture_output=True, text=True, timeout=1800)
    return p.stdout


def parse(out, source):
    """(owner, kind, ref) from the section-grouped report."""
    found, kind = [], None
    for line in out.splitlines():
        # ⛔ Headers are "--- <group>  (<n>) ---". An earlier version tested
        #    endswith(")") and so NEVER matched, leaving kind None and parsing
        #    ZERO findings out of a report that had 16 -- a silent false all-clear
        #    from the component whose whole job is to stop findings being missed.
        if line.startswith("--- ") and line.rstrip().endswith("---"):
            kind = re.sub(r"\s*\(\d+\)\s*---$", "", line[4:].rstrip()).strip()
            continue
        # ⛔ The trailing caveat block is indented too and was being parsed as a
        #    finding ("invisible: [pin/OVER-CLIFF] here and needs a reader").
        #    Findings are indented exactly 2; the footer starts with ⛔ and wraps
        #    at 3. Stop at the footer and require the finding indent.
        if line.lstrip().startswith("⛔"):
            kind = None
            continue
        if kind and line.startswith("  ") and not line.startswith("   ") and line.strip():
            parts = line.split()
            if len(parts) >= 2:
                found.append({"source": source, "group": kind,
                              "a": parts[0], "rest": " ".join(parts[1:])})
    return found


def cliff_advice(owner):
    """The BROKER's own pruning text for this seat, fetched live -- never copied.

    ⛔ A DM that says "SHED 2469" and nothing else tells a seat the size of its
       problem and not one thing about the fix, so the seat invents one. The
       endpoint already carries authoritative guidance under `how_to_prune`
       ("DELETE, DO NOT REWRITE -- spent pointers first; rewording nets ~zero").
       Forward it instead of paraphrasing: a paraphrase in this file goes stale
       the day the broker's advice changes, and nothing would catch it.

    ⭐ TRIM `retire_when` FIRST -- it is WRAPPER, not body, so it raises your
       CEILING instead of lowering your content, and costs nothing. Measured by
       `unibrowse` 2026-10-23: three retire_when strings, wrapper 1358 -> 1087,
       max_body_chars 7925 -> 8196.
    ⚠️ It is FREE, not BIG. Same measurement: 271 chars from retire_when versus
       1,704 that still had to come out of real standing constraints. A seat
       over by thousands cannot get there this way, and saying otherwise sends
       it looking for budget that is not there.
    """
    try:
        d = get(f"/session-payload/{owner}")
    except Exception:
        return ""
    howto = (d.get("how_to_prune") or "").strip()
    if not howto:
        return ""
    return ("\n\nOVER/AT-RISK ON THE SESSION-START CLIFF. Two levers, free one first:\n"
            "  1. Trim `retire_when` on your pins -- it is WRAPPER, so it RAISES max_body_chars\n"
            "     and costs no content. Free, but bounded: ~271 chars when `unibrowse` did it.\n"
            "  2. Then the body. The broker's own guidance, verbatim:\n\n     " + howto +
            "\n\n⛔ Re-read your verdict from GET /session-payload/<you> after editing. Do not\n"
            "   compute headroom from a body figure -- the budget is body + a per-item wrapper\n"
            "   against YOUR max_body_chars, with a fleet-state reserve another seat can spend.")


def post(to, subject, content):
    body = json.dumps({"to": to, "from": "memo", "durable": True,
                       "priority": "info", "subject": subject,
                       "content": content}).encode()
    req = urllib.request.Request(BROKER + "/messages", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    dry = "--dry-run" in sys.argv
    pins = parse(run("pins.py"), "pin")
    # ⭐ sweep.py, not scan.py: scan.py sees server4 only, and the first all-host
    #    run proved that matters -- docker-heal's fix had landed on server4 alone
    #    while three hosts still served the pre-fix text. Routing off the local
    #    scan would have reported that as clean.
    files = parse(run("sweep.py"), "file")

    # pins.py groups by FINDING KIND with owner in col 1; scan.py groups by OWNER.
    by_owner, advisory = {}, []
    for f in pins:
        line = f"[pin/{f['group']}] {f['rest']}"
        if f["group"] in ACTIONABLE:
            by_owner.setdefault(f["a"], []).append(line)
        else:
            advisory.append(f"{f['a']}: {line}")
    for f in files:
        owner = f["group"]
        if owner.startswith("host-local:"):
            # ⛔⛔ THESE ARE THE MOST ACTIONABLE FINDINGS AND ROUTING USED TO DROP
            #    ALL OF THEM. A host-local skill file has no owner in its path,
            #    so the only reason `system` heard about docker-heal yesterday is
            #    that I hand-delivered it. Unattended, that finding dies in a
            #    digest nobody is obliged to read.
            # ⛔ BUT AN OWNER MUST BE ESTABLISHED, NEVER INFERRED FROM THE NAME.
            #    A DM to a guessed seat is worse than none: it asks the wrong seat
            #    to edit a file it does not own, and the right seat still never
            #    hears. Only entries proven by that seat ACTING on the finding go
            #    in the map; everything else goes to the digest, where a human
            #    assigns it once and it earns a line here.
            mapped = owner_for_hostlocal(owner)
            if not mapped:
                advisory.append(f"UNOWNED {owner}: {f['a']} {f['rest']}")
                continue
            owner, host = mapped
            by_owner.setdefault(owner, []).append(
                f"[file on {host}] {f['a']} {f['rest']}")
            continue
        by_owner.setdefault(owner, []).append(f"[file] {f['a']} {f['rest']}")

    try:
        seen = set(tuple(x) for x in json.loads(STATE.read_text()))
    except Exception:
        seen = set()

    new = {o: [l for l in ls if (o, l) not in seen] for o, ls in by_owner.items()}
    new = {o: ls for o, ls in new.items() if ls}

    if len(new) > MAX_SEATS:
        print(f"⛔ {len(new)} seats have NEW findings, over MAX_SEATS={MAX_SEATS}. "
              f"Not DMing the fleet. Telling memo instead.")
        if not dry:
            post("memo", "instruction audit: unusual finding fan-out",
                 f"{len(new)} seats have new instruction-surface findings, over the "
                 f"{MAX_SEATS} cap. Not routed automatically. Owners:\n  "
                 + ", ".join(sorted(new)))
        return 0

    for owner, lines in sorted(new.items()):
        msg = ("Instruction-surface audit found claims in files or pins you own that "
               "no longer resolve:\n\n  " + "\n  ".join(lines) +
               "\n\nA 410 keeps its title, so the meaning is recoverable and the line can be "
               "rewritten. A 404 keeps nothing -- the surrounding prose is the last record, so "
               "capture it into a new memo BEFORE editing the line.\n\n"
               "Ben authorised this in Slack #memo 2026-10-23: volatile claims in skill/CLAUDE.md "
               "files and pins cite a memo id, the owning seat makes the edit, memo detects and "
               "reverifies nightly. I do not edit other seats' files.\n\n"
               "Reported once. You will not be re-sent this unless it changes.")
        if any("OVER-CLIFF" in l or "at-risk-cliff" in l for l in lines):
            msg += cliff_advice(owner)
        if dry:
            print(f"--- would DM {owner} ---\n{msg}\n")
        else:
            r = post(owner, "instruction-surface findings in files/pins you own", msg)
            print(f"DM {owner}: sent={r.get('sent')} live={r.get('live')}")
        for l in lines:
            seen.add((owner, l))

    if not dry:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(seen)))
    # ⭐ The digest carries the FULL current set, not just the new ones -- it is
    #    the copy that can notice a finding nobody acted on, which the once-only
    #    per-seat DM is structurally blind to.
    digest = (f"instruction surface, {len(by_owner)} seat(s) with defects, "
              f"{len(advisory)} advisory.\n\nDEFECTS (routed):\n  "
              + ("\n  ".join(f"{o}: {'; '.join(ls)}" for o, ls in sorted(by_owner.items())) or "none")
              + "\n\nADVISORY (not routed):\n  " + ("\n  ".join(sorted(advisory)) or "none"))
    if dry:
        print("--- digest to memo ---\n" + digest)
    else:
        post("memo", "instruction-surface audit digest", digest)
    print(f"routed {len(new)} seat(s); {sum(len(v) for v in by_owner.values())} defect(s), "
          f"{len(advisory)} advisory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
