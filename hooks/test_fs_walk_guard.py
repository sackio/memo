#!/usr/bin/env python3
"""Controls for hooks/fs-walk-guard.py.

⭐ THE FIRST TWO CASES ARE THE REAL INCIDENT ARGV, recovered from ps output on
2026-08-21. They are load-bearing: the first version of the guard passed every
synthetic case in this file and ALLOWED BOTH OF THEM, because it read `dfs` out
of `bfs -S dfs / ...` as the search root. A detector never run against a known
positive is not a detector.

Run: python3 hooks/test_fs_walk_guard.py
"""
import json, subprocess, sys
H = "/mnt/nas/data/code/memo/hooks/fs-walk-guard.py"

def run(payload):
    p = subprocess.run(["python3", H], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20)
    out = p.stdout.strip()
    if not out:
        return "ALLOW", ""
    try:
        d = json.loads(out)
    except Exception:
        return "ALLOW(unparsed)", out[:80]
    dec = d.get("hookSpecificOutput", {}).get("permissionDecision", "?")
    return dec.upper(), d.get("reason", "").splitlines()[2] if d.get("reason") else ""

def bash(c, cwd="/mnt/nas/data/code/memo"):
    return {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": cwd}

# ── THE TWO REAL ONES. These are the incident. If either ALLOWs, the guard is
#    not a guard — it is a decoration.
POSITIVE = [
  ("REAL#1 bfs /", bash("bfs -S dfs -regextype findutils-default / -path /proc -prune -o -type d -iname '*-mnt-nas-data-code-agentkit*' -print")),
  ("REAL#2 bfs /mnt/nas", bash("bfs -S dfs -regextype findutils-default /mnt/nas -iname '*agent-a97*'")),
  ("find / unbounded", bash("find / -name '*.jsonl' 2>/dev/null")),
  ("find -L /mnt/nas", bash("/usr/bin/find -L /mnt/nas -type f -name x")),
  ("find $HOME unbounded", bash("find ~ -name '*.log'")),
  ("find /mnt/nas/data/code", bash("find /mnt/nas/data/code -name '*.py'")),
  ("rg at /mnt/nas", bash("rg 'secret' /mnt/nas")),
  ("grep -rn /home/ben", bash("grep -rn foo /home/ben")),
  ("piped after a safe cmd", bash("echo hi; find /mnt/nas -name z")),
  ("Glob tool at /", {"tool_name":"Glob","tool_input":{"pattern":"**/*agentkit*","path":"/"}}),
  ("Grep tool at /mnt/nas", {"tool_name":"Grep","tool_input":{"pattern":"x","path":"/mnt/nas"}}),
  ("Glob abs pattern", {"tool_name":"Glob","tool_input":{"pattern":"/mnt/nas/**/*.jsonl"}}),
  # ⭐ REAL#4 (agents, 2026-08-21 22:08). Cross-host work on this fleet is nearly
  #    always `ssh <host> 'bash -s' <<REMOTE`, so heredoc-stripping -- right for a
  #    commit message -- made the payload invisible exactly when it IS the command.
  #    Their orphaned zticker walk went through this path.
  ("REAL#4 ssh + bash -s heredoc",
   bash("ssh ben@server5 'bash -s' <<'REMOTE'\n"
        "find /mnt/nas/data/quantum/zticker -type f -newermt -20 minutes\nREMOTE")),
  ("ssh + quoted command", bash('ssh ben@server5 "find /mnt/nas -name x"')),
  ("ssh -p with opts", bash("ssh -o ConnectTimeout=6 -p 4999 ben@office 'rg pat /mnt/nas/data/code'")),
  ("bash -c wrapping a walk", bash('bash -c "find /mnt/nas -name y"')),

  # ⭐ REAL #3, 2026-08-21 evening (agents). The guard MISSED this one live: the
  #    root is "." and relative walks were exempt, on the assumption that a cwd is
  #    local. It ran 15 min over NFS on a host at 95% io pressure.
  ("REAL#3 ugrep -rn . on NAS cwd",
   bash("ugrep -G --ignore-files --hidden -I --exclude-dir=.git -rn embport-prompt --include=* .",
        cwd="/mnt/nas/data/code/quantum-feed")),
  ("rg . from NAS cwd", bash("rg embport .", cwd="/mnt/nas/data/code/quantum-feed")),
  ("rg with no root from NAS cwd", bash("rg embport", cwd="/mnt/nas/data/code/quantum-feed")),
  ("find . from NAS cwd", bash("find . -name '*.py'", cwd="/mnt/nas/data/code/quantum-feed")),
  ("relative subdir on NAS", bash("grep -rn foo hooks/", cwd="/mnt/nas/data/code/memo")),
  ("abs repo subtree on NAS", bash("find /mnt/nas/data/code/memo/src -type f")),
  ("cd onto NAS then walk", bash("cd /mnt/nas/data/code/ha && find . -name '*.yaml'", cwd="/tmp")),
]

# ── NEGATIVE CONTROLS: real commands from today's work. A guard that blocks
#    these is a guard nobody will keep.
NEGATIVE = [
  ("bounded find, agents' 12:20", bash("find /mnt/nas/data/code -maxdepth 6 -name x")),
  ("memo-minder A.1", bash("/usr/bin/find -L /home/ben/.claude/projects -maxdepth 2 -name '*.jsonl' -newer /tmp/bf_cutoff -printf '%T@ %s %p\\n'")),
  ("my own scan today", bash("cd /home/ben/.claude/projects && timeout 60 /usr/bin/find -L . -maxdepth 3 -name '*.jsonl'")),
  ("relative find from /tmp", bash("find . -name '*.md'", cwd="/tmp/scratch")),
  ("rg from a LOCAL checkout", bash("rg TODO .", cwd="/home/ben/local/proj")),
  ("tmp walk", bash("find /tmp/claude-1000 -name '*.ics'")),
  ("grep non-recursive on /", bash("grep foo /etc/hosts")),
  ("bounded rg on NAS", bash("rg --max-depth 2 'def embed' /mnt/nas/data/code/memo-v2/src")),
  ("cd to /tmp then walk", bash("cd /tmp && find . -name '*.log'", cwd="/mnt/nas/data/code/memo")),
  ("git ls-files, the fix", bash("git ls-files | grep embport", cwd="/mnt/nas/data/code/quantum-feed")),
  ("git grep, the fix", bash("git grep -n embport", cwd="/mnt/nas/data/code/quantum-feed")),
  ("no cwd in payload", {"tool_name":"Bash","tool_input":{"command":"rg foo ."}}),
  ("git log", bash("git -C /mnt/nas/data/code/memo log --oneline")),
  # ⭐ The harness's own Glob/Grep keep the NARROW rule -- see is_dangerous().
  ("Glob in a NAS project", {"tool_name":"Glob","tool_input":{"pattern":"**/*.py","path":"/mnt/nas/data/code/memo"}}),
  ("Grep in a NAS project", {"tool_name":"Grep","tool_input":{"pattern":"x","path":"/mnt/nas/data/code/atc/server"}}),
  ("Glob no path", {"tool_name":"Glob","tool_input":{"pattern":"**/*.py"}}),
  ("unparseable quotes", bash("find /mnt/nas -name \"unbalanced")),
  # ⭐ CAUGHT BY THE GUARD BLOCKING ITS OWN COMMIT, 2026-08-21. A heredoc body is
  #    DATA. The quotes balance, so shlex parses the prose as commands and anyone
  #    documenting a walk gets denied for describing one.
  ("heredoc quoting a walk",
   bash("cat > /tmp/msg.txt <<'EOF'\nfix: ugrep -rn pat . ran 15 min over NFS\n"
        "also find /mnt/nas -name x\nEOF\ngit commit -F /tmp/msg.txt")),

  ("heredoc then a REAL walk after it",
   bash("cat > /tmp/a <<'EOF'\nharmless prose\nEOF\necho done")),
]
NEGATIVE += [
  ("heredoc prose only", bash("cat > /tmp/a <<'EOF'\nharmless prose\nEOF\necho done")),
  ("unknown tool", {"tool_name":"Write","tool_input":{"file_path":"/mnt/nas/x"}}),
  ("malformed input", {"tool_name":"Bash","tool_input":"not-a-dict"}),
]

# ⚠️ The far side's cwd is UNKNOWN, so a RELATIVE root inside an ssh payload must
#    keep failing open -- guessing it against the local cwd would deny remote work
#    on evidence we do not have.
NEGATIVE += [
  ("ssh + RELATIVE root (cwd unknown)", bash("ssh ben@server5 'find . -name x'")),
  ("ssh + bounded remote walk", bash('ssh ben@server5 "find /mnt/nas -maxdepth 3 -name x"')),
  ("ssh probe, no walk", bash("ssh -p 4999 ben@office \"curl -sS -o /dev/null -w 'code=%{http_code}' http://server4:8000/health\"")),
]

fails = 0
print("=== POSITIVE CONTROLS (must DENY) ===")
for name, p in POSITIVE:
    d, why = run(p)
    ok = d == "DENY"
    fails += 0 if ok else 1
    print(f"  {'PASS' if ok else '** FAIL **':10} {name:32} -> {d}  {why[:70]}")
print("=== NEGATIVE CONTROLS (must ALLOW) ===")
for name, p in NEGATIVE:
    d, why = run(p)
    ok = d.startswith("ALLOW")
    fails += 0 if ok else 1
    print(f"  {'PASS' if ok else '** FAIL **':10} {name:32} -> {d}  {why[:70]}")
print(f"\n{'ALL PASS' if not fails else str(fails)+' FAILURES'}")
sys.exit(1 if fails else 0)
