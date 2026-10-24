#!/usr/bin/env python3
"""Clear `valid_until` on memos that are CONTINUATIONS, not replacements.

AUTHORISED by Ben, Slack #memo 2026-10-23 11:05 ("yes", answering the hoisted
ask: "52 memos are wrongly marked expired (continuations, not replacements).
I'd clear valid_until on those 52 only.").

⛔ WHY THE INTERSECTION RUNS FIRST, AND WHY IT IS NOT OPTIONAL. The last bulk
   corpus change that looked obviously right deleted 33 cross-domain memos still
   cited by live ones (`0c20c7dd`). Its verification proved the deletion was
   REVERSIBLE and said nothing about whether the SET matched the SCOPE. The check
   that was missing, and that runs here: intersect the change set with what
   depends on it, and look at the overlap before writing.
⚠️ This change is additive (content becomes visible again, nothing is hidden or
   deleted), so the overlap is informational rather than blocking -- but a seat
   whose pin cites one of these is a seat whose citation silently starts
   resolving to a live doc again, and that is worth knowing before it happens.

⛔ superseded_by IS LEFT ALONE. The lineage link is true and useful; only the
   EXPIRY is wrong. That is also exactly the scope Ben approved -- no more.
"""
import argparse, json, re, sys, urllib.request

API = "http://server4:8000"
CONT = re.compile(r"\bPART\s*\d|\bcontinues\b|\bcontinuation\b|\(continued\b|\bwhich is FULL\b", re.I)


def get(path):
    with urllib.request.urlopen(API + path, timeout=90) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    docs = get("/documents?limit=20000")
    by_id = {d["id"]: d for d in docs}
    targets = []
    for d in docs:
        if not d.get("superseded_by") or d.get("valid_until") is None:
            continue
        heir = by_id.get(d["superseded_by"])
        if heir is None:
            continue
        blob = (heir.get("title") or "") + " " + (heir.get("content") or "")[:400]
        if CONT.search(blob) or CONT.search(d.get("title") or ""):
            targets.append(d)
    ids8 = {d["id"][:8] for d in targets}
    print(f"continuations marked expired: {len(targets)}")

    # ── the scope check the 2026-09-06 purge lacked ────────────────────────
    citers = {}
    for d in docs:
        if d["id"] in {t["id"] for t in targets}:
            continue
        blob = (d.get("title") or "") + (d.get("content") or "")
        for t in targets:
            if t["id"][:8] in blob:
                citers.setdefault(t["id"][:8], []).append(d["id"][:8])
    print(f"of those, cited by at least one OTHER live memo: {len(citers)}")

    pinned = set()
    try:
        bro = "http://192.168.1.168:3030"
        with urllib.request.urlopen(bro + "/reminders-owners", timeout=30) as r:
            owners = [o["owner"] for o in json.loads(r.read())["owners"] if o.get("owner")]
        for o in owners:
            with urllib.request.urlopen(f"{bro}/reminders/{o}", timeout=30) as r:
                for rec in (json.loads(r.read()).get("reminders") or []):
                    if rec.get("inherited") or (rec.get("state") or "") == "retired":
                        continue
                    blob = (rec.get("body") or "") + " ".join(str(x) for x in (rec.get("memo_refs") or []))
                    for i in ids8:
                        if i in blob:
                            pinned.add((o, i))
    except Exception as e:                                   # noqa: BLE001
        print(f"⚠️ pin intersection INCOMPLETE ({e}) -- not a zero", file=sys.stderr)
    print(f"of those, cited by at least one live PIN: {len(pinned)}"
          f"{' -> ' + str(sorted(pinned)[:6]) if pinned else ''}")

    if not a.apply:
        print("\n(dry run -- nothing written. Re-run with --apply)")
        for d in targets[:10]:
            print(f"  {d['id'][:8]} -> {d['superseded_by'][:8]}  {(d.get('title') or '')[:78]}")
        return 0

    ok = fail = 0
    for d in targets:
        req = urllib.request.Request(f"{API}/documents/{d['id']}",
                                     data=json.dumps({"valid_until": None}).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
        except Exception as e:                               # noqa: BLE001
            print(f"  ⛔ PATCH failed {d['id'][:8]}: {e}")
            fail += 1
            continue
        # ⛔ READ BACK. A 200 on this API has returned success for a phantom
        #    field three times (`add_tags`, `expect_content` over HTTP,
        #    `valid_until` itself). The write is not done until the read says so.
        back = get(f"/documents/{d['id']}")
        if back.get("valid_until") is None:
            ok += 1
        else:
            print(f"  ⛔ STILL EXPIRED after a 200: {d['id'][:8]} -> {back.get('valid_until')}")
            fail += 1
    print(f"\ncleared: {ok}   failed: {fail}   of {len(targets)}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
