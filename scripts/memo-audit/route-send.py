#!/usr/bin/env python3
"""Harvest `route[]` from a run's batch records and DM each owning seat — once.

Ben, Slack #memo 2026-10-19 12:50 EDT: *"when you find things like this you
should route it to the appropriate agent."* Auditing memos against the live
fleet finds things wrong with THE FLEET, and recording that in a memo and
telling nobody is the failure mode.

⛔⛔ DE-DUPLICATE ACROSS RUNS, OR THE AUDIT BECOMES A NIGHTLY NAG. A finding a
seat has already triaged does not stop being true, so the next run's worker
finds it again and routes it again. `finance` asked for exactly this on
2026-10-21 00:50 — "pending Ben as ask a43, do not re-report tomorrow" — and
they are right: re-sending a finding whose owner has already acted on it costs
them a read, costs me a write, and trains every seat to skim these.
⭐ Same shape the ASK-id mechanism exists to prevent: three green bars for one
photo request in 49 minutes, because restating minted a new question each time.

⛔ THE LEDGER IS APPEND-ONLY AND FINGERPRINTED ON (seat, finding text), NOT on
the memo it came from. The same defect found while auditing a different memo is
the same defect. ⚠️ But the fingerprint is exact-text, so a reworded finding
WILL re-send — that is deliberate. Fuzzy matching here would silently swallow a
genuinely new finding that happens to read like an old one, and a dropped
finding is invisible while a duplicate is merely annoying.

⚠️ A SEAT CORRECTING A ROUTED FINDING IS THE CONTROL, NOT NOISE. Measured: seats
have corrected relayed claims 10 times in ~5 days and were right every time.
Record the correction against the fingerprint so the next run does not re-assert
what its owner disproved.
"""
from __future__ import annotations

import argparse, hashlib, json, re, time, urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "routes-sent-ledger.json"
BROKER = "http://192.168.1.168:3030/messages"


def fingerprint(seat: str, finding: str) -> str:
    norm = " ".join((finding or "").split()).lower()
    return hashlib.sha256(f"{seat}\x00{norm}".encode()).hexdigest()[:16]


def harvest(rundir: Path) -> dict[str, list[dict]]:
    routes: dict[str, list[dict]] = defaultdict(list)
    # ⛔ MATCH THE RECORD NAME EXACTLY. `batch-*.json` also catches
    # `batch-0000.raw.json` and any `batch-0000.report.json` written by hand
    # during a recovery, and each one re-enters the loop as a batch that has no
    # report — producing warnings about files that were never batches and, with
    # a fuzzier ledger, duplicate sends.
    rec_name = re.compile(r"^batch-\d+\.json$")
    for f in sorted(rundir.glob("batch-*.json")):
        if not rec_name.match(f.name):
            continue
        d = json.loads(f.read_text())
        # `report` is the field run.py stores, recovering it from the session
        # transcript when the worker's last turn did not carry it. Falling back
        # to scraping `result` keeps this working on records written before
        # that fix, where the report is only in the text.
        rep = d.get("report")
        if rep is None:
            for blob in reversed(re.findall(r"```json\s*(.*?)```", d.get("result") or "", re.S)):
                try:
                    cand = json.loads(blob)
                except ValueError:
                    continue
                if isinstance(cand, dict) and "audited" in cand:
                    rep = cand
                    break
        if rep is None:
            print(f"  ⚠️ {f.name}: no report — its findings are NOT in this send")
            continue
        for e in (rep.get("route") or []):
            routes[(e.get("seat") or "?").strip()].append(e)
    return routes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    routes = harvest(Path(a.rundir))

    sent, skipped = {}, 0
    for seat, entries in sorted(routes.items()):
        fresh = []
        for e in entries:
            fp = fingerprint(seat, e.get("finding") or "")
            if fp in ledger:
                skipped += 1
                continue
            fresh.append((fp, e))
        if not fresh:
            continue
        lines = []
        for _, e in fresh:
            src = (e.get("from_memo") or "")[:8]
            lines.append(f"- {(e.get('finding') or '').strip()}"
                         + (f"\n  (found auditing memo {src})" if src else ""))
        body = (f"memo-audit {time.strftime('%Y-%m-%d')} found the following in your domain "
                f"while checking memos against the live fleet. {len(fresh)} item(s); each "
                "carries the evidence the worker ran. Not a request for a reply — but if one "
                "is wrong or already handled, say so and it will not be re-sent.\n\n"
                + "\n".join(lines))
        if a.dry_run:
            print(f"[dry-run] {seat}: {len(fresh)} new ({len(entries) - len(fresh)} already sent)")
            continue
        payload = json.dumps({"to": seat, "from": "memo",
                              "subject": f"memo-audit: {len(fresh)} finding(s) in your domain",
                              "content": body}).encode()
        req = urllib.request.Request(BROKER, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=30))
            ok = r.get("sent") == 1
        except Exception as ex:
            print(f"  ⛔ {seat}: {str(ex)[:90]}")
            continue
        # ⛔ Record ONLY after a confirmed send. Writing the ledger first would
        # mark a failed delivery as sent and the finding would never go again.
        if ok:
            for fp, e in fresh:
                ledger[fp] = {"seat": seat, "at": time.time(),
                              "from_memo": e.get("from_memo"),
                              "finding": (e.get("finding") or "")[:300]}
        sent[seat] = len(fresh)
        time.sleep(0.4)

    if not a.dry_run:
        LEDGER.write_text(json.dumps(ledger, indent=1))
    print(f"\nseats messaged {len(sent)} · findings sent {sum(sent.values())} "
          f"· suppressed as already-sent {skipped} · ledger {len(ledger)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
