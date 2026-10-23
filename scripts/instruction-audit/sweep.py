#!/usr/bin/env python3
"""Run the instruction-file audit on EVERY host, and merge the results.

⛔⛔ WHY THIS EXISTS. scan.py audits one host. Ben's directive (Slack #memo
   2026-10-23 05:57, "we need to maintain any files that anyone is using") is
   fleet-wide, and sessions run on four hosts. scan.py's own footer says a clean
   result here is not a clean result for the fleet -- this closes that, instead
   of documenting it forever.

⛔ AN SSH FAILURE IS A HOST PROBLEM, NOT AN EMPTY RESULT. memo-minder learned
   this twice the hard way: a truncated loop and a genuine null are byte-
   identical, and a host that was never searched reads as a host with nothing
   wrong. Every host here lands in exactly one of three states -- searched /
   not-searched / errored -- and the summary prints all three, always.

⭐ DEDUP IS BY OWNERSHIP, NOT BY PATH. Most skills are symlinks from
   ~/.claude/{skills,agents} into /mnt/nas repos, so they are THE SAME BYTES on
   all four hosts: reporting them per-host would quadruple every finding and
   route four DMs for one defect. A finding whose owner resolves to a repo is
   therefore collapsed across hosts; one whose owner is `host-local:` is kept
   per host and tagged, because those genuinely differ -- /home/ben is
   HOST-LOCAL and the same path holds different content on each machine.
"""
import json, subprocess, sys
from pathlib import Path

HOSTS = ["server4", "server3", "server5", "office"]
HERE = Path(__file__).resolve().parent
SCAN = str(HERE / "scan.py")
TIMEOUT = 600


def run_host(host):
    """-> (state, findings, stderr). state in searched|unreachable|errored.

    ⛔ stdout must be PURE JSON. scan.py prints a human report AND writes the
       JSON file, so the report goes to /dev/null and only the file is catted.
       ⚠️ `set -e` is load-bearing: without it this compound exits as its LAST
       stage (`cat`/`rm`), so a scan.py crash would arrive as rc=0 with empty
       stdout -- a host that failed, reported as a host with no findings.
    """
    script = (
        'set -e\n'
        'f=$(mktemp /tmp/instr-audit.XXXXXX.json)\n'
        f'python3 {SCAN} "$HOME/.claude" --json "$f" >/dev/null 2>&1\n'
        'cat "$f"\n'
        'rm -f -- "$f"\n'
    )
    if host == "server4":
        cmd = ["bash", "-c", script]
    else:
        cmd = ["ssh", "-p", "4999", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
               f"ben@{host}", "bash -s"]
    try:
        p = subprocess.run(cmd, input=(None if host == "server4" else script),
                           capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return "errored", [], f"timed out after {TIMEOUT}s -- INCONCLUSIVE, not zero"
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        state = "unreachable" if ("ssh:" in err or "Connection" in err or
                                  "Host key" in err or "Permission denied" in err) else "errored"
        return state, [], err[:300] or f"rc={p.returncode}, no stderr"
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        return "errored", [], f"unparseable JSON ({e}); first 120 chars: {p.stdout[:120]!r}"
    return "searched", doc.get("findings", []), ""


def main():
    results, per_host = {}, {}
    for h in HOSTS:
        state, findings, err = run_host(h)
        per_host[h] = (state, len(findings), err)
        for f in findings:
            f = dict(f)
            owner = f.get("owner", "?")
            if owner.startswith("host-local:"):
                key = (h, owner, f.get("file"), f.get("line"), f.get("ref"))
                f["host"] = h
                f["owner"] = f"{owner}@{h}"      # addressable: which host to fix on
            else:
                # same bytes on every host -- one finding, not four
                key = (owner, Path(f.get("file", "")).name, f.get("line"), f.get("ref"))
            results.setdefault(key, f)

    findings = list(results.values())
    by_owner = {}
    for f in findings:
        by_owner.setdefault(f.get("owner", "?"), []).append(f)

    print("=== instruction-file audit, ALL HOSTS ===")
    for h in HOSTS:
        state, n, err = per_host[h]
        mark = {"searched": "✅", "unreachable": "⛔", "errored": "⛔"}[state]
        print(f"  {mark} {h:<9} {state:<12} {n:>3} raw finding(s)" + (f"  {err}" if err else ""))
    missed = [h for h, (s, _, _) in per_host.items() if s != "searched"]
    if missed:
        print(f"\n⛔ {len(missed)} host(s) NOT SEARCHED: {', '.join(missed)}.")
        print("   This result says NOTHING about them. Not clean -- unaudited.")
    print(f"\nmerged findings   : {len(findings)} across {len(by_owner)} owner(s)")
    print("(shared repo files collapsed across hosts; host-local kept per host)\n")
    for owner in sorted(by_owner, key=lambda o: -len(by_owner[o])):
        print(f"--- {owner}  ({len(by_owner[owner])}) ---")
        for f in by_owner[owner][:40]:
            loc = f"{Path(f.get('file','?')).name}:{f.get('line','?')}"
            print(f"  {f.get('kind',''):<18} {loc:<34} {f.get('ref', f.get('detail',''))}")
    if not findings:
        print("no mechanically-checkable defect found on the hosts that WERE searched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
