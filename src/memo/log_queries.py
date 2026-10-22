"""Claude Code transcript log queries. [001/FR-033]

Given `(host, project, session_uuid, query?)`, returns matched line ranges with
enough metadata to build a `claude_log_ref` provenance block — which is the
point: this is how a memo gets to cite the exact conversation lines it came
from, rather than "someone said this once".

Transcripts are JSONL at `~/.claude/projects/<mangled-cwd>/<uuid>.jsonl` and run
to tens of MB. Three rules follow from that, and all three are safety rather
than style:

* **`command grep`, never bare `grep`.** The shell snapshot on this fleet shims
  `grep`/`rg`/`find` to a ugrep tree-scanner that, on a bounded-alternation
  pattern, can walk all of `$HOME` even when handed one small file. That took
  the office host down for an hour on 2026-07-22 at 17 GB RSS. `command grep`
  bypasses the shim.
* **Always `-m <max>`.** An unbounded match count on a 40 MB transcript returns
  a result set nobody can use and that we would then have to hold in memory.
  Since 2026-10-15 the bound sits on the SECOND grep of a pipeline, so that it
  counts conversation rather than Claude Code's API-request records — see
  `EXCLUDED_RECORD_TYPES`. The first grep is unbounded but never accumulates:
  it is SIGPIPEd as soon as the second has enough.
* **Never read a whole transcript.** Only matched line ranges are returned.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path.home() / ".claude" / "projects"

DEFAULT_MAX_MATCHES = 20
HARD_MAX_MATCHES = 200
CONTEXT_LINES = 2
# Refuse to run a pattern this long: bounded-alternation regexes are what make
# the shimmed scanner pathological, and a giant pattern is the usual shape.
MAX_PATTERN_LEN = 200

# ⛔ Claude Code 2.1.300 writes three new record types into EVERY transcript:
# `api-request-shape`, `api-request-blob` and `api-request`. They carry each API
# request's system prompt, tool schemas and parameters — CLAUDE.md, the skill
# listing and the tool list, byte-identical across every session on the fleet.
# Measured by `guide` on server3, 2026-10-15: ~126 KB on the first request
# (blob 87 KB + shape 39 KB), then ~2 KB per request; the blobs are deduped, so
# it is one-off per session rather than per turn.
#
# They are not conversation. A provenance citation that lands on one cites the
# prompt rather than anything anybody said, and on a common word they can
# outnumber the real matches, eating the `-m` budget before grep reaches a line
# somebody typed.
#
# ⭐ This is the only transcript reader in the repo that is NOT type-gated:
# `scripts/transcript-search` (classify), `scripts/transcript-passages` and
# `skills/memo-minder/bin/capture_miss_scan.py` all allow-list user/assistant
# and drop these for free. A raw grep has no such gate, so it is explicit here.
EXCLUDED_RECORD_TYPES = ("api-request-shape", "api-request-blob", "api-request")

# How far into a line to look for its `"type"` key. The records above put it in
# the first few dozen bytes; 400 is slack, and reading further would mean
# holding an 87 KB blob per hit. ⚠️ If a future record buries its type past
# this, the line is KEPT — the miss degrades to today's behaviour and can never
# drop a real match, which is the direction this has to fail in.
_TYPE_SCAN_CHARS = 400

# The exclusion, as grep sees it. It is applied as a SECOND grep in a pipeline
# rather than as a filter in Python, because `-m` has to count the lines a
# caller wanted: a session holds one `api-request` record per request, so on a
# pattern that appears in the system prompt they fill the match window entirely
# and the conversation is never reached. Measured on a synthetic 200-request
# transcript: a 5x over-fetch still returned 0 real matches out of 20.
#
# ⚠️ `.{0,%d}` bounds it to the head of the line, so a message that merely
# QUOTES one of these type strings — this module's own session does — is not
# dropped. `type` is not always the first key (it sits at index 3-4 on
# user/assistant records), so the prefix cannot be anchored tighter than this.
_EXCLUDE_RE = r'^.{0,%d}"type": ?"(%s)"' % (
    _TYPE_SCAN_CHARS, "|".join(EXCLUDED_RECORD_TYPES))


class LogQueryError(Exception):
    """Bad request — unresolvable transcript or unsafe pattern."""


def mangle_cwd(project_dir: str) -> str:
    """Claude Code's transcript directory name for a cwd: `/` -> `-`."""
    return str(project_dir).replace("/", "-")


