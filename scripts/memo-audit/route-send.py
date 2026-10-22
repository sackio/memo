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
SUBSCRIBERS = "http://192.168.1.168:3030/subscribers"
# Workers name the seat they BELIEVE owns a finding, and they guess. Ben's
# routing instruction says to name a best guess, so a wrong name is expected
# and must be caught here rather than sent.
SEAT_ALIASES = {
    # mcp_polygon lives in the mcp_massive fork; `polygon` is not a seat.
    "polygon": "massive",
}


def known_seats() -> set[str]:
    """⛔⛔ A DM TO AN ALMOST-RIGHT ADDRESS IS ACCEPTED, STORED, AND NEVER
    DELIVERED — there is no error to notice. Measured 2026-10-21: a worker
    routed a finding to `polygon`, which is not among the 193 registered
    subscribers; the send would have reported success and reached nobody.
    ⚠️ Presence here is not reachability either, but absence IS undeliverability,
    and that is the half worth refusing on."""
    with urllib.request.urlopen(SUBSCRIBERS, timeout=30) as r:
        d = json.load(r)
    rows = d if isinstance(d, list) else (d.get("subscribers") or d.get("sessions") or [])
    if rows and isinstance(rows[0], dict):
        return {(x.get("id") or x.get("subscriber_id") or x.get("name")) for x in rows}
    return set(rows)


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


# ⛔⛔ THE LEDGER CONFLATES "RESOLVED" WITH "STILL OPEN AND DELIBERATELY UNRESOLVED",
#    AND A SEAT TOLD US SO. Suppressing a re-send assumes the owner acted. Some
#    findings are the opposite: the owner AGREES, cannot act unilaterally, and needs
#    the reminder to keep arriving until the condition clears.
#    `ha`, 2026-10-22, on the HVAC hold watchdog: "DO NOT mark this handled and DO NOT
#    stop re-sending it ... Keep re-sending until the condition no longer references
#    `hold_is_manual`. If I go quiet on it, that is a failure, not a resolution."
#    ⇒ Under the old logic that finding would have been sent exactly once and then
#    silently suppressed forever, and the silence would have read as a fix.
#
# ⭐ THE ENTRY MUST CARRY ITS OWN RETIREMENT CONDITION, not a date. A date expires
#    whether or not the reason did; `clears_when` is checkable by whoever reads it.
# ⚠️ Keep this list SHORT and owner-requested. It defeats de-duplication on purpose,
#    so an entry nobody asked for is just a seat being spammed every run.
RESEND_FORCED = [
    {"seat": "ha",
     "match": "hold_is_manual",
     "clears_when": "the watchdog condition no longer references `hold_is_manual` "
                    "(ha, 2026-10-22; needs Ben's ruling — changes HVAC behaviour, "
                    "and the attribute is under a symmetric authorship ban, "
                    "memos 6e972137 + 52afcba8)"},
]


def resend_forced(seat: str, finding: str) -> bool:
    """True if this finding must bypass ledger suppression and be re-sent."""
    f = (finding or "").lower()
    return any(r["seat"] == seat and r["match"].lower() in f for r in RESEND_FORCED)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    routes = harvest(Path(a.rundir))

    seats = known_seats()
    for bad, good in SEAT_ALIASES.items():
        if bad in routes and good in seats:
            routes.setdefault(good, []).extend(routes.pop(bad))
            print(f"  ↪ remapped {bad} → {good} (alias)")
    # ⛔⛔ A THREAD IS A VALID TARGET AND IS *NEVER* IN THE SUBSCRIBER REGISTRY.
    #    A conversation has conv_id `<seat>.<label>` but REGISTERS under its wire
    #    (`thread-<seat>-<n>`), so `ha.led` is absent from GET /subscribers while
    #    being perfectly deliverable — the broker resolves the conv_id on send.
    #    Validating against the registry alone therefore DROPS a good address and
    #    prints "no such subscriber", which is the confident-wrong direction.
    #    MEASURED 2026-10-22: `ha` main asked for three walkway/WLED findings to be
    #    re-sent to `ha.led` because that thread owns them. Sent by hand, it
    #    resolved to wire `thread-ha-2`, delivered live. Under the old check the
    #    worker could never have routed there at all.
    # ⭐ So: accept `<seat>.<label>` when <seat> itself is a known subscriber. That
    #    keeps the typo protection (an unknown PARENT is still refused) without
    #    pretending the registry is the set of all addresses. Presence in the
    #    registry proves reachability; ABSENCE FROM IT PROVES NOTHING.
    def _addressable(target: str) -> bool:
        if target in seats:
            return True
        parent, sep, label = target.partition(".")
        return bool(sep and label and parent in seats)

    unknown = {s_ for s_ in routes if not _addressable(s_)}
    for s_ in sorted(unknown):
        print(f"  ⛔ UNKNOWN SEAT {s_!r} — {len(routes[s_])} finding(s) NOT sent, "
              f"no such subscriber. Reroute by hand or add an alias:")
        for e in routes[s_]:
            print(f"      {(e.get('finding') or '')[:110]}")
        routes.pop(s_)

    sent, skipped = {}, 0
    for seat, entries in sorted(routes.items()):
        fresh = []
        for e in entries:
            fp = fingerprint(seat, e.get("finding") or "")
            if fp in ledger and not resend_forced(seat, e.get("finding") or ""):
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
