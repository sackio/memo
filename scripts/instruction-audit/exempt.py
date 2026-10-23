#!/usr/bin/env python3
"""Record a pin line its HOLDER has declared not-a-claim, so the nightly lane
stops asking about it.

⭐⭐ WHY THIS SCRIPT EXISTS, AND WHY IT IS NOT OPTIONAL. The DM the pin lane
   sends offers three options, and the third is "NOT A CLAIM: say so and I will
   stop flagging that line." Within 20 minutes of the first fleet-wide send,
   twelve seats took option 3 -- and almost every one of them was RIGHT: a
   section header, a project done-definition, a classification rule, a list of
   operator rulings, a pointer with a REFUTE probe. If the next sweep re-asks
   them, the lane has broken its own promise to a dozen seats at once, which is
   exactly how a detector earns a filter.

⛔ AN EXEMPTION IS THE HOLDER'S JUDGEMENT, NOT MINE. Each record names who
   declared it and when, so a later reader can see it was granted rather than
   assumed -- and can re-open it with the seat if the line turns out to assert
   something after all.

Usage:
  exempt.py --from-decls decls.json          # [{owner, pin, match, who, note}]
  exempt.py --list
"""
import argparse, json, re, subprocess, sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path.home() / ".memo-instruction-audit" / "pin-exempt.json"


def claim_key(rest: str) -> str:
    """⚠️ MUST MATCH route.py's claim_key EXACTLY -- the age and the pin id are
    stripped, because both change while the claim does not."""
    return re.sub(r"^\S+\s+(?:\d+|\?)d\s+", "", rest).strip()


def current_claims():
    """(owner, pin_id8, claim_key) for every line the lane would flag today."""
    out = subprocess.run([sys.executable, str(HERE / "pins.py")],
                         capture_output=True, text=True, timeout=2400)
    if out.returncode != 0:
        raise SystemExit(f"⛔ pins.py failed: {out.stderr[-500:]}")
    rows, kind = [], None
    for line in out.stdout.splitlines():
        if line.startswith("--- "):
            kind = line[4:].split("  (")[0].strip()
            continue
        if kind != "unanchored-pin-claim" or not line.startswith("  ") or line.startswith("   "):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append((parts[0], parts[1], claim_key(" ".join(parts[1:]))))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-decls")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    try:
        ex = json.loads(STATE.read_text())
    except Exception:
        ex = {}

    if a.list:
        for owner, items in sorted(ex.items()):
            for i in items:
                print(f"{owner:<24} {i.get('who','?'):<10} {i.get('at','?')}  {i['claim'][:80]}")
        print(f"\n{sum(len(v) for v in ex.values())} exemption(s) across {len(ex)} owner(s)")
        return 0

    decls = json.loads(Path(a.from_decls).read_text())
    rows = current_claims()
    today = date.today().isoformat()
    added, missing = 0, []
    for d in decls:
        owner, pin, match = d["owner"], d.get("pin", ""), d["match"]
        hits = [r for r in rows if r[0] == owner
                and (not pin or r[1].startswith(pin[:8]))
                and match.lower() in r[2].lower()]
        if not hits:
            # ⭐ NOT AN ERROR AND WORTH SAYING: the seat may have REWRITTEN or
            #   deleted the line instead, in which case there is nothing to
            #   exempt and the lane already stopped flagging it. Silence here
            #   would hide a typo in the declaration, which looks identical.
            missing.append(f"{owner}/{pin[:8]}: no live claim matches {match!r}")
            continue
        for _, _, key in hits:
            lst = ex.setdefault(owner, [])
            if any(e["claim"] == key for e in lst):
                continue
            lst.append({"claim": key, "who": d.get("who", owner), "at": today,
                        "note": d.get("note", "")})
            added += 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(ex, indent=1, sort_keys=True))
    print(f"recorded {added} exemption(s); {sum(len(v) for v in ex.values())} total")
    for m in missing:
        print(f"  ⚠️ {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
