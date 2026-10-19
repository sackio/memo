#!/usr/bin/env python3
"""Diff a snapshot directory against the live corpus.

⛔ A TIMEOUT IS NOT A 404 AND A 404 IS NOT A DELETION YOU CAUSED. An earlier
version of this check reported `HTTP 000` (connect timeout, host at load 146) as
"DELETED" for two memos that were untouched. Three outcomes, three names:
  200 -> compare · 404 -> gone · anything else/none -> UNKNOWN, say so.
It reads the whole corpus ONCE instead of one request per memo, because 24
sequential curls against a loaded host is what produced the timeouts.
"""
import json, sys, urllib.request
from pathlib import Path

snap = Path(sys.argv[1] if len(sys.argv) > 1 else "stage2-before")
try:
    docs = json.load(urllib.request.urlopen(
        "http://127.0.0.1:8000/documents?limit=20000", timeout=180))
except Exception as e:                                    # noqa: BLE001
    print(f"⛔ could not read the corpus ({type(e).__name__}: {e}) — "
          f"this is UNKNOWN, not 'everything deleted'")
    sys.exit(2)

live = {d["id"]: d for d in docs}
rows, unknown = [], []
for f in sorted(snap.glob("*.json")):
    b = json.loads(f.read_text())
    bid = b.get("id")
    if not bid:
        unknown.append((f.stem, "snapshot has no id"))
        continue
    d = live.get(bid)
    bc = len(b.get("content") or "")
    if d is None:
        rows.append((bid[:8], bc, None, "GONE (deleted or absent from listing)"))
        continue
    ac = len(d.get("content") or "")
    flags = []
    if (d.get("title") or "") != (b.get("title") or ""):
        flags.append("TITLE")
    if d.get("valid_until") is not None and b.get("valid_until") is None:
        flags.append("RETIRED")
    if ac != bc:
        flags.append(f"{ac-bc:+d}ch")
    rows.append((bid[:8], bc, ac, " ".join(flags) or "unchanged"))

print(f"snapshot {snap} · {len(rows)} memos · corpus {len(docs)} docs\n")
for i, bc, ac, note in rows:
    print(f"  {i}  {bc:>6} -> {str(ac):>6}  {note}")
changed = [r for r in rows if r[3] != "unchanged"]
print(f"\n  changed: {len(changed)}/{len(rows)} · unchanged: {len(rows)-len(changed)}")
if unknown:
    print(f"  ⚠️ UNKNOWN (not a negative): {unknown}")