def transcript_path(project_dir: str, session_uuid: str,
                    projects_dir: Path | None = None) -> Path:
    base = projects_dir or PROJECTS_DIR
    return base / mangle_cwd(project_dir) / f"{session_uuid}.jsonl"


def _validate(pattern: str) -> None:
    if not pattern or not pattern.strip():
        raise LogQueryError("empty pattern")
    if len(pattern) > MAX_PATTERN_LEN:
        raise LogQueryError(
            f"pattern too long ({len(pattern)} > {MAX_PATTERN_LEN}) — long "
            "bounded-alternation patterns are what make the scanner pathological"
        )
    try:
        re.compile(pattern)
    except re.error as e:
        raise LogQueryError(f"invalid regex: {e}") from e


def _excluded_record_type(text: str) -> str | None:
    """The API-request type this line is, or None if it is not one of them.

    Read off the head of the line rather than by parsing it: the blob record is
    87 KB and there is no reason to decode it to learn that we are throwing it
    away. One function, so the test and the label it prints cannot disagree.
    """
    head = text[:_TYPE_SCAN_CHARS]
    for t in EXCLUDED_RECORD_TYPES:
        if f'"type":"{t}"' in head or f'"type": "{t}"' in head:
            return t
    return None


def _sync_grep(path: Path, pattern: str, max_matches: int) -> dict:
    """Match `pattern` in the transcript, minus the API-request records.

    Two greps in a pipeline, wired argv-to-argv so no shell — and therefore no
    shim — is involved. The first is unbounded but never accumulates: the
    second carries `-m`, and when it has enough it exits and SIGPIPEs the
    first. So the bound still holds and nothing large is ever held in memory,
    while `-m` counts only lines a caller would want.
    """
    import subprocess

    grep = shutil.which("grep") or "/bin/grep"
    want = max_matches + 1          # one extra, purely to detect "there is more"
    p1 = subprocess.Popen(
        [grep, "-n", "-a", "-E", "--", pattern, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(
        [grep, "-v", "-a", "-E", f"-m{want}", "--", _EXCLUDE_RE],
        stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p1.stdout.close()               # so p1 actually receives the SIGPIPE
    try:
        stdout, stderr2 = p2.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p2.kill(); p1.kill()
        raise LogQueryError(f"grep timed out after 30s on {path}")
    # `communicate`, not `wait`: p1's stderr is a pipe nobody has drained, and a
    # process blocked writing to a full pipe never exits — `wait` would hang on
    # it rather than report anything.
    try:
        _, err1 = p1.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        p1.kill()
        _, err1 = p1.communicate()
    # 0 = matched, 1 = no matches, 141/-13 = SIGPIPE (p1 stopped because p2 had
    # enough). None of those is an error here; anything else is.
    if p1.returncode not in (0, 1, 141, -13):
        raise LogQueryError(
            f"grep failed ({p1.returncode}): {(err1 or b'')[:200]!r}")
    if p2.returncode not in (0, 1):
        raise LogQueryError(f"grep failed ({p2.returncode}): {stderr2[:200]}")

    out: list[dict] = []
    excluded = 0
    for line in stdout.splitlines():
        num, _, text = line.partition(":")
        if not num.isdigit():
            continue
        # Second net, behind the grep one. Same window, same strings, so it
        # should never fire — and if it does, the warning below says the two
        # have drifted rather than letting the budget quietly leak again.
        if _excluded_record_type(text):
            excluded += 1
            continue
        # Truncate here, not at the caller: an unsliced blob line is 87 KB and
        # the response only ever shows the first 500 characters of it.
        out.append({"line": int(num), "text": text[:4000]})
    if excluded:
        # The two nets are meant to agree by construction, so this is drift —
        # the regex let through what `_excluded_record_type` caught. Not fatal,
        # but it means `-m` is being spent on machinery again.
        logger.warning("log_query: %d api-request record(s) passed the grep "
                       "exclusion and were dropped in Python — _EXCLUDE_RE and "
                       "_excluded_record_type have drifted", excluded)
    return {"hits": out, "excluded": excluded,
            "more_available": len(out) > max_matches}


def _sync_extract(path: Path, line_no: int, context: int) -> dict:
    """Pull a small window around a matched line, with role/timestamp if parseable."""
    lo, hi = max(1, line_no - context), line_no + context
    lines: list[str] = []
    role = None
    timestamp = None
    with path.open("r", errors="replace") as fh:
        for i, raw in enumerate(fh, start=1):
            if i > hi:
                break
            if i < lo:
                continue
            elided = None if i == line_no else _excluded_record_type(raw)
            if elided:
                # ⛔ An adjacent `api-request-blob` is 87 KB, and the excerpt is
                # capped at 2000 characters — so without this the context
                # window silently swallows the very line being cited. The line
                # RANGE still spans it, because that is what is on disk.
                lines.append(f"[{elided} record elided]")
            else:
                lines.append(raw.rstrip("\n"))
            if i == line_no:
                try:
                    rec = json.loads(raw)
                    role = rec.get("type") or (rec.get("message") or {}).get("role")
                    timestamp = rec.get("timestamp")
                except ValueError:
                    pass  # a transcript line need not be valid JSON
    return {"line_start": lo, "line_end": hi, "role": role,
            "timestamp": timestamp, "excerpt": "\n".join(lines)[:2000]}


async def query(*, project_dir: str, session_uuid: str, pattern: str,
                host: str | None = None, max_matches: int = DEFAULT_MAX_MATCHES,
                context: int = CONTEXT_LINES,
                projects_dir: Path | None = None) -> dict:
    """Search one transcript. Returns matches + a provenance-shaped stub. [001/FR-033]"""
    _validate(pattern)
    capped = max(1, min(int(max_matches or DEFAULT_MAX_MATCHES), HARD_MAX_MATCHES))

    path = transcript_path(project_dir, session_uuid, projects_dir)
    if not path.is_file():
        raise LogQueryError(f"no transcript at {path}")

    scan = await asyncio.to_thread(_sync_grep, path, pattern, capped)
    hits = scan["hits"][:capped]
    matches = []
    for h in hits:
        detail = await asyncio.to_thread(_sync_extract, path, h["line"], context)
        matches.append({**detail, "matched_line": h["line"], "matched_text": h["text"][:500]})

    return {
        "host": host,
        "project_dir": project_dir,
        "session_uuid": session_uuid,
        "transcript": str(path),
        "pattern": pattern,
        "match_count": len(matches),
        # ⚠️ Was `len(matches) >= capped`, which is an INFERENCE from a full
        # window and not the same claim. The second grep fetches one extra line
        # for exactly this, so the flag now reports a measurement.
        "truncated": scan["more_available"],
        "max_matches": capped,
        "matches": matches,
        # Ready to drop into a Provenance block once the caller picks a match.
        "provenance_template": {
            "claude_log_ref": {
                "host": host, "project_dir": project_dir,
                "session_uuid": session_uuid,
                "line_range_start": matches[0]["line_start"] if matches else None,
                "line_range_end": matches[0]["line_end"] if matches else None,
            }
        },
    }


async def list_sessions(project_dir: str,
                        projects_dir: Path | None = None) -> list[dict]:
    """Transcripts available for a project, newest first."""
    base = (projects_dir or PROJECTS_DIR) / mangle_cwd(project_dir)
    if not base.is_dir():
        return []
    out = []
    for p in base.glob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"session_uuid": p.stem, "path": str(p),
                    "size_bytes": st.st_size, "modified_at": st.st_mtime})
    return sorted(out, key=lambda d: d["modified_at"], reverse=True)
