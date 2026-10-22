#!/usr/bin/env python3
"""One cached copy of the corpus, shared by every script and worker.

⛔⛔ `GET /documents?limit=20000` IS A ~50 MB FULL-TABLE READ, AND THE WORKER
BRIEF TELLS EVERY WORKER TO DO IT. With 4-6 workers running concurrently, each
scanning the corpus to count citers, the memo container read **1.68 TB
cumulative and 494 MB/s at peak** off server4's ROOT disk -- measured by the
`system` seat 2026-10-22, during a RAID rebuild, on the single point of failure
for the whole fleet. `/documents` has no field selection (params: query, tags,
after, before, min_tokens, max_tokens, min_score, limit, db_path), so there is
no way to ask for less; the only lever is asking less OFTEN.

⇒ Fetch once per TTL into a local file and let everyone read that. The cache
lives on LOCAL disk, never /mnt/nas: the point is to stop hammering storage,
and an NFS round trip per read would move the problem rather than fix it.

⚠️ A CACHE IS A STALENESS BUG WAITING TO BE WRITTEN. Callers that must see the
current state -- a read-back after a write, an existence check before a delete
-- pass `fresh=True` or call the API directly. The TTL is short for that reason,
and every consumer gets the cache's AGE alongside the data so it can decide
rather than assume.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "http://server4:8000"
CACHE = Path("/tmp/memo-corpus-cache.json")      # ⛔ local disk, never /mnt/nas
TTL = 600                                        # seconds


def age() -> float | None:
    try:
        return time.time() - CACHE.stat().st_mtime
    except OSError:
        return None


def fetch(api: str = API) -> list[dict]:
    with urllib.request.urlopen(f"{api}/documents?limit=20000", timeout=300) as r:
        docs = json.loads(r.read().decode())
    if not isinstance(docs, list):
        docs = docs.get("documents") or docs.get("items") or []
    return docs


def path(ttl: int = TTL, fresh: bool = False, api: str = API) -> Path:
    """The cache file, refreshed if older than `ttl`. Returns its path."""
    a = age()
    if fresh or a is None or a > ttl:
        docs = fetch(api)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(docs))
        os.replace(tmp, CACHE)                   # atomic: no torn read
        try:
            os.chmod(CACHE, 0o666)               # several seats share this host
        except OSError:
            pass
    return CACHE


def load(ttl: int = TTL, fresh: bool = False, api: str = API) -> list[dict]:
    return json.loads(path(ttl, fresh, api).read_text())


if __name__ == "__main__":
    args = sys.argv[1:]
    fresh = "--fresh" in args
    p = path(fresh=fresh)
    if "--path" in args or not args or args == ["--fresh"]:
        print(p)                                 # for shell and worker use
        print(f"# {len(json.loads(p.read_text()))} documents, "
              f"cache age {age():.0f}s, TTL {TTL}s", file=sys.stderr)
