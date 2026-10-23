#!/usr/bin/env python3
"""Audit the INSTRUCTION SURFACE -- the files that are loaded into sessions and
make claims -- the way the corpus audit treats memos.

⭐ WHY THIS LANE EXISTS (Ben, Slack #memo 2026-10-23 05:57):
   "we need to maintain any files that anyone is using with claims that
    a. should reference memo which we can better control,
    b. need to be reverified and maintained"

⭐⭐ THE ASYMMETRY THAT MAKES THIS WORSE THAN A STALE MEMO.
   memo is PULL: a stale memo sits there until someone asks for it.
   An instruction file is PUSH: it is spliced into EVERY session, unasked, and
   read as established fact by a seat with no memory to question it. So the blast
   radius of one stale claim is strictly larger here than in the corpus -- and
   this surface has never had an audit at all.
   Measured 2026-10-23 on server4: 104 md files, 2.2 MB, symlinked out of 10+
   repos owned by different seats.

⛔ THIS SCRIPT ONLY REPORTS. It never edits an instruction file.
   Editing CLAUDE.md or a skill file requires the operator's explicit
   authorization, every time. Findings ROUTE to the OWNING SEAT (resolved from
   the symlink target), per Ben's standing order of 2026-10-19.

⛔ DEFAULT CHECKS TOUCH NO NFS. `/mnt/nas` write latency hit 62s on 2026-10-23
   and a GETATTR storm from this script would be a self-inflicted outage. The
   default check (do memo references resolve?) reads only the shared local corpus
   cache. Path and endpoint checks are OPT-IN: --paths, --net.

⛔ A NULL HERE IS NOT A HEALTH CLAIM. "0 broken references" means the ids that
   were WRITTEN resolve; it says nothing about claims that cite no id at all,
   which are the majority and need a reader. This script finds the mechanically
   checkable subset and reports its own denominator so the rest is visible.
"""
import argparse, json, os, re, sys, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "memo-audit"))

# ⛔ Both forms, per memo-minder: 11 of 17 checkpoints carry SHORT keys, and a
#    reader that matches only the anchored form reads those as zero.
RE_FULL  = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')
RE_SHORT = re.compile(r'(?<![0-9a-f-])`([0-9a-f]{8})`')     # backticked only: bare 8-hex is far too noisy
RE_PATH  = re.compile(r'(?<![\w`])(/(?:mnt|home|opt|srv|etc|var)/[\w./+-]{3,})')
RE_NET   = re.compile(r'\b((?:https?://)?(?:[a-z0-9-]+\.)*[a-z0-9-]+:\d{2,5})(?:/[\w./-]*)?')

SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "venv",
            # ⛔ `projects/*/memory/` is the RETIRED file-based store. CLAUDE.md:
            #    "Knowledge goes in memo -- nothing else is read ... never a
            #    memory/ directory". Those files are not loaded into any session,
            #    so they are not part of the surface this audit is about. Scanning
            #    them inflated a first run from 104 files to 637 and produced
            #    hundreds of "dangling refs" that were `originSessionId` frontmatter.
            "memory", "cache", "todos", "statsig", "shell-snapshots"}

# ⭐⭐ PRECISION GATE. A bare 32-hex uuid is NOT evidence of a memo reference: this
#    fleet writes SESSION ids, beacon ids, container ids and image digests in the
#    same shape, and skill files deliberately quote BAD ids as counter-examples.
#    A first run without this gate called 594 of 795 refs dangling (75%) -- and a
#    reference checker that fails three quarters of the time is measuring the
#    wrong thing, not finding a catastrophe.
#    ⇒ Require a memo cue near the ref, and veto the known non-memo contexts.
CUE   = re.compile(r'memo|/recall|/memorize|corpus|/documents', re.I)
VETO  = re.compile(r'session|transcript|\.jsonl|originSessionId|beacon|'
                   r'e\.g\.|example|placeholder|captures|WRONG|short-form|'
                   r'sha256|image|digest|container', re.I)
# an id that is visibly fabricated: ascending nibbles, or <4 distinct characters
def _placeholder(h: str) -> bool:
    return h in ("a1b2c3d4", "deadbeef", "12345678") or len(set(h)) < 4


def is_memo_ref(text: str, start: int, end: int, ident: str) -> bool:
    """Does this hex string actually CLAIM to be a memo id?"""
    if _placeholder(ident[:8]):
        return False
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    line = text[ls: le if le != -1 else len(text)]
    if VETO.search(line):
        return False
    # cue on the line, or in the ~120 chars before it (a list under a memo heading)
    return bool(CUE.search(line) or CUE.search(text[max(0, ls - 120):ls]))


def owner_of(p: Path) -> str:
    """Which repo really owns this file -- resolved THROUGH the symlink.

    ⭐ A finding reported against `~/.claude/skills/x` is unactionable: that is a
       symlink, and editing it edits another seat's repo. The owner is the route.
    """
    try:
        real = p.resolve()
    except OSError:
        return "<unresolvable>"
    s = str(real)
    m = re.match(r'(/mnt/nas/data/code/[^/]+)', s)
    if m:
        return m.group(1).split("/")[-1]
    m = re.match(r'(/mnt/nas/data/[^/]+)', s)
    if m:
        return "nas:" + m.group(1).split("/")[-1]
    return "host-local:" + str(real.parent).replace(os.path.expanduser("~"), "~")


