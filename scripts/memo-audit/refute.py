#!/usr/bin/env python3
"""REFUTE probes — the one cheap observation that would disprove a memo.

⭐ WHY THIS EXISTS. A paid audit ($0.41/memo, measured 2026-10-22) spends most of
its money re-establishing a claim from scratch. While it verifies, it necessarily
works out the DISCRIMINATOR — the single thing that, if it came back the other
way, would settle the memo as wrong. That is the expensive part, and nobody kept
it. Stored on the memo, a cron reruns it for the cost of one HTTP request and
only a REFUTED probe escalates to a paid audit.

⛔⛔ A PROBE IS NOT A SHELL COMMAND, AND THAT IS DELIBERATE. These are authored by
an LLM reading a memo, and they run unattended, on a host that owns the fleet. So
a probe is a typed record with a closed verb set, an allowlist of read-only
binaries, no shell, no pipes, no redirection, no writes, and a hard timeout.
⇒ Anything the worker cannot express this way it writes as prose for a human; the
cron skips it rather than guessing. A probe that cannot be expressed safely is a
probe we do not run.

⭐⭐ THREE OUTCOMES, NEVER TWO. `holds` · `refuted` · `inconclusive`.
A probe that could not run — host down, NFS stalled, binary missing, timeout —
is INCONCLUSIVE. It is not a pass and it is not a refutation. Collapsing it into
either is the exact failure this fleet keeps paying for: an unreachable host read
as a clean result, a timeout counted as a zero. The runner reports the three
separately and never lets the count of `holds` absorb a probe that did not run.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROBE_RE = re.compile(r"<!--\s*refute v1\s+(\{.*?\})\s*-->", re.S)
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 120
MAX_READ = 2_000_000          # bytes we will pull from a file or a URL

# ── what may run ──────────────────────────────────────────────────────────────
# argv[0] -> None means every invocation is read-only; a set means argv[1] must
# be in it. ⛔ Add nothing here that can write, delete, restart or authenticate.
ALLOWED_ARGV: dict[str, set[str] | None] = {
    "git":      {"log", "show", "rev-parse", "status", "diff", "ls-files",
                 "cat-file", "describe", "branch", "tag", "remote", "config"},
    "kubectl":  {"get", "describe", "top", "version", "api-resources"},
    "docker":   {"inspect", "ps", "images", "version"},
    "systemctl": {"is-active", "is-enabled", "show", "status", "list-timers"},
    "stat": None, "ls": None, "wc": None, "head": None, "tail": None,
    "readlink": None, "date": None, "hostname": None, "uname": None,
    "df": None, "nproc": None, "sha256sum": None, "md5sum": None,
    "crontab": {"-l"},
}
# ⛔ Flags that turn a read-only subcommand into a writer or an executor.
# `git -c alias.x='!sh -c ...' x` RUNS SHELL, so `-c` is refused outright even
# though it looks like harmless configuration; `--output` writes a file;
# `--upload-pack`/`--receive-pack`/`--exec-path` name a program to execute.
ARGV_FORBIDDEN_FLAGS = {"--unset", "--replace-all", "--add", "--edit", "-e",
                        "-c", "--config-env", "--exec-path", "--output",
                        "--upload-pack", "--receive-pack", "-o"}

# Global options that take a separate value, so the subcommand is further right.
VALUE_FLAGS = {"-C", "--git-dir", "--work-tree", "--namespace", "-n",
               "--context", "--kubeconfig", "-H", "--host"}


def _subcommand(argv: list[str]) -> str | None:
    """The first non-option token after the binary.

    ⚠ argv[1] IS NOT THE SUBCOMMAND. `git -C /repo log` puts it at index 3,
    and reading index 1 rejects every legitimate repo-scoped probe -- measured
    the first time this validator met a real one.
    """
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            i += 2 if tok in VALUE_FLAGS else 1
            continue
        return tok
    return None

LAN_HOSTS = {"server3", "server4", "server5", "office", "gpu1", "gpu2", "gpu3",
             "localhost", "127.0.0.1", "nas"}
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9 _@%+=:,./^~{}\[\]()#*?-]*$")


class Unsafe(ValueError):
    """The probe is rejected before anything runs."""


@dataclass
class Result:
    verdict: str                       # holds | refuted | inconclusive
    detail: str
    probe: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.verdict}: {self.detail}"


# ── safety ────────────────────────────────────────────────────────────────────
def _host_is_lan(host: str) -> bool:
    if host in LAN_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return False
    return ip.is_private or ip.is_loopback


def _check_timeout(p: dict) -> int:
    t = int(p.get("timeout") or DEFAULT_TIMEOUT)
    if not 1 <= t <= MAX_TIMEOUT:
        raise Unsafe(f"timeout {t}s outside 1..{MAX_TIMEOUT}")
    return t


def validate(p: dict) -> None:
    """Raise Unsafe unless this probe is one we are willing to run unattended."""
    kind = p.get("kind")
    if kind not in {"http", "path", "grep", "cmd"}:
        raise Unsafe(f"unknown kind {kind!r}")
    _check_timeout(p)

    if kind == "http":
        url = str(p.get("url") or "")
        m = re.match(r"^(https?)://([^/:\s]+)(?::(\d+))?(/.*)?$", url)
        if not m:
            raise Unsafe("url must be a plain http(s) URL")
        if not _host_is_lan(m.group(2)):
            raise Unsafe(f"host {m.group(2)} is not on the LAN — probes do not "
                         "reach the public internet")
        if str(p.get("method", "GET")).upper() not in {"GET", "HEAD"}:
            raise Unsafe("only GET and HEAD")

    elif kind in {"path", "grep"}:
        path = str(p.get("path") or "")
        if not path.startswith("/"):
            raise Unsafe("path must be absolute")
        if kind == "grep" and not p.get("pattern"):
            raise Unsafe("grep needs a pattern")
        host = p.get("host")
        if host is not None:
            if not _host_is_lan(str(host)):
                raise Unsafe(f"host {host!r} is not on the LAN")
            if not SAFE_TOKEN.match(path):
                raise Unsafe(f"path {path!r} is not safe to cross a remote shell")

    elif kind == "cmd":
        argv = p.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise Unsafe("argv must be a non-empty list of strings")
        binary = Path(argv[0]).name
        if binary not in ALLOWED_ARGV:
            raise Unsafe(f"{binary!r} is not an allowed read-only binary")
        sub = ALLOWED_ARGV[binary]
        if sub is not None:
            # ⚠ NOT EVERY SUBCOMMAND IS A BARE WORD. `crontab -l` names its read
            # mode with an OPTION, so looking only for the first non-option token
            # finds nothing and the probe is refused -- measured 2026-10-22, a
            # worker wanted exactly that probe and had to settle for a grep.
            # Accept either form: the first non-option token, or any allowed
            # token appearing in argv.
            got = _subcommand(argv)
            if got not in sub and not any(t in sub for t in argv[1:]):
                raise Unsafe(f"{binary} subcommand {got!r} must be one of {sorted(sub)}")
        bad = ARGV_FORBIDDEN_FLAGS.intersection(argv)
        if bad:
            raise Unsafe(f"forbidden flag(s) {sorted(bad)}")
        host = p.get("host")
        if host is not None:
            if not _host_is_lan(str(host)):
                raise Unsafe(f"host {host!r} is not on the LAN")
            # it crosses a remote shell, so the tokens must be boring
            for a in argv:
                if not SAFE_TOKEN.match(a):
                    raise Unsafe(f"token {a!r} is not safe to cross a remote shell")


# ── the verbs ─────────────────────────────────────────────────────────────────
def _judge(p: dict, body: str, extra_ok: bool = True) -> Result:
    """Apply expect_substr / expect_absent_substr to text we actually read."""
    want = p.get("expect_substr")
    if want is not None and want not in body:
        return Result("refuted", f"expected substring {want!r} absent", p)
    nope = p.get("expect_absent_substr")
    if nope is not None and nope in body:
        return Result("refuted", f"forbidden substring {nope!r} present", p)
    if not extra_ok:
        return Result("refuted", "status/rc check failed", p)
    return Result("holds", "all expectations met", p)


def _run_http(p: dict) -> Result:
    method = str(p.get("method", "GET")).upper()
    want = p.get("expect_status", [200])
    want = [want] if isinstance(want, int) else list(want)
    req = urllib.request.Request(p["url"], method=method)
    try:
        with urllib.request.urlopen(req, timeout=_check_timeout(p)) as r:
            status, body = r.status, r.read(MAX_READ).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                 # a status IS an answer
        status, body = e.code, (e.read(MAX_READ).decode("utf-8", "replace") if e.fp else "")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Result("inconclusive", f"could not reach {p['url']}: {e}", p)
    if status not in want:
        return Result("refuted", f"status {status}, expected {want}", p)
    return _judge(p, body)


def _ssh(host: str, argv: list, p: dict):
    """Run a read-only argv on `host`. Returns (CompletedProcess, None) or
    (None, reason) where a reason means INCONCLUSIVE -- the probe did not run.

    ⛔ EVERY FAILURE HERE IS `inconclusive`, NEVER `refuted`. An unreachable
       host, a timeout and a refused key all mean the claim WAS NOT TESTED, and
       the one thing this lane must never do is publish a disproof it did not
       earn: `refute-recheck` queues refuted ids and `route.py` DMs their pin
       holders "a memo your pin cites has been DISPROVED."
    """
    for a in argv:
        if not SAFE_TOKEN.match(a):
            return None, f"token {a!r} is not safe to cross a remote shell"
    cmd = ["ssh", "-p", "4999", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           f"ben@{host}", "--", " ".join(shlex.quote(a) for a in argv)]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=_check_timeout(p))          # never shell=True
    except subprocess.TimeoutExpired:
        return None, (f"ssh to {host} timed out after {_check_timeout(p)}s — "
                      "a timeout is not a zero")
    except (OSError, ValueError) as e:
        return None, f"could not ssh to {host}: {e}"
    if cp.returncode == 255:
        return None, (f"ssh to {host} failed (rc 255) — host not searched, "
                      "not a clean result")
    return cp, None


def _run_path(p: dict) -> Result:
    want_exists = str(p.get("expect", "exists")) == "exists"
    host = p.get("host")
    if host:
        # ⛔⛔ `host` WAS ACCEPTED, VALIDATED AND THEN SILENTLY IGNORED HERE
        #    until 2026-10-26. The probe was stat'ed on whatever box the runner
        #    happened to be on -- server4 -- and the verdict published under
        #    the authority of a host it never contacted. MEASURED: 1 probe in
        #    842 did this, `6c548a84` (server6's backup dir). Its `refuted`
        #    happened to match what server6 really holds, which is exactly why
        #    nothing caught it: a method that cannot be trusted still agrees
        #    with reality most of the time, and only the disagreements bill.
        cp, why = _ssh(host, ["test", "-e", p["path"]], p)
        if why is not None:
            return Result("inconclusive", why, p)
        if cp.returncode not in (0, 1):
            return Result("inconclusive",
                          f"test -e on {host} returned rc {cp.returncode}, which "
                          f"is neither present nor absent: {cp.stderr.strip()[:120]}", p)
        here = cp.returncode == 0
        where = f" on {host}"
    else:
        where = ""
        try:
            here = Path(p["path"]).exists()
        except OSError as e:                             # an NFS stall, typically
            return Result("inconclusive", f"stat failed: {e}", p)
    if here is not want_exists:
        return Result("refuted",
                      f"{p['path']}{where} {'exists' if here else 'is absent'}, "
                      f"expected {'exists' if want_exists else 'absent'}", p)
    return Result("holds", f"{p['path']}{where} as expected", p)


def _run_grep(p: dict) -> Result:
    path = Path(p["path"])
    host = p.get("host")
    want = str(p.get("expect", "present")) == "present"

    # ⛔⛔ A MISSING FILE DOES NOT REFUTE AN `expect: absent` PROBE, AND THIS
    #    FUNCTION SAID IT DID until 2026-10-26 -- it returned `refuted` from
    #    the FileNotFoundError branch, above and before `expect` was ever read.
    #    So a probe asserting "pattern X is NOT in file Y" reported X as FOUND
    #    in a file that does not exist. ⚠️ IT FAILS IN THE DIRECTION THAT
    #    MANUFACTURES DRIFT, and it is LATENT, not quiet: MEASURED 2026-10-26,
    #    43 grep probes carry `expect: absent` and 0 of their files were
    #    missing, so the lane has never once exercised this branch. It fires
    #    the day any of those 43 paths is renamed, deleted or moved to another
    #    host -- precisely the day the memo most deserves a careful look.
    # ⭐ For `expect: present` a missing file IS a refutation: the cited
    #    evidence is gone. The two cases differ and must not share a branch.
    def _missing() -> Result:
        if want:
            return Result("refuted",
                          f"{path} does not exist, so the pattern it cited is gone", p)
        return Result("inconclusive",
                      f"{path} does not exist, so an `absent` claim about its "
                      "contents cannot be tested — this is not a refutation", p)

    if host:
        cp, why = _ssh(host, ["cat", "--", str(path)], p)
        if why is not None:
            return Result("inconclusive", why, p)
        if cp.returncode != 0:
            err = cp.stderr.strip()
            if "No such file" in err:
                return _missing()
            return Result("inconclusive",
                          f"could not read {path} on {host}: {err[:120]}", p)
        body = cp.stdout[:MAX_READ]
    else:
        try:
            body = path.read_text("utf-8", "replace")[:MAX_READ]
        except FileNotFoundError:
            return _missing()
        except OSError as e:
            return Result("inconclusive", f"could not read {path}: {e}", p)
    # ⛔⛔ re.M IS LOAD-BEARING AND ITS ABSENCE FAILED IN THE DIRECTION THAT
    #    MANUFACTURES DRIFT. Without it `^` matches only at the START OF THE
    #    WHOLE FILE, so an anchored probe refutes any memo whose evidence is not
    #    on line 1 -- and "pattern absent" is byte-identical to a claim that has
    #    genuinely rotted. MEASURED 2026-10-25 on the 05:47Z run: of 48
    #    refutations, 17 probes were anchored and 14 of those lacked an inline
    #    `(?m)`; re-running those 14 with re.M flipped 11 to `holds`. 23% of the
    #    lane's output was false, and `servers` and `nginx` each disputed one the
    #    same morning -- correctly, both times.
    # ⭐ THE FIX BELONGS IN THE RUNNER, NOT THE PROBES. 18 of the 39 anchored
    #    patterns already carried `(?m)` because some authoring worker thought to
    #    add it; the other 17 did not. Patching patterns repairs the ones written
    #    so far and nothing about the next author's discipline.
    # ⚠️ IT CANNOT CREATE A FALSE REFUTATION. re.M only ADDS match positions,
    #    so for `expect: present` it can only turn refuted -> holds; it moves
    #    `expect: absent` the other way, but all 8 anchored absent-probes already
    #    carry `(?m)` and so are unaffected. Unanchored patterns cannot change.
    try:
        found = re.search(p["pattern"], body, re.M) is not None
    except re.error as e:
        return Result("inconclusive", f"bad pattern: {e}", p)
    where = f"{path}{' on ' + host if host else ''}"
    if found is not want:
        return Result("refuted",
                      f"pattern {'found' if found else 'absent'} in {where}, "
                      f"expected {'present' if want else 'absent'}", p)
    return Result("holds", f"pattern as expected in {where}", p)


def _run_cmd(p: dict) -> Result:
    argv, host = list(p["argv"]), p.get("host")
    if host:
        argv = ["ssh", "-p", "4999", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                f"ben@{host}", "--", " ".join(shlex.quote(a) for a in argv)]
    try:
        cp = subprocess.run(argv, capture_output=True, text=True,
                            timeout=_check_timeout(p))          # never shell=True
    except subprocess.TimeoutExpired:
        return Result("inconclusive", f"timed out after {_check_timeout(p)}s — "
                                      "a timeout is not a zero", p)
    except (OSError, ValueError) as e:
        return Result("inconclusive", f"could not run: {e}", p)
    if host and cp.returncode == 255:
        return Result("inconclusive", f"ssh to {host} failed (rc 255) — host not "
                                      "searched, not a clean result", p)
    want_rc = p.get("expect_rc", 0)
    body = cp.stdout + cp.stderr
    if want_rc is not None and cp.returncode != want_rc:
        return Result("refuted", f"rc {cp.returncode}, expected {want_rc}: "
                                 f"{body.strip()[:200]}", p)
    return _judge(p, body)


def refuted_ids() -> set[str]:
    """Memos the free nightly recheck disproved since the last run.

    ⚠️ ABSENT IS NOT EMPTY. No queue file means the recheck has never run, not
    that every probe holds -- so this returns an empty set either way and the
    CALLER must not read a zero here as evidence of corpus health.

    ⭐ IT LIVES HERE, NOT IN A SELECTOR, BECAUSE TWO SELECTORS NEED IT AND THIS
       REPO HAS ALREADY PAID FOR TWO IMPLEMENTATIONS OF ONE JOB -- memo-minder
       enumerated sessions with shell `find` while transcript-passages used
       Python `glob`, and the `find` path silently returned 0 on the busiest
       host for weeks. One definition, imported twice.
    """
    q = Path(__file__).resolve().parent / "refute-queue.json"
    try:
        return set(json.loads(q.read_text()).get("ids") or [])
    except (OSError, ValueError):
        return set()


RUNNERS = {"http": _run_http, "path": _run_path, "grep": _run_grep, "cmd": _run_cmd}


def run(p: dict) -> Result:
    try:
        validate(p)
    except Unsafe as e:
        return Result("inconclusive", f"REJECTED before running: {e}", p)
    return RUNNERS[p["kind"]](p)


def extract(content: str) -> list[dict]:
    """Every well-formed probe embedded in a memo's content."""
    out = []
    for m in PROBE_RE.finditer(content or ""):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue                    # a malformed probe is not a probe
    return out


