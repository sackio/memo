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


def _sync_grep(path: Path, pattern: str, max_matches: int) -> list[dict]:
    """Run `command grep -n -m<max>` and parse `line:text` output."""
    import subprocess

    grep = shutil.which("grep") or "/bin/grep"
    # argv form, so no shell is involved and therefore no shim to bypass —
    # `command grep` is the equivalent discipline when a shell IS involved.
    proc = subprocess.run(
        [grep, "-n", "-a", "-E", f"-m{max_matches}", "--", pattern, str(path)],
        capture_output=True, text=True, timeout=30,
    )
    # grep exits 1 on "no matches", which is not an error here.
    if proc.returncode not in (0, 1):
        raise LogQueryError(f"grep failed ({proc.returncode}): {proc.stderr[:200]}")

    out: list[dict] = []
    for line in proc.stdout.splitlines():
        num, _, text = line.partition(":")
        if not num.isdigit():
            continue
        out.append({"line": int(num), "text": text})
    return out


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

    hits = await asyncio.to_thread(_sync_grep, path, pattern, capped)
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
        "truncated": len(matches) >= capped,
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
