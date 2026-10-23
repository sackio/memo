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
STATE = Path.home() / ".memo-instruction-audit" / "seen.json"
MAX_SEATS = 12

# ⛔ SEVERITY SPLIT. Only a DEFECT earns a DM. Sitting at 86% of the session-start
#    budget is a fact about headroom, not something broken, and DMing 12 seats
#    about headroom every night is exactly how the 2 seats that are genuinely
#    TRUNCATED get filtered out as noise. Advisories go to the digest only.
ACTIONABLE = {"OVER-CLIFF", "dangling-memo-ref", "unreadable", "budget-unmeasured"}
HERE = Path(__file__).resolve().parent


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
    files = parse(run("scan.py", str(Path.home() / ".claude")), "file")

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
            continue                       # no seat owns it -- goes to the digest only
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