def collect(roots, maxdepth=3):
    out = []
    for root in roots:
        root = Path(os.path.expanduser(root))
        if not root.exists():
            continue
        if root.is_file():
            out.append(root); continue
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            d = Path(dirpath)
            if len(d.parts) - base >= maxdepth:
                dirnames[:] = []
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIR]
            for fn in filenames:
                if fn.endswith(".md"):
                    out.append(d / fn)
    # dedupe on REAL path: ~/.claude/skills/x and the repo file are one file
    seen, uniq = set(), []
    for p in out:
        try: rp = p.resolve()
        except OSError: rp = p
        if rp in seen: continue
        seen.add(rp); uniq.append(p)
    return uniq


def live_ids():
    """Every id in the corpus, plus a first-8 prefix index. Shared cache only."""
    import corpus
    docs = corpus.load()
    full = {d["id"] for d in docs if d.get("id")}
    pref = {}
    for i in full:
        pref.setdefault(i[:8], []).append(i)
    return full, pref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="*", default=["~/.claude"])
    ap.add_argument("--paths", action="store_true", help="also stat absolute paths (TOUCHES NFS)")
    ap.add_argument("--net", action="store_true", help="also probe host:port (makes connections)")
    ap.add_argument("--json", metavar="FILE")
    a = ap.parse_args()

    files = collect(a.roots or ["~/.claude"])
    full, pref = live_ids()
    print(f"corpus: {len(full)} live ids", file=sys.stderr)

    findings, stats = [], {"files": len(files), "refs": 0, "skipped": 0, "paths": 0, "nets": 0}

    for p in files:
        try:
            text = p.read_text(errors="replace")
        except OSError as e:
            findings.append({"file": str(p), "owner": owner_of(p), "kind": "unreadable",
                             "detail": str(e)})
            continue
        owner = owner_of(p)
        for m in RE_FULL.finditer(text):
            if not is_memo_ref(text, m.start(), m.end(), m.group(0)):
                stats["skipped"] += 1
                continue
            stats["refs"] += 1
            if m.group(0) not in full:
                findings.append({"file": str(p), "owner": owner, "kind": "dangling-memo-ref",
                                 "ref": m.group(0), "line": text[:m.start()].count("\n") + 1})
        for m in RE_SHORT.finditer(text):
            if not is_memo_ref(text, m.start(), m.end(), m.group(1)):
                stats["skipped"] += 1
                continue
            stats["refs"] += 1
            if m.group(1) not in pref:
                findings.append({"file": str(p), "owner": owner, "kind": "dangling-memo-ref",
                                 "ref": m.group(1) + " (short)",
                                 "line": text[:m.start()].count("\n") + 1})
        if a.paths:
            for m in set(RE_PATH.findall(text)):
                stats["paths"] += 1
                if not os.path.exists(m.rstrip(".,;:)")):
                    findings.append({"file": str(p), "owner": owner, "kind": "missing-path",
                                     "ref": m})
        if a.net:
            import socket
            for m in set(RE_NET.findall(text)):
                stats["nets"] += 1
                hp = m.split("//")[-1]
                host, _, port = hp.rpartition(":")
                try:
                    socket.create_connection((host, int(port)), timeout=3).close()
                except Exception:
                    findings.append({"file": str(p), "owner": owner, "kind": "dead-endpoint",
                                     "ref": m})

    by_owner = {}
    for f in findings:
        by_owner.setdefault(f["owner"], []).append(f)

    print(f"\n=== instruction-surface audit ===")
    print(f"files scanned      : {stats['files']}")
    print(f"memo refs checked  : {stats['refs']}")
    print(f"hex strings vetoed : {stats['skipped']}  (session/beacon ids, examples, counter-examples)")
    if a.paths: print(f"paths checked      : {stats['paths']}")
    if a.net:   print(f"endpoints checked  : {stats['nets']}")
    print(f"findings           : {len(findings)}  across {len(by_owner)} owners\n")
    for owner in sorted(by_owner, key=lambda o: -len(by_owner[o])):
        print(f"--- {owner}  ({len(by_owner[owner])}) ---")
        for f in by_owner[owner][:40]:
            loc = f"{Path(f['file']).name}:{f.get('line','?')}"
            print(f"  {f['kind']:<18} {loc:<34} {f.get('ref', f.get('detail',''))}")
    if not findings:
        print("no mechanically-checkable defect found.")
    print("\n⛔ A clean run here is NOT 'the instruction files are correct'. It means the")
    print("   ids that were WRITTEN resolve. Claims citing no id are the majority and")
    print("   are invisible to this script -- they need a reader, which is the audit worker.")

    if a.json:
        Path(a.json).write_text(json.dumps({"stats": stats, "findings": findings}, indent=1))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