def render(p: dict) -> str:
    return "<!-- refute v1 " + json.dumps(p, separators=(",", ":"), sort_keys=True) + " -->"


# ── storing a probe on a memo ─────────────────────────────────────────────────
API = "http://server4:8000"


def fetch(doc_id: str, api: str = API) -> dict:
    with urllib.request.urlopen(f"{api}/documents/{doc_id}", timeout=60) as r:
        return json.loads(r.read().decode())


def store(doc_id: str, probe: dict, *, authored_by: str = "memo-audit",
          basis: str = "measured", note: str = "", api: str = API) -> dict:
    """Attach a probe to a memo's `metadata`, keeping every other key.

    ⛔⛔ `metadata` ON PATCH IS A FULL REPLACE, NOT A MERGE. Measured 2026-10-22
    against the live service on canary f0c6aa5b: PATCH `{"metadata":{"x":true}}`
    left the document holding exactly `{"x": true}` and silently dropped the
    keys that were there, provenance included -- `db.py:545` takes the dict
    whole whenever it is not None. Same family as `content=`. ⇒ READ, MERGE,
    WRITE. Never hand this endpoint a partial metadata dict.
    """
    validate(probe)                 # refuse to store what we would refuse to run
    doc = fetch(doc_id, api)
    meta = dict(doc.get("metadata") or {})          # <- the merge that matters
    meta["refute"] = {"probe": probe, "authored_by": authored_by,
                      "authored_at": __import__("datetime").date.today().isoformat(),
                      "basis": basis, **({"note": note} if note else {})}
    req = urllib.request.Request(
        f"{api}/documents/{doc_id}", method="PATCH",
        data=json.dumps({"metadata": meta}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    back = fetch(doc_id, api).get("metadata") or {}
    # ⚠ read back FIELD BY FIELD -- a 200 is not a write on this service
    if (back.get("refute") or {}).get("probe") != probe:
        raise RuntimeError(f"probe did not land on {doc_id}: {back.get('refute')}")
    lost = set(meta) - set(back)
    if lost:
        raise RuntimeError(f"metadata keys lost on {doc_id}: {sorted(lost)}")
    return back["refute"]


def probe_of(doc: dict) -> dict | None:
    """The probe stored on a memo, from metadata or (legacy) from its content."""
    p = ((doc.get("metadata") or {}).get("refute") or {}).get("probe")
    if p:
        return p
    found = extract(doc.get("content") or "")
    return found[0] if found else None


# ── controls ──────────────────────────────────────────────────────────────────
def self_test() -> int:
    """⛔ A DETECTOR NEVER RUN AGAINST A KNOWN POSITIVE IS NOT A DETECTOR.

    Each case asserts the verdict for a probe whose answer is known from here.
    """
    me = Path(__file__).resolve()
    cases: list[tuple[str, dict, str]] = [
        ("file that exists",      {"kind": "path", "path": str(me)}, "holds"),
        ("file that does not",    {"kind": "path", "path": "/nonexistent-aa9d2ec0"}, "refuted"),
        ("absence asserted true", {"kind": "path", "path": "/nonexistent-aa9d2ec0",
                                   "expect": "absent"}, "holds"),
        ("grep hit",              {"kind": "grep", "path": str(me),
                                   "pattern": r"A DETECTOR NEVER RUN"}, "holds"),
        # ⛔ the pattern is ASSEMBLED, never written literally: the first
        # version of this control spelled its "absent" string into this very
        # file, so grepping the file FOUND it and the control passed as `holds`.
        # A negative control whose needle lives in the haystack tests nothing.
        ("grep miss",             {"kind": "grep", "path": str(me),
                                   "pattern": "zz" + "q-absent-" + "needle"}, "refuted"),
        ("grep on missing file",  {"kind": "grep", "path": "/nonexistent-aa9d2ec0",
                                   "pattern": "x"}, "refuted"),
        # ⛔ THE CONTROL THIS LANE DID NOT HAVE UNTIL 2026-10-25. `^import
        #    subprocess` sits well past line 1 of this file, so it matches ONLY
        #    under re.M. Without the flag this case returns `refuted` and the
        #    11 false refutations above come straight back. A bare `^` anchored
        #    on line 1 would pass either way and prove nothing.
        ("grep anchored, not L1",  {"kind": "grep", "path": str(me),
                                   "pattern": r"^import subprocess$"}, "holds"),
        ("cmd rc 0",              {"kind": "cmd", "argv": ["hostname"]}, "holds"),
        ("cmd substring",         {"kind": "cmd", "argv": ["hostname"],
                                   "expect_substr": "zzz"}, "refuted"),
        ("unreachable host",      {"kind": "http", "url": "http://127.0.0.1:1/x"}, "inconclusive"),
        ("public URL refused",    {"kind": "http", "url": "https://example.com/"}, "inconclusive"),
        ("writer binary refused", {"kind": "cmd", "argv": ["rm", "-rf", "/tmp/x"]}, "inconclusive"),
        ("git write refused",     {"kind": "cmd", "argv": ["git", "push"]}, "inconclusive"),
        ("git -C log accepted",   {"kind": "cmd", "argv":
                                   ["git", "-C", "/mnt/nas/data/code/memo", "log",
                                    "-1", "--format=%s"]}, "holds"),
        ("crontab -l accepted",   {"kind": "cmd", "argv": ["crontab", "-l"],
                                   "expect_substr": "cronguard"}, "holds"),
        ("crontab write refused", {"kind": "cmd", "argv": ["crontab", "/tmp/x"]},
                                  "inconclusive"),
        ("git -c alias refused",  {"kind": "cmd", "argv":
                                   ["git", "-c", "alias.z=!id", "-C", "/tmp", "z"]},
                                  "inconclusive"),
        ("shell metachars",       {"kind": "cmd", "argv": ["ls", "; rm -rf /"],
                                   "host": "server4"}, "inconclusive"),
        ("absurd timeout",        {"kind": "path", "path": "/", "timeout": 99999}, "inconclusive"),

        # ⛔ THE CONTROLS THIS LANE DID NOT HAVE UNTIL 2026-10-26.
        # ⭐ BOTH REMOTE CASES DISCRIMINATE, which is the only reason they are
        #    worth their ssh. `/mnt/fast4` is server4-local (verified absent on
        #    server3 and server5, 2026-10-26), so a runner that IGNORES `host`
        #    stats it here, finds it, and answers `holds` -- failing the
        #    control. A runner that honours `host` reaches server3 and answers
        #    `refuted`. A control that passes either way proves nothing; the
        #    canary that covered one verb of this runner and read as proof of
        #    the runner is the mistake these are written against.
        # ⚠️ `inconclusive` is ACCEPTED here so that a genuinely unreachable
        #    server3 does not fail the gate and skip the whole recheck lane.
        #    It is NOT a loophole: the unfixed runner returns `holds`, which is
        #    in neither set.
        ("path host honoured",    {"kind": "path", "path": "/mnt/fast4",
                                   "host": "server3"}, ("refuted", "inconclusive")),
        ("grep host honoured",    {"kind": "grep", "host": "server3",
                                   "path": "/mnt/fast4/nginx/conf/site:agent.pushbuild.com",
                                   "pattern": "server_name"}, ("refuted", "inconclusive")),
        ("path host not on LAN",  {"kind": "path", "path": "/", "host": "example.com"},
                                  "inconclusive"),
        # ⛔⛔ THE ONE THAT MATTERS. A missing file cannot show that a pattern
        #    is ABSENT from it, and this branch returned `refuted` -- reporting
        #    the forbidden pattern as FOUND in a file that does not exist. 43
        #    probes carry `expect: absent`; none has yet hit a missing file, so
        #    the lane has never exercised it. Without the fix this case returns
        #    `refuted` and the control fails.
        ("grep absent, no file",  {"kind": "grep", "path": "/nonexistent-aa9d2ec0",
                                   "pattern": "x", "expect": "absent"}, "inconclusive"),
    ]
    bad = 0
    for name, probe, want in cases:
        # ⚠️ `want` may be a TUPLE of acceptable verdicts -- used only where an
        #    unreachable LAN host would otherwise fail the gate and skip the
        #    recheck. Never widen one to make a failing control pass.
        wants = (want,) if isinstance(want, str) else tuple(want)
        got = run(probe)
        ok = got.verdict in wants
        bad += not ok
        label = want if isinstance(want, str) else "|".join(wants)
        print(f"  {'✅' if ok else '⛔'} {name:24s} want={label:21s} got={got.verdict:13s} {got.detail[:70]}")
    print(f"\n{len(cases) - bad}/{len(cases)} controls pass")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--self-test":
        return self_test()
    if len(argv) > 1 and argv[1] == "--store":
        doc_id, probe = argv[2], json.loads(argv[3])
        try:
            rec = store(doc_id, probe)
        except (Unsafe, RuntimeError, urllib.error.URLError, OSError) as e:
            print(f"⛔ not stored: {e}")
            return 3
        print(json.dumps(rec, indent=2))
        return 0
    if len(argv) > 1 and argv[1] == "--validate":
        try:
            validate(json.loads(argv[2]))
        except (Unsafe, ValueError) as e:
            print(f"⛔ rejected: {e}")
            return 3
        print("✅ probe is safe to store and run")
        return 0
    if len(argv) > 1 and argv[1] == "--run":
        probe = json.loads(argv[2]) if len(argv) > 2 else json.load(sys.stdin)
        r = run(probe)
        print(json.dumps({"verdict": r.verdict, "detail": r.detail}, indent=2))
        return {"holds": 0, "refuted": 1, "inconclusive": 2}[r.verdict]
    print(__doc__)
    print("usage: refute.py --self-test\n"
          "       refute.py --validate '<probe json>'\n"
          "       refute.py --run      '<probe json>'\n"
          "       refute.py --store    <memo-id> '<probe json>'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
